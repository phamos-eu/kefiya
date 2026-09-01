# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt
from __future__ import unicode_literals

import frappe
import json

# Every user-facing message in this module goes through _(). It was never
# imported here: the transfer endpoints are the only callers and had not run in
# production yet, so the resulting NameError stayed latent.
from frappe import _

from kefiya.utils import account_classification, account_kind


def _use_tan_authentication() -> bool:
    """Helper: check Kefiya Settings for TAN toggle."""
    return bool(
        frappe.db.get_single_value("Kefiya Settings", "enable_tan_authentication")
    )


def _fetch_session():
    """A shared FinTS dialog -- but only where every caller knows about it.

    With the TAN toggle off, the transaction import runs through the legacy
    controller, which knows nothing about sessions: it builds its own client
    and opens its own dialog in the constructor. The best-effort extras always
    use the new controller and would leave the session's dialog standing, so
    the legacy import would open a SECOND dialog on the same bank access --
    exactly the double dialog get_fetch_groups exists to avoid, and the one
    banks answer by tearing the connection down.

    So the session is only entered when the whole fetch goes through the
    session-aware controller. In legacy mode every command opens and closes its
    own dialog, as it did before any of this existed.
    """
    import contextlib

    from kefiya.utils.fints_controller import fints_session

    if not _use_tan_authentication():
        return contextlib.nullcontext()
    return fints_session()


def _get_fints_controller():
    """Return the appropriate FinTSController class depending on TAN toggle."""
    if _use_tan_authentication():
        from kefiya.utils.fints_controller import FinTSController
    else:
        from kefiya.utils.fints_controller_legacy import FinTSController
    return FinTSController


@frappe.whitelist()
def import_fints_transactions(kefiya_import, kefiya_login, user_scope):
    """Create payment entries by FinTS transactions.

    :param kefiya_import: kefiya_import doc name
    :param kefiya_login: kefiya_login doc name
    :param user_scope: Current open doctype page
    :type kefiya_import: str
    :type kefiya_login: str
    :type user_scopet: str
    :return: List of max 10 transactions and all new payment entries
    """
    # Permission gate: this contacts the bank and writes Bank Transactions,
    # so it needs write rights on the login -- a whitelisted endpoint is
    # otherwise callable by any logged-in user.
    frappe.has_permission("Kefiya Login", ptype="write",
                          doc=kefiya_login, throw=True)
    FinTSController = _get_fints_controller()
    interactive = {"docname": user_scope, "enabled": True}

    return FinTSController(kefiya_login, interactive) \
        .import_fints_transactions(kefiya_import)


@frappe.whitelist()
def import_fints_holdings(kefiya_login, user_scope):
    """Fetch securities holdings (Depot) for a login and store a snapshot.

    :param kefiya_login: kefiya_login doc name
    :param user_scope: current open doctype page (for progress/TAN UI)
    :return: dict with created/updated counts
    """
    frappe.has_permission("Kefiya Login", ptype="write",
                          doc=kefiya_login, throw=True)
    from kefiya.utils.securities import refresh_holdings

    FinTSController = _get_fints_controller()
    interactive = {"docname": user_scope, "enabled": True}

    controller = FinTSController(kefiya_login, interactive)
    holdings = controller.get_fints_holdings()
    return refresh_holdings(kefiya_login, holdings)


@frappe.whitelist()
def get_fetch_groups():
    """Group the logins into sets that may be fetched in parallel.

    A collective fetch of 54 logins runs for roughly twelve minutes because it
    is strictly sequential -- one FinTS dialog after the other. Most of that
    time is dialog setup, not data.

    Parallelising *within* one bank access is not safe: FinTS is a single-
    dialog protocol, two dialogs on the same access each trigger their own
    strong authentication and can invalidate each other's stored client state
    (which is exactly the state kefiya shares between sibling logins so that
    one TAN covers the whole bank). Different banks are independent
    counterparties, though, and can be talked to at the same time.

    So the unit of parallelism is the bank access: same BLZ + same FinTS login.
    The caller runs one sequential chain per group and the chains side by side.
    Speed-up is bounded by the largest group, not by the total -- with one bank
    holding forty of the accounts, expect roughly a third off, not a tenth of
    the time.

    Logins excluded from fetching, or without an account IBAN, are left out
    entirely: they would only add failures.

    :return: list of groups, each {"key", "logins": [name, ...]}; no
        credentials are exposed -- the key is a hash, not the login.
    """
    import hashlib

    # Permission gate: frappe.get_list applies the Kefiya Login read
    # permissions and User Permissions, so a caller only ever sees the accesses
    # they may read. No separate has_permission check is needed -- and none
    # would be correct, since a user with partial access should get their part
    # rather than an error.
    rows = frappe.get_list(
        "Kefiya Login",
        fields=["name", "blz", "fints_login", "account_iban", "skip_fetch"],
        limit_page_length=0,
    )

    groups = {}
    for row in rows:
        if row.get("skip_fetch") or not row.get("account_iban"):
            continue
        # The BLZ alone would merge two different accesses at the same bank,
        # and the FinTS login is a credential -- hash the pair instead of
        # handing it to the browser.
        raw = "{0}|{1}".format(row.get("blz") or "", row.get("fints_login") or "")
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        groups.setdefault(key, []).append(row["name"])

    # Longest group first: the caller starts it first, so the run is not held
    # up at the end by the one chain that still has forty accounts to go.
    return [
        {"key": key, "logins": logins}
        for key, logins in sorted(
            groups.items(), key=lambda item: -len(item[1]))
    ]


@frappe.whitelist()
def start_fetch_group(logins, user_scope=None):
    """Hand one bank access to a worker and return before it has finished.

    fetch_group talks to the bank for as long as the access has accounts --
    thirty of them on this instance, minutes of it. Called straight from the
    browser that is one HTTP request held open for the whole run, which is a
    gateway timeout waiting to happen and a progress bar that cannot move.

    So the browser starts it and listens instead. The worker publishes one
    event per account as it lands, and a closing one for the group; the run
    panel fills in exactly as it did when it drove the accounts itself.

    The TAN prompt survives the move: frappe.enqueue carries the enqueueing
    user into the job, and the prompt is published to that user, so it reaches
    the same browser.

    :param logins: list of Kefiya Login names, or its JSON form
    :return: {"run": id, "logins": [...]} -- the id every event carries
    """
    if isinstance(logins, str):
        logins = frappe.parse_json(logins)
    if not isinstance(logins, (list, tuple)):
        frappe.throw(_("fetch_group expects a list of logins."))

    ordered = list(dict.fromkeys(str(name) for name in logins if name))
    if not ordered:
        return {"run": None, "logins": []}

    # Same gate as the fetch itself, applied before anything is queued: a
    # caller without write rights must be refused here rather than in a worker
    # whose failure they would never see.
    for name in ordered:
        frappe.has_permission("Kefiya Login", ptype="write", doc=name,
                              throw=True)

    run = frappe.generate_hash(length=12)
    frappe.enqueue(
        "kefiya.utils.client.fetch_group",
        queue="long",
        # An access with thirty accounts and a bank in no hurry. The default
        # 300s killed the run somewhere in the middle, leaving the accounts
        # behind it looking like failures.
        timeout=3600,
        logins=ordered,
        user_scope=user_scope,
        run=run,
    )
    return {"run": run, "logins": ordered}


@frappe.whitelist()
def fetch_group(logins, user_scope=None, run=None):
    """Fetch every login of ONE bank access through a single FinTS dialog.

    The counterpart to get_fetch_groups(): that one says which logins may be
    fetched together, this one does it. All logins of a group share one client
    and one open dialog, so the bank handshake -- several round trips plus
    authentication -- is paid once for the whole access instead of once per
    account.

    A failure of one login does not stop the rest: each is reported on its own,
    the same way the sequential run reports it. That matters more here than in
    a single fetch, because giving up would abandon thirty accounts over one.

    Not a replacement for running the groups side by side: this makes each
    chain shorter, the caller still runs the chains in parallel.

    The access holds one dialog, so a release the bank parks mid-run stops the
    group rather than being overwritten by the next account: see _park_rest.

    :param logins: list of Kefiya Login names, or its JSON form
    :param run: id of a run started by start_fetch_group. Set means somebody is
        listening; the per-account events are published as they land. Absent
        means a synchronous caller wants the return value and nothing else.
    :return: {"results": {login: summary}, "failed": {login: message}}
    """
    if isinstance(logins, str):
        logins = frappe.parse_json(logins)
    if not isinstance(logins, (list, tuple)):
        frappe.throw(_("fetch_group expects a list of logins."))

    # Deduplicate while keeping the order: the same login twice would fetch and
    # import it twice.
    ordered = list(dict.fromkeys(str(name) for name in logins if name))

    # A worker inherits the user but not their language: frappe.local.lang
    # stays at the system default, so every _() in here answered in English
    # while the same string on the same site translates fine for a request.
    # The fetch panel read half German, half English again, and this time the
    # translation was not missing -- it was never asked for.
    _speak_the_users_language()

    results = {}
    failed = {}
    held = None

    with _fetch_session():
        for name in ordered:
            # The bank is waiting for a release on an earlier account of this
            # same access, and the access holds ONE dialog: fetching on would
            # open a second and overwrite the parked challenge, so the release
            # the user gives would belong to a dialog that no longer exists.
            if held:
                results[name] = _not_attempted(held)
                _say(run, name, results[name])
                continue
            try:
                results[name] = fetch_all(name, user_scope)
                if results[name].get("tan_required"):
                    held = name
                _say(run, name, results[name])
            except Exception as exc:
                failed[name] = str(exc)
                _say(run, name, None, str(exc))
                # Discard this login's partial work before moving on. Without
                # it the next successful login commits the failed one's leftovers
                # too -- an empty draft Kefiya Import and half-written login
                # state. The scheduler solves the same problem the same way in
                # _recover_from_failed_login; the rollback drops the Error Log
                # as well, hence the explicit commit after it.
                try:
                    frappe.db.rollback()
                except Exception:
                    pass
                frappe.log_error(
                    title="Kefiya fetch_group: {0}".format(name)[:140],
                    message=frappe.get_traceback(),
                    reference_doctype="Kefiya Login",
                    reference_name=name,
                )
                try:
                    frappe.db.commit()
                except Exception:
                    pass

    if run:
        frappe.publish_realtime(
            "kefiya_fetch_group_done",
            {"run": run, "held": held, "count": len(ordered)},
            user=frappe.session.user)

    return {"results": results, "failed": failed}


def _speak_the_users_language():
    """Set the language of the user this job was queued for.

    Never raises: a job that cannot read a preference should answer in the
    site's language, not fail a fetch that is about to talk to a bank.
    """
    try:
        lang = frappe.db.get_value("User", frappe.session.user, "language")
        if lang:
            frappe.local.lang = lang
    except Exception:
        pass


def _not_attempted(held):
    """The summary for an account the group never got to.

    Shaped like a real one so the panel needs no special case, and marked
    tan_required so the retry link picks it up with the account it is waiting
    on: those two belong to the same second attempt.
    """
    return {
        "transactions": {"status": "tan_required"},
        "tan_required": True,
        "message": _(
            "Not attempted -- {0} is waiting for a release, and this bank"
            " access holds one dialog at a time."
        ).format(held),
        "errors": [],
    }


def _say(run, login, summary, error=None):
    """Publish what just happened to one account of a running group.

    Never raises: a realtime hiccup must not take down a fetch that has already
    talked to the bank. The panel would miss a line; the bookings are safe
    either way, and the run's closing event carries the count to compare
    against.
    """
    if not run:
        return
    try:
        frappe.publish_realtime(
            "kefiya_fetch_progress",
            {"run": run, "login": login, "summary": summary, "error": error},
            user=frappe.session.user)
    except Exception:
        pass


def _claim_send_lock(lock_key, seconds=600):
    """Claim a send lock atomically, or report that someone else holds it.

    The previous shape was a read followed by a write::

        if frappe.cache().get_value(key): refuse
        ...
        frappe.cache().set_value(key, user)

    Between those two lines sits a window in which a second request -- a double
    click, or the outbox and the single send firing together -- finds the key
    free as well. Both then go on and the same transfer is paid twice, which is
    precisely what the lock exists to prevent.

    frappe.cache() is a Redis client, so SET NX EX decides and claims in one
    atomic operation. The key is built with make_key so it stays the same key
    that get_value/delete_value use.

    :return: True if the caller now holds the lock
    """
    cache = frappe.cache()
    return bool(cache.set(
        cache.make_key(lock_key), frappe.session.user, ex=seconds, nx=True))


def _created_transactions(result):
    """The bookings an import actually created.

    Both controllers answer with {"transactions": [...], "payments": [...]},
    where "payments" are the Bank Transactions that were created and
    "transactions" is the raw material they were built from. A list is accepted
    as well so a future controller returning one is counted, not miscounted.
    """
    if not result:
        return []
    if isinstance(result, dict):
        return result.get("payments") or []
    if isinstance(result, (list, tuple)):
        return list(result)
    return []


def _optional_fetch(summary, key, label, fn):
    """Run one best-effort extra fetch, telling absent from broken apart.

    A bank that does not offer a segment is not a malfunction: python-fints
    raises FinTSUnsupportedOperation ("bank supports ()"), which used to be
    logged like any other failure. One collective run produced dozens of Error
    Log entries that way -- 24 for credit cards alone -- burying the failures
    that do need attention. Unsupported segments are now recorded on the
    summary and skipped silently; everything else is still logged in full.

    :param summary: the fetch_all result dict, mutated in place
    :param key: summary key to fill
    :param label: short name used in summary["errors"] / ["unsupported"]
    :param fn: zero-argument callable performing the fetch
    :return: the fetch result, or None when skipped or failed
    """
    try:
        from fints.exceptions import FinTSUnsupportedOperation
    except Exception:
        FinTSUnsupportedOperation = ()

    try:
        return fn()
    except Exception as exc:
        if FinTSUnsupportedOperation and isinstance(
                exc, FinTSUnsupportedOperation):
            summary.setdefault("unsupported", []).append(label)
            return None
        if _is_refused_by_the_bank(exc):
            summary.setdefault("unsupported", []).append(label)
            summary.setdefault("unsupported_details", {})[label] = _(
                "the bank refuses this query for this account")
            return None
        summary["errors"].append(label)
        # The label alone says WHICH retrieval failed, never why -- the reason
        # sat in the Error Log, where nobody running a collective fetch looks.
        # The log in the browser asks for a reason per account, so one short,
        # masked line travels with the summary; the full traceback stays in the
        # Error Log as before.
        summary.setdefault("error_details", {})[label] = _short_reason(exc)
        frappe.log_error(
            title="Kefiya fetch_all: {0} failed".format(label),
            message=frappe.get_traceback(),
        )
        return None


def _is_refused_by_the_bank(exc):
    """Did the bank simply decline the request, rather than anything breaking?

    python-fints turns response code 9010 -- the bank's generic "cannot process
    this order" -- into FinTSClientError("Error during dialog initialization,
    could not fetch BPD. Please check that you passed the correct bank
    identifier..."), no matter which segment was refused. The message is about
    the wrong thing entirely: nothing is wrong with the bank identifier and the
    dialog is not broken. In one collective run this single misreading produced
    24 Error Log entries and told the user that every account had a defective
    connection, when the truth was that none of them is a securities account
    and none of them offers electronic statements.

    That the dialog survives is not an assumption: every retrieval AFTER the
    refused one answered normally in the same session.
    """
    try:
        from fints.exceptions import (
            FinTSClientError, FinTSClientPINError, FinTSSCARequiredError,
        )
    except Exception:
        return False
    # Both of these are FinTSClientError subclasses and neither is a refusal:
    # a blocked PIN or a demand for strong authentication has to stay loud.
    if isinstance(exc, (FinTSClientPINError, FinTSSCARequiredError)):
        return False
    if not isinstance(exc, FinTSClientError):
        return False
    return "could not fetch BPD" in str(exc)


def _short_reason(exc, limit=180):
    """One readable line for the UI -- without account identifiers in it.

    An exception message from the bank can quote the account it was about, and
    a fetch report is exactly the place where an IBAN must not appear in full.
    Anything that looks like an IBAN or an account number is reduced to its
    last four digits, the same rule the rest of the app follows.
    """
    import re

    text = " ".join(str(exc or "").split()) or exc.__class__.__name__

    def _mask_iban(match):
        token = match.group(0)
        return "..." + token[-4:]

    text = re.sub(r"\b[A-Z]{2}\d{2}[A-Za-z0-9]{8,26}\b", _mask_iban, text)
    text = re.sub(r"\b\d{8,}\b", _mask_iban, text)

    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


@frappe.whitelist()
def fetch_all(kefiya_login, user_scope=None):
    """Fetch everything the bank offers for one login in a single action.

    Runs the real transaction import first (the "Umsaetze"), then best-effort
    fetches balance, standing orders / scheduled debits, the statement/document
    list and credit-card transactions. Every extra fetch is wrapped so that a
    failure of one (or a bank that does not support it) never aborts the others
    or the transaction import. Standing orders are additionally fed into the
    Kefiya Planned Payment forecast table.

    A single confirmation/TAN covers the session because the login's stored
    client state is shared across the calls.

    :return: dict summary {transactions, balance, planned, statements,
        credit_card, errors}
    """
    # One dialog for the whole fetch. Without this every one of the five
    # retrievals below builds its own controller and opens its own dialog:
    # handshake, authentication, one command, HKEND -- five times, for one
    # login. Inside the session they share a client and a standing dialog,
    # and a caller that wraps several logins of the same bank access (see
    # fetch_group) pays the handshake once for all of them.
    with _fetch_session():
        from frappe.utils import now_datetime
        from kefiya.utils.import_bank_transaction import resolve_incremental_from_date

        # Permission gate: a user-triggered bank fetch that creates Bank
        # Transactions / Payment Entries must hold write rights on the login.
        frappe.has_permission("Kefiya Login", ptype="write",
                              doc=kefiya_login, throw=True)

        if frappe.db.get_value("Kefiya Login", kefiya_login, "skip_fetch"):
            return {
                "transactions": {"status": "skipped"},
                "skipped": True,
                "errors": [],
            }

        scope = user_scope or kefiya_login
        summary = {
            "transactions": None,
            "balance": None,
            "planned": None,
            "statements": None,
            "credit_card": None,
            # So the log can say what this account is, and the reader can see
            # why a guarantee or a share deposit reports no running balance.
            "account_kind": account_kind.kind_of(kefiya_login),
            # And whether the books agree with the bank about that. Only set
            # when they do not, so the log stays quiet on the normal case.
            "ledger_complaint": account_classification.ledger_complaint(
                kefiya_login),
            "errors": [],
        }

        bank_account, allowed_days = (frappe.db.get_value(
            "Kefiya Login", kefiya_login,
            ["bank_account", "allowed_sync_days_in_past"]
        ) or (None, None))

        # 1) Transactions (the primary purpose: "aktuelle Umsaetze abrufen").
        #    A failure here IS reported to the user (unlike the best-effort extras).
        kefiya_import = frappe.get_doc({
            "doctype": "Kefiya Import",
            "kefiya_login": kefiya_login,
            "from_date": resolve_incremental_from_date(bank_account, allowed_days),
            "to_date": now_datetime().date(),
        })
        kefiya_import.save()
        try:
            new_txns = import_fints_transactions(
                kefiya_import.name, kefiya_login, scope)
        except Exception as e:
            # A TAN/SCA request raises TanInteractionRequired after the interactive
            # socket event was already published, so the caller (form or cockpit)
            # can prompt for the TAN. Report it as a status instead of a raw error
            # and stop here: nothing else can be fetched until the session is
            # authenticated. Any other error is surfaced truthfully.
            try:
                from kefiya.utils.fints_controller import TanInteractionRequired
            except Exception:
                TanInteractionRequired = ()
            if TanInteractionRequired and isinstance(e, TanInteractionRequired):
                # Returning normally is what keeps the parked challenge alive -- a
                # re-raise would fail the request and roll the login's TAN state
                # back with it. The flip side is that this draft import is no
                # longer discarded by that rollback: nothing was fetched into it,
                # so remove it here instead of leaving one empty draft per attempt.
                try:
                    frappe.delete_doc(
                        "Kefiya Import", kefiya_import.name,
                        ignore_permissions=True, delete_permanently=True)
                except Exception:
                    frappe.log_error(
                        title="Kefiya: removing empty import failed",
                        message=frappe.get_traceback(),
                    )
                summary["transactions"] = {"status": "tan_required"}
                summary["tan_required"] = True
                summary["message"] = str(e)
                return summary
            raise
        # import_fints_transactions answers with a DICT --
        # {"transactions": [...], "payments": [...]} -- so len() of it counted
        # its two keys. Every account in the collective log therefore reported
        # "2 neu", whether the bank had sent nothing or eighteen bookings. The
        # bookings that were actually created are the "payments".
        created = _created_transactions(new_txns)
        summary["transactions"] = {
            "import": kefiya_import.name,
            "new_count": len(created),
        }

        # The FinTS-capability reads (balance, scheduled debits, statements, credit
        # card) live on the new FinTSController regardless of the TAN toggle.
        from kefiya.utils.fints_controller import FinTSController as FetchCtl
        from kefiya.utils.fints_controller import _to_jsonable

        from kefiya.utils import fetch_persistence

        # 2) Balance incl. credit line -> Bank Account (best effort).
        def _fetch_balance():
            rows = _to_jsonable(FetchCtl(kefiya_login).get_fints_balance())
            stored = fetch_persistence.store_balance(
                kefiya_login, rows if isinstance(rows, list) else [])
            stored["rows"] = rows
            # The same number, counted backwards over the bookings that were
            # just fetched: each of them gets the balance as it stood after it.
            # Runs here and not in store_balance because only the caller knows
            # which window the bank was asked for -- outside that window the
            # backward count has no guarantee of being gap-free.
            if stored.get("stored"):
                stored["running"] = fetch_persistence.apply_running_balance(
                    kefiya_login, stored.get("balance"),
                    balance_date=stored.get("balance_date"),
                    from_date=kefiya_import.from_date)
            return stored

        summary["balance"] = _optional_fetch(
            summary, "balance", "balance", _fetch_balance)

        # 2b) Pending entries (vorgemerkte Umsaetze) -> the forecast table.
        #     A pending entry is a payment the bank accepted but has not booked
        #     -- which is what Kefiya Planned Payment is for, and why its
        #     payment_kind already carries a "Pending" option. Keeping them
        #     there rather than as Bank Transaction drafts is also what makes
        #     the reconciliation work by itself: when the booking finally
        #     arrives, match_on_bank_transaction() removes the planned record it
        #     fulfils, so the same payment is never counted twice.
        def _fetch_pending():
            from kefiya.utils.planned_payment import (
                PENDING_KIND, normalize_pending_entries,
                refresh_planned_payments,
            )

            entries = FetchCtl(kefiya_login).get_fints_pending_transactions()
            norm = normalize_pending_entries(entries)
            result = refresh_planned_payments(
                kefiya_login, norm["items"], payment_kinds=(PENDING_KIND,))
            result["skipped"] = norm["skipped"]
            return result

        summary["pending"] = _optional_fetch(
            summary, "pending", "pending_transactions", _fetch_pending)

        # 2c) Securities holdings (Depot) -> Kefiya Security Holding.
        def _fetch_holdings():
            from kefiya.utils.securities import refresh_holdings

            holdings = FetchCtl(kefiya_login).get_fints_holdings()
            return refresh_holdings(kefiya_login, holdings)

        summary["holdings"] = _optional_fetch(
            summary, "holdings", "holdings", _fetch_holdings)

        # 3) Standing orders / scheduled debits -> forecast table (best effort).
        def _fetch_planned():
            from kefiya.utils.planned_payment import (
                STANDING_ORDER_KINDS, normalize_scheduled_debits,
                refresh_planned_payments,
            )
            raw = _to_jsonable(FetchCtl(kefiya_login).get_fints_scheduled_debits())
            norm = normalize_scheduled_debits(raw if isinstance(raw, list) else [])
            # Scoped: without it this sweep would cancel the pending entries
            # written just above, and the next one would cancel these back.
            planned = refresh_planned_payments(
                kefiya_login, norm["items"],
                payment_kinds=STANDING_ORDER_KINDS)
            planned["skipped"] = norm["skipped"]
            return planned

        summary["planned"] = _optional_fetch(
            summary, "planned", "scheduled_debits", _fetch_planned)

        def _fetch_standing_orders():
            rows = _to_jsonable(
                FetchCtl(kefiya_login).get_fints_standing_orders())
            rows = rows if isinstance(rows, list) else []
            return {
                "count": len(rows),
                "orders": rows,
                # Until the schedule's field order is confirmed against real
                # orders, say so rather than let a number imply certainty.
                "schedule_confirmed": False,
            }

        # Standing orders are a different business transaction from the
        # scheduled debits above, and were never asked for at all.
        summary["standing_orders"] = _optional_fetch(
            summary, "standing_orders", "standing_orders",
            _fetch_standing_orders)

        # 4) Electronic statements -> the documents themselves, attached to the
        #    login. The list alone only says which ones exist; each has to be
        #    fetched separately.
        def _fetch_statements():
            controller = FetchCtl(kefiya_login)
            listing = controller.get_fints_statements()
            return fetch_persistence.download_statements(
                controller, kefiya_login,
                listing if isinstance(listing, list) else [])

        summary["statements"] = _optional_fetch(
            summary, "statements", "statements", _fetch_statements)

        # 5) Credit-card transactions -> Bank Transactions, same as a booking.
        def _fetch_credit_card():
            cc = _to_jsonable(
                FetchCtl(kefiya_login).get_fints_credit_card_transactions())
            return fetch_persistence.store_credit_card_transactions(
                kefiya_login, cc if isinstance(cc, list) else [])

        summary["credit_card"] = _optional_fetch(
            summary, "credit_card", "credit_card", _fetch_credit_card)

        # 6) What the bank lets this account transfer. No extra command: the
        #    limit rides on the account information sent at logon, so reading
        #    it inside the shared session costs nothing -- and a limit read
        #    once and never again is the one that silently goes stale and
        #    starts rejecting payment runs.
        def _fetch_limit():
            from kefiya.utils.transfer_limit import refresh_transfer_limit

            return refresh_transfer_limit(
                kefiya_login, controller=FetchCtl(kefiya_login))

        summary["transfer_limit"] = _optional_fetch(
            summary, "transfer_limit", "transfer_limit", _fetch_limit)

        # 7) What the bank ALLOWS on each account -- same segment as the limit,
        #    same free ride inside the shared session. Read every time for the
        #    same reason: a bank that enables (or withdraws) a transaction on
        #    an account says so at the next logon and nowhere else, so a list
        #    read once would keep hiding a button the bank has since offered.
        def _fetch_capabilities():
            from kefiya.utils.account_capabilities import (
                refresh_account_capabilities,
            )

            return refresh_account_capabilities(
                kefiya_login, controller=FetchCtl(kefiya_login))

        summary["capabilities"] = _optional_fetch(
            summary, "capabilities", "account_capabilities",
            _fetch_capabilities)

        return summary


@frappe.whitelist()
def submit_payment_request_via_fints(payment_request_name, user_scope, confirmed=0, instant_payment=0):
    """Prepare a SEPA credit transfer (pain.001) for an Outward Payment Request
    and submit it directly via FinTS -- no manual file upload.

    Money movement stays human-in-the-loop: the caller must pass
    ``confirmed=1`` (set only after an explicit user confirmation dialog), and
    the bank's TAN is supplied by the user (the UI prompts via the realtime TAN
    handler, then calls ``send_transfer_tan``). This never sends money on its
    own.

    :return: {"status": "submitted" | "tan_required" | "error", ...}
    """
    from frappe.utils import cint

    # Hard gate: never reach sepa_transfer without explicit confirmation.
    if not cint(confirmed):
        return {"status": "error", "message": _(
            "Transfer not confirmed. Money is only sent after explicit"
            " confirmation."
        )}

    # Permission gate: whitelisted endpoints are callable by any logged-in user,
    # so a money-moving transfer must require submit rights on the Payment
    # Request (and read rights on the paying Kefiya Login below).
    frappe.has_permission(
        "Payment Request", ptype="submit",
        doc=payment_request_name, throw=True)

    from kefiya.events.hammer_script.payment_request_on_submit import (
        _build_sepa_xml,
    )

    pr = frappe.get_doc("Payment Request", payment_request_name)
    if pr.payment_request_type != "Outward":
        return {"status": "error",
                "message": _("Only Outward Payment Requests can be paid out.")}
    if pr.docstatus != 1:
        return {"status": "error",
                "message": _("Payment Request must be submitted before payout.")}
    if not pr.company_bank_account:
        return {"status": "error",
                "message": _("Payment Request has no company bank account.")}

    # B2: guard against double submission (double click / retry). The claim
    # itself happens further down, immediately before the bank is contacted --
    # atomically, and after the checks that can still refuse the payout, so a
    # refused attempt does not block a retry for ten minutes.
    lock_key = "kefiya_transfer:" + payment_request_name

    # B7: the company bank account must map to exactly one Kefiya Login.
    logins = frappe.get_all(
        "Kefiya Login",
        filters={"bank_account": pr.company_bank_account},
        pluck="name",
    )
    if len(logins) != 1:
        return {"status": "error", "message": _(
            "Expected exactly one Kefiya Login for bank account {0}, found {1}."
        ).format(pr.company_bank_account, len(logins))}
    kefiya_login = logins[0]

    xml_content, error = _build_sepa_xml(payment_request_name)
    if error:
        return {"status": "error", "message": error}
    if not xml_content:
        return {"status": "error", "message": _("Failed to generate SEPA XML.")}

    # B8: audit every attempt to move money before contacting the bank.
    frappe.logger("kefiya").info(
        "SEPA transfer attempt: pr=%s login=%s user=%s",
        payment_request_name, kefiya_login, frappe.session.user,
    )
    if not _claim_send_lock(lock_key):
        return {"status": "error", "message": _(
            "A transfer for this Payment Request is already in progress."
        )}

    # Transfers always require strong authentication (PSD2), so always use the
    # TAN-capable controller regardless of the import-mode setting.
    from kefiya.utils.fints_controller import FinTSController
    interactive = {"docname": user_scope, "enabled": True}
    try:
        controller = FinTSController(kefiya_login, interactive)
        return controller.submit_sepa_transfer(
            xml_content, instant_payment=instant_payment,
            payment_reference=payment_request_name)
    except Exception:
        # release the lock on hard failure so the user can retry deliberately;
        # the audit log + bank statement remain the source of truth.
        frappe.cache().delete_value(lock_key)
        raise


@frappe.whitelist()
def submit_kefiya_transfer(transfer_name, user_scope, confirmed=0):
    """Send an approved Kefiya Transfer to the bank.

    One payment goes out as a single transfer (HKCCS, or HKIPZ when instant),
    several as one collective order (HKCCM) that needs only a single TAN
    instead of one per payment.

    Recipients are typed in freely here, so the guards the invoice workflow
    would otherwise provide sit on this path instead: the document must be
    submitted (and submit rights are separate from create rights), the caller
    must confirm explicitly, and nothing is sent twice.

    :return: {"status": "submitted" | "tan_required" | "vop_mismatch" | "error"}
    """
    from frappe.utils import cint

    # Hard gate: never reach the bank without an explicit confirmation.
    if not cint(confirmed):
        return {"status": "error", "message": _(
            "Transfer not confirmed. Money is only sent after explicit"
            " confirmation."
        )}

    # Permission gate: sending is a submit-level action on the transfer.
    frappe.has_permission(
        "Kefiya Transfer", ptype="submit", doc=transfer_name, throw=True)

    doc = frappe.get_doc("Kefiya Transfer", transfer_name)
    if doc.docstatus != 1:
        return {"status": "error", "message": _(
            "The transfer must be approved (submitted) before it is sent."
        )}
    if doc.status == "Sent":
        return {"status": "error", "message": _(
            "This transfer was already sent to the bank."
        )}
    if doc.status == "Scheduled at Bank":
        return {"status": "error", "message": _(
            "The bank already holds this transfer for {0}. Change or cancel it"
            " with your bank."
        ).format(frappe.utils.formatdate(doc.execution_date))}

    # An order we manage ourselves must not be paid before its day just
    # because someone pressed send: the date is the whole point of it.
    if _not_due_yet(doc):
        return {"status": "error", "message": _(
            "{0} is due on {1} and is managed here, so it is presented for"
            " release on that day. To pay it now set the execution date to"
            " today; to let the bank hold it, untick \"Manage the due date"
            " ourselves\"."
        ).format(transfer_name, frappe.utils.formatdate(doc.execution_date))}

    scheduled = _bank_holds_the_date(doc)

    unsupported = _refuse_unsupported([doc], scheduled)
    if unsupported:
        return unsupported

    over = _refuse_over_limit([doc])
    if over:
        return over

    # Guard against a double click resending real money. Claimed atomically
    # just before the bank is contacted, see _claim_send_lock.
    lock_key = "kefiya_transfer_doc:" + transfer_name

    pain_xml, control_sum, count = doc.build_pain001()

    # Audit every attempt before contacting the bank.
    frappe.logger("kefiya").info(
        "Kefiya Transfer attempt: doc=%s login=%s count=%s sum=%s user=%s",
        transfer_name, doc.kefiya_login, count, control_sum,
        frappe.session.user,
    )
    if not _claim_send_lock(lock_key):
        return {"status": "error", "message": _(
            "A transfer for this document is already in progress."
        )}

    from kefiya.utils.fints_controller import FinTSController
    interactive = {"docname": user_scope, "enabled": True}
    try:
        controller = FinTSController(doc.kefiya_login, interactive)
        result = controller.submit_sepa_transfer(
            pain_xml,
            instant_payment=cint(doc.instant_payment),
            payment_reference=transfer_name,
            multiple=count > 1,
            control_sum=control_sum if count > 1 else None,
            scheduled=scheduled,
        )
    except Exception as exc:
        # Release the lock so the user can retry deliberately; the audit log
        # and the bank statement remain the source of truth.
        frappe.cache().delete_value(lock_key)
        if scheduled and _is_unsupported_schedule(exc):
            # The bank cannot file a dated order. Nothing was sent, so the
            # honest outcome is to keep it here and say so -- not to pay it
            # today because the requested date could not be honoured.
            doc.db_set("manage_due_date", 1)
            return {"status": "error",
                    "message": _unsupported_schedule_message(doc)}
        raise

    status = (result or {}).get("status")
    if status == "submitted":
        doc.db_set("status", "Scheduled at Bank" if scheduled else "Sent")
        if result.get("task_id"):
            doc.db_set("bank_task_id", result["task_id"])
    elif status == "vop_mismatch":
        doc.db_set("vop_pending", 1)
        _parked_is_not_failed(doc)
    return result


def _parked_is_not_failed(doc):
    """An order held at the payee check is not an order that failed.

    A "Failed" left over from an earlier attempt survives the next one --
    db_set on vop_pending does not touch it -- and the list then shows red
    for an order that is approved, unsent and waiting for one confirmation.
    Only that stale state is cleared; Due or On Hold say something this does
    not know better than.
    """
    if doc.status == "Failed":
        doc.db_set("status", "Approved")


def present_due_transfers():
    """Put transfers whose day has come in front of somebody.

    This is the half of "manage the due date ourselves" that actually happens
    on the day. It deliberately does NOT send: a credit transfer needs a TAN,
    and with a decoupled procedure (pushTAN) there is nobody to confirm it at
    four in the morning -- the run would leave a parked challenge behind and
    still not have paid anyone. So the order is marked Due and the people who
    may release it get a ToDo.

    Idempotent: an order already marked Due is not announced twice.

    :return: {"due": int}
    """
    from frappe.utils import formatdate, nowdate

    names = frappe.get_all(
        "Kefiya Transfer",
        filters={
            "docstatus": 1,
            "status": "Approved",
            "manage_due_date": 1,
            "on_hold": 0,
            "execution_date": ("<=", nowdate()),
        },
        pluck="name",
    )
    for name in names:
        doc = frappe.get_doc("Kefiya Transfer", name)
        doc.db_set("status", "Due")
        try:
            _announce_due_transfer(doc, formatdate(doc.execution_date))
        except Exception:
            # The status is what the outbox is read from; a failed ToDo must
            # not hide a payment that is due.
            frappe.log_error(
                title="Kefiya: could not announce a due transfer",
                message=frappe.get_traceback(),
                reference_doctype="Kefiya Transfer",
                reference_name=name,
            )

    return {"due": len(names)}


def _announce_due_transfer(doc, due_on):
    """One ToDo per person who may release the transfer."""
    owners = set()
    if doc.owner:
        owners.add(doc.owner)
    if doc.modified_by:
        owners.add(doc.modified_by)

    description = _(
        "Transfer {0} over {1} is due on {2} and needs to be released."
    ).format(doc.name, frappe.utils.fmt_money(doc.total_amount), due_on)

    for owner in owners:
        if frappe.db.exists("ToDo", {
            "reference_type": "Kefiya Transfer",
            "reference_name": doc.name,
            "allocated_to": owner,
            "status": "Open",
        }):
            continue
        frappe.get_doc({
            "doctype": "ToDo",
            "allocated_to": owner,
            "reference_type": "Kefiya Transfer",
            "reference_name": doc.name,
            "date": doc.execution_date,
            "priority": "High",
            "description": description,
        }).insert(ignore_permissions=True)


def _refuse_over_limit(docs):
    """Stop an order the bank would refuse anyway -- and say what to do.

    A bank rejects the WHOLE order when it exceeds the limit, not the part
    above it. Finding that out from a rejection message means a payment run
    that paid nothing, so it is checked here first. The whole selection is
    measured at once: the limit applies to what leaves the account, not to one
    document.

    :return: an error dict to return to the caller, or None when it fits
    """
    from kefiya.utils.transfer_limit import check_batch

    verdict = check_batch(docs)
    if verdict["ok"]:
        return None

    return {"status": "error", "over_limit": True,
            "limit": verdict.get("limit"),
            "available": verdict.get("available"),
            "message": verdict["reason"] + " " + _(
                "Split it over several days, or reduce it.")}


def _refuse_unsupported(docs, scheduled):
    """Stop an order this account was never allowed to send.

    The bank states its per-account business transactions at logon, long
    before anybody presses send. Letting the order go anyway means the refusal
    arrives as a return code in the middle of the dialog -- after the TAN was
    spent, and worded by the bank rather than by us.

    Only an explicit refusal stops anything: an account whose list has never
    been fetched has no list, and that is answered with "unknown", which
    passes. Nothing is blocked on a guess.

    :return: an error dict to return to the caller, or None
    """
    from kefiya.utils import account_capabilities as caps

    docs = [d for d in (docs or []) if d]
    if not docs:
        return None

    bank_account = frappe.db.get_value(
        "Kefiya Login", docs[0].kefiya_login, "bank_account")
    if not bank_account:
        return None

    count = sum(len(d.items or []) for d in docs)
    capability = caps.required_capability(
        payment_count=count,
        scheduled=bool(scheduled),
        instant=bool(docs[0].instant_payment))

    if not caps.refuses(bank_account, capability):
        return None

    return {"status": "error", "unsupported": True,
            "capability": capability,
            "message": caps.refusal_message(bank_account, capability)}


def _is_unsupported_schedule(exc):
    """Did the bank simply not offer the dated-transfer segment?"""
    try:
        from fints.exceptions import FinTSUnsupportedOperation
    except Exception:
        return False
    return isinstance(exc, FinTSUnsupportedOperation)


def _bank_holds_the_date(doc):
    """True when the BANK is to keep this order until its execution date.

    Two ways to pay on a future day, and the user picks per transfer, the way
    StarMoney does it:

    * we keep it -- the order waits in the outbox and is presented for release
      on the day. Nothing reaches the bank until then, so it can still be
      changed or dropped here.
    * the bank keeps it -- the order is handed over now as a dated transfer
      (HKCSE) and the bank executes it on the day. From then on it can only be
      changed at the bank, which is why it is not the default.

    A date of today or earlier is never handed over: no bank files an order for
    a day that has arrived.
    """
    from frappe.utils import cint, getdate, nowdate

    if cint(doc.manage_due_date):
        return False
    return getdate(doc.execution_date or nowdate()) > getdate(nowdate())


def _not_due_yet(doc):
    """True for an order we manage that has not reached its day."""
    from frappe.utils import cint, getdate, nowdate

    if not cint(doc.manage_due_date):
        return False
    return getdate(doc.execution_date or nowdate()) > getdate(nowdate())


def _unsupported_schedule_message(doc):
    from frappe.utils import formatdate

    return _(
        "This bank does not offer transfers dated ahead, so it will not hold"
        " {0} until {1}. The order stays here and is presented for release on"
        " that day instead."
    ).format(doc.name, formatdate(doc.execution_date))


def _parse_transfer_names(transfer_names):
    """Turn the client's argument into a de-duplicated list of names.

    Two failure modes this guards against, both of which pay twice:

    * a JSON string instead of a list -- iterating it would walk single
      characters rather than document names;
    * the same name listed twice -- the document would be loaded twice and its
      payments would appear twice in one pain.001, so the recipient is paid
      twice from a single order.

    Order is preserved so the message matches what the user saw.
    """
    if isinstance(transfer_names, str):
        try:
            transfer_names = json.loads(transfer_names)
        except ValueError:
            frappe.throw(_("Invalid transfer selection."))

    if isinstance(transfer_names, str) or not isinstance(
            transfer_names, (list, tuple)):
        frappe.throw(_("Invalid transfer selection: expected a list."))

    seen = set()
    unique = []
    for name in transfer_names:
        if not isinstance(name, str) or not name:
            frappe.throw(_("Invalid transfer selection."))
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return unique


@frappe.whitelist()
def set_transfer_hold(transfer_names, on_hold):
    """Hold approved transfers back in the outbox, or release them again.

    Holding changes only when an order is sent, never what it says -- amounts
    and recipients stay locked by the submit -- which is why it is permitted
    after approval while editing the document is not.
    """
    from frappe.utils import cint

    transfer_names = _parse_transfer_names(transfer_names)

    changed = []
    for name in transfer_names:
        frappe.has_permission(
            "Kefiya Transfer", ptype="submit", doc=name, throw=True)
        doc = frappe.get_doc("Kefiya Transfer", name)
        doc.set_hold(cint(on_hold))
        changed.append(name)
    return {"status": "ok", "changed": changed}


@frappe.whitelist()
def approve_transfers(transfer_names):
    """Approve several drafts at once -- one submit each, in one action.

    Approving is what submitting the document does: it locks the amounts and
    the recipients and assigns the end-to-end identifiers. It moves no money.
    Sending is a separate step that the bank confirms with a TAN, which is why
    approving a whole selection is safe to offer as a batch while sending one
    is deliberately confirmed order by order.

    Each document is submitted inside its own savepoint. A document that
    refuses -- over the transfer limit, a recipient the bank rejects -- must
    not undo the ones approved before it, and it must not pass unnoticed
    either: the caller is told, per document, what happened. A silent partial
    success is the failure mode this guards against.

    :param transfer_names: JSON list (or list) of Kefiya Transfer names
    :return: {"status", "approved": [names], "refused": [{"name", "reason"}]}
    """
    transfer_names = _parse_transfer_names(transfer_names)
    if not transfer_names:
        return {"status": "error", "message": _("No transfers selected.")}

    approved = []
    refused = []
    for name in transfer_names:
        frappe.has_permission(
            "Kefiya Transfer", ptype="submit", doc=name, throw=True)

        doc = frappe.get_doc("Kefiya Transfer", name)
        if doc.docstatus != 0:
            # Already approved, or cancelled. Saying so beats submitting it
            # again and reporting a framework error as if something broke.
            refused.append({"name": name,
                            "reason": _("Not a draft any more.")})
            continue

        point = "kefiya_approve"
        frappe.db.savepoint(point)
        try:
            doc.submit()
            approved.append(name)
        except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
            frappe.db.rollback(save_point=point)
            refused.append({"name": name, "reason": _short_reason(exc)})

    return {"status": "ok", "approved": approved, "refused": refused}


@frappe.whitelist()
def send_transfer_outbox(transfer_names, user_scope, confirmed=0,
                         approve_drafts=0):
    """Send several approved transfers as one collective order (HKCCM).

    This is what makes the outbox worth having: orders are entered one by one
    and then leave together on a single TAN, instead of one TAN per order.

    Refuses rather than guesses. Mixing paying accounts would silently debit
    the wrong one, an already-sent order would pay twice, and a held-back order
    is held back for a reason -- so each of those aborts the whole send instead
    of quietly dropping or including a document.

    Drafts may be approved on the way out, and where that happens is the whole
    point: AFTER every refusal above has had its say, not before. Approving is
    irreversible -- it locks the amounts and the recipients, and undoing it
    means cancelling the order and entering it again. Doing it in the browser
    first, as the outgoing-payments page did, meant that a batch refused here
    for mixing execution dates left its drafts approved for nothing.

    :param transfer_names: JSON list (or list) of Kefiya Transfer names
    :param approve_drafts: submit the still-unapproved orders in the selection
        once the batch as a whole has been accepted
    :return: {"status", "sent": [names], "approved": [names],
        "refused": [{"name", "reason"}]}
    """
    from frappe.utils import cint

    if not cint(confirmed):
        return {"status": "error", "message": _(
            "Transfer not confirmed. Money is only sent after explicit"
            " confirmation."
        )}

    transfer_names = _parse_transfer_names(transfer_names)
    if not transfer_names:
        return {"status": "error", "message": _("No transfers selected.")}

    approve_drafts = cint(approve_drafts)
    docs = []
    drafts = []
    for name in transfer_names:
        frappe.has_permission(
            "Kefiya Transfer", ptype="submit", doc=name, throw=True)
        doc = frappe.get_doc("Kefiya Transfer", name)
        if doc.docstatus == 0 and approve_drafts:
            # Kept in the batch and checked like every other order. What it is
            # NOT yet is approved -- that waits until the checks are through.
            drafts.append(doc)
        elif doc.docstatus != 1:
            return {"status": "error", "message": _(
                "{0} is not approved yet."
            ).format(name)}
        if doc.status == "Sent":
            return {"status": "error", "message": _(
                "{0} was already sent to the bank."
            ).format(name)}
        if doc.status == "Scheduled at Bank":
            return {"status": "error", "message": _(
                "{0} is already held by the bank for its execution date."
            ).format(name)}
        if doc.on_hold:
            return {"status": "error", "message": _(
                "{0} is held back. Release it first or deselect it."
            ).format(name)}
        if _not_due_yet(doc):
            return {"status": "error", "message": _(
                "{0} is due on {1} and is managed here. It is presented for"
                " release on that day; deselect it."
            ).format(name, frappe.utils.formatdate(doc.execution_date))}
        docs.append(doc)

    # One message carries one execution date, so a batch cannot mix orders the
    # bank is to hold with orders that go out now -- the bank would apply one
    # date to all of them.
    scheduling = {_bank_holds_the_date(doc) for doc in docs}
    if len(scheduling) > 1:
        return {"status": "error", "message": _(
            "Some of the selected transfers are to be held by the bank and"
            " some are to go out now. Send them separately."
        )}
    scheduled = scheduling.pop() if scheduling else False
    if scheduled and len({str(doc.execution_date) for doc in docs}) > 1:
        return {"status": "error", "message": _(
            "A collective order carries one execution date. The selected"
            " transfers name different days -- send them separately."
        )}

    logins = {doc.kefiya_login for doc in docs}
    if len(logins) > 1:
        return {"status": "error", "message": _(
            "All selected transfers must be paid from the same account."
        )}
    kefiya_login = docs[0].kefiya_login

    # Several orders leave as ONE collective message, so the transaction the
    # bank has to allow is the collective one -- an account cleared for a
    # single transfer is not thereby cleared for HKCCM.
    unsupported = _refuse_unsupported(docs, scheduled)
    if unsupported:
        return unsupported

    # The bank's limit applies to what leaves the account, so the selection is
    # measured as a whole. Checking each document on its own would wave through
    # ten orders that each fit and together do not.
    over = _refuse_over_limit(docs)
    if over:
        return over

    # Every refusal is behind us. Only now is it safe to approve, because from
    # here the order either goes to the bank or fails at the bank -- and an
    # order that failed at the bank is meant to stay approved and be sent
    # again, which is exactly not true of one this endpoint turned away.
    approved, refused = [], []
    if drafts:
        for doc in drafts:
            point = "kefiya_send_approve"
            frappe.db.savepoint(point)
            try:
                doc.submit()
                approved.append(doc.name)
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                frappe.db.rollback(save_point=point)
                refused.append({"name": doc.name,
                                "reason": _short_reason(exc)})
        if refused:
            # The batch is smaller than the one that was confirmed. Dropping
            # the refused ones is right -- they carry no end-to-end identifier
            # and cannot be part of the message -- but it must be said, and
            # the total the limit was measured against only ever shrinks.
            gone = {r["name"] for r in refused}
            docs = [doc for doc in docs if doc.name not in gone]
            transfer_names = [n for n in transfer_names if n not in gone]
        if not docs:
            return {"status": "error", "approved": approved,
                    "refused": refused,
                    "message": _("Nothing could be approved, so nothing was"
                                 " sent.")}

    # Lock per document, not per account. The single-document endpoint locks
    # the same keys, so a document cannot be sent through both paths at once --
    # which would pay it twice.
    lock_keys = ["kefiya_transfer_doc:" + doc.name for doc in docs]

    from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
        build_pain001_for,
    )
    pain_xml, control_sum, count = build_pain001_for(docs)

    frappe.logger("kefiya").info(
        "Kefiya outbox send: docs=%s login=%s count=%s sum=%s user=%s",
        ",".join(transfer_names), kefiya_login, count, control_sum,
        frappe.session.user,
    )
    # Claim every document atomically. All or nothing: a partial claim would
    # leave documents locked that this send never covers, and sending a batch
    # while one of its documents is already on its way to the bank is exactly
    # the double payment these keys exist to prevent.
    claimed = []
    for key in lock_keys:
        if _claim_send_lock(key):
            claimed.append(key)
            continue
        for done in claimed:
            frappe.cache().delete_value(done)
        return {"status": "error", "approved": approved, "refused": refused,
                "message": _(
                    "A send is already in progress for: {0}"
                ).format(key.split(":", 1)[1])}

    from kefiya.utils.fints_controller import FinTSController
    interactive = {"docname": user_scope, "enabled": True}
    try:
        controller = FinTSController(kefiya_login, interactive)
        result = controller.submit_sepa_transfer(
            pain_xml,
            instant_payment=cint(docs[0].instant_payment),
            payment_reference=",".join(transfer_names)[:140],
            multiple=count > 1,
            control_sum=control_sum if count > 1 else None,
            scheduled=scheduled,
        )
    except Exception as exc:
        for key in lock_keys:
            frappe.cache().delete_value(key)
        if scheduled and _is_unsupported_schedule(exc):
            for doc in docs:
                doc.db_set("manage_due_date", 1)
            return {"status": "error", "approved": approved,
                    "refused": refused,
                    "message": _unsupported_schedule_message(docs[0])}
        raise

    status = (result or {}).get("status")
    if status == "submitted":
        # The bank accepted one message covering all of them, so they are
        # marked together -- leaving part of a batch as unsent would invite a
        # second send of money that is already gone.
        for doc in docs:
            doc.db_set("status",
                       "Scheduled at Bank" if scheduled else "Sent")
            if result.get("task_id"):
                doc.db_set("bank_task_id", result["task_id"])
    elif status == "vop_mismatch":
        for doc in docs:
            doc.db_set("vop_pending", 1)
            _parked_is_not_failed(doc)

    result["sent"] = transfer_names
    # What the send approved on its way out, so the page can name an order
    # that was locked but never left -- silence there is how a draft goes
    # missing between two states.
    result["approved"] = approved
    result["refused"] = refused
    return result


@frappe.whitelist()
def refresh_transfer_limit(kefiya_login, user_scope=None):
    """Ask the bank what this account may transfer, and store it.

    Reading the limit contacts the bank, and the answer decides whether a
    payment run is allowed to leave -- so it takes the same write right on the
    login that a fetch does.
    """
    frappe.has_permission("Kefiya Login", ptype="write",
                          doc=kefiya_login, throw=True)

    from kefiya.utils.fints_controller import FinTSController
    from kefiya.utils.transfer_limit import refresh_transfer_limit as _refresh

    interactive = {"docname": user_scope or kefiya_login, "enabled": True}
    return _refresh(kefiya_login,
                    controller=FinTSController(kefiya_login, interactive))


@frappe.whitelist()
def get_pending_vop(kefiya_login):
    """Return the parked Verification-of-Payee mismatch for a login, if any.

    Read-only: lets the UI show what the bank actually objected to before a
    reviewer decides whether to release the transfer.
    """
    frappe.has_permission(
        "Kefiya Login", ptype="read", doc=kefiya_login, throw=True)

    login = frappe.get_doc("Kefiya Login", kefiya_login)
    if not login.stored_vop_state:
        return {"status": "none"}

    from kefiya.utils import vop_rule

    return {
        "status": "pending",
        "reference": login.vop_reference,
        "vop_result": vop_rule.parked_answer(login.vop_result),
    }


@frappe.whitelist()
def approve_vop_transfer(kefiya_login, user_scope, confirmed=0):
    """Release a transfer the bank flagged with a VoP mismatch.

    The bank could not confirm that the payee name matches the IBAN. Kefiya
    refuses such an order outright and parks it; this endpoint is the only way
    it can still go through, and only after a human compared the payee against
    the underlying document. ``confirmed`` must be set by an explicit dialog --
    a VoP mismatch is exactly the signal a payment-diversion fraud produces, so
    it must never be waved through automatically.

    :return: {"status": "submitted" | "tan_required" | "error", ...}
    """
    from frappe.utils import cint

    # Hard gate: releasing a flagged payee is a deliberate human act.
    if not cint(confirmed):
        return {"status": "error", "message": _(
            "Verification of Payee mismatch not confirmed. The payee must be"
            " checked against the invoice before the transfer is released."
        )}

    # Permission gate: this is the step that lets money leave despite the
    # bank's warning, so require write rights on the paying login.
    frappe.has_permission(
        "Kefiya Login", ptype="write", doc=kefiya_login, throw=True)

    login = frappe.get_doc("Kefiya Login", kefiya_login)
    if not login.stored_vop_state:
        return {"status": "error",
                "message": _("No pending Verification of Payee for this login.")}

    # Audit before contacting the bank: who released which flagged payee.
    frappe.logger("kefiya").info(
        "VoP override: login=%s reference=%s user=%s",
        kefiya_login, login.vop_reference, frappe.session.user,
    )
    frappe.log_error(
        title="Kefiya: Verification-of-Payee mismatch released by user",
        message="login={0} reference={1} user={2}\nvop_result={3}".format(
            kefiya_login, login.vop_reference, frappe.session.user,
            login.vop_result,
        ),
    )

    from kefiya.utils.fints_controller import FinTSController
    interactive = {"docname": user_scope, "enabled": True}
    controller = FinTSController(kefiya_login, interactive)
    return controller.approve_pending_vop()


@frappe.whitelist()
def send_transfer_tan(kefiya_login, tan, user_scope):
    """Continue a pending SEPA transfer by sending the user's TAN.

    Reuses the controller's stored-TAN resume mechanism (the pending transfer
    dialog was persisted when the TAN was requested).

    What the bank answered is checked by the controller, at the point the TAN
    is answered -- see _refuse_a_refused_order. This used to return
    {"status": "submitted"} whenever building the controller did not raise,
    never looking at the answer at all, while its own docstring promised that
    a money movement is never reported as done on a guess. A refusal now
    arrives here as an exception and is reported as one.
    """
    # Permission gate: continuing a money transfer with a TAN requires write
    # rights on the paying Kefiya Login (whitelisted endpoint would otherwise be
    # callable by any logged-in user).
    frappe.has_permission(
        "Kefiya Login", ptype="write", doc=kefiya_login, throw=True)

    from kefiya.utils.fints_controller import (
        FinTSController,
        TanInteractionRequired,
    )
    interactive = {"docname": user_scope, "enabled": True}
    try:
        # Re-instantiating with the TAN resumes the stored dialog and sends it.
        FinTSController(kefiya_login, interactive, tan=tan)
    except TanInteractionRequired:
        # The bank requested a further/renewed challenge; the UI re-prompts.
        return {"status": "tan_required", "docname": kefiya_login}
    except Exception as e:
        frappe.log_error(
            title="Kefiya SEPA transfer TAN submission failed",
            message=frappe.get_traceback(),
        )
        return {"status": "error", "message": str(e)}
    return {"status": "submitted"}


@frappe.whitelist()
def get_accounts(kefiya_login, user_scope):
    """Return FinTS accounts for a given login.

    For TAN-enabled mode we may end up triggering a TAN flow.
    For legacy mode we just use the old controller.
    """
    # Reading the bank's account list exposes account data of that login.
    frappe.has_permission("Kefiya Login", ptype="read",
                          doc=kefiya_login, throw=True)
    FinTSController = _get_fints_controller()

    interactive = {"docname": user_scope, "enabled": True}

    # New controller may raise TanInteractionRequired – we just ignore and let
    # the realtime handler + UI deal with it. Legacy controller won’t raise it.
    try:
        return {
            "accounts": FinTSController(
                kefiya_login,
                interactive
            ).get_fints_accounts()
        }
    except Exception:
        # In TAN mode this can be TanInteractionRequired – handled via socket.
        # For legacy mode we re-raise to not hide real errors.
        if not _use_tan_authentication():
            raise


@frappe.whitelist()
def is_tan_enabled():
    """Small helper for JS if needed."""
    return _use_tan_authentication()


@frappe.whitelist()
def new_bank_account(payment_doc, bankData):
    """Create new bank account.

    Create new bank account and if missing a bank entry.
    :param payment_doc: json formated payment_doc
    :param bankData: json formated bank information
    :type payment_doc: str
    :type bankData: str
    :return: Dict with status and bank details
    """
    # Permission gate: creates a Bank Account record from client-supplied
    # bank data, so it must require create rights rather than being callable
    # by any logged-in user.
    frappe.has_permission("Bank Account", ptype="create", throw=True)
    from kefiya.utils.bank_account_controller import \
        BankAccountController
    return BankAccountController().new_bank_account(payment_doc, bankData)


@frappe.whitelist()
def get_missing_bank_accounts():
    """Get possibly missing bank accounts.

    Query payment entries for missing bank accounts.
    :return: List of payment entry data
    """
    frappe.has_permission("Bank Account", ptype="read", throw=True)
    from kefiya.utils.bank_account_controller import \
        BankAccountController
    return BankAccountController().get_missing_bank_accounts()


@frappe.whitelist()
def has_page_permission(page_name):
    """Check if user has permission for a page doctype.

    Based on frappe/desk/desk_page.py
    :param page_doc: page doctype object
    :type page_doc: page doctyp
    :return: Boolean
    """
    from kefiya.utils.bank_account_controller import \
        has_page_permission
    return has_page_permission(page_name)


@frappe.whitelist()
def add_payment_reference(payment_entry, sales_invoice):
    """Add payment reference to payment entry for sales invoice.

    Create new bank account and if missing a bank entry.
    :param payment_entry: json formated payment_doc
    :param sales_invoice: json formated bank information
    :type payment_entry: str
    :type sales_invoice: str
    :return: Payment reference name
    """
    # Permission gate: creating/attaching Payment Entries requires write rights.
    frappe.has_permission("Payment Entry", ptype="write", throw=True)

    from kefiya.utils.assign_payment_controller import \
        AssignmentController

    return AssignmentController().add_payment_reference(
        payment_entry,
        sales_invoice
    )


@frappe.whitelist()
def add_sales_invoice_payment(bank_transaction_name, sales_invoice_name):
    """Amount to allocate when reconciling a bank transaction against an
    invoice.

    The assignment wizard's "reconcile" button calls this and hands the result
    to ERPNext's reconcile_vouchers. The method never existed in this module,
    so the button answered with a 404 ("Die von Ihnen gesuchte Ressource ist
    nicht verfuegbar") and no reconciliation ever happened.

    Allocating more than either side has open would leave a negative
    outstanding on the invoice or over-allocate the transaction, so the smaller
    of the two open amounts wins -- the same rule the automatic assignment
    applies.

    :return: amount to allocate (float, always > 0)
    """
    from frappe.utils import flt

    # Reconciliation writes the allocation onto the Bank Transaction, so write
    # rights there are the relevant gate; the invoice is only read.
    #
    # Both checks are document-bound. A DocType-level check alone would let a
    # user whose access is narrowed by a User Permission -- to one company, say
    # -- read any bank transaction's unallocated amount and reconcile against it.
    transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
    frappe.has_permission(
        "Bank Transaction", ptype="write", doc=transaction, throw=True)

    invoice = frappe.get_doc("Sales Invoice", sales_invoice_name)
    frappe.has_permission("Sales Invoice", ptype="read", doc=invoice, throw=True)

    unallocated = flt(transaction.unallocated_amount)
    outstanding = flt(invoice.outstanding_amount)

    if unallocated <= 0:
        frappe.throw(_(
            "Bank transaction {0} has nothing left to allocate."
        ).format(bank_transaction_name))

    if outstanding <= 0:
        frappe.throw(_(
            "Sales Invoice {0} has no outstanding amount."
        ).format(sales_invoice_name))

    return min(unallocated, outstanding)


@frappe.whitelist()
def auto_assign_payments():
    """Query assignable payments and create payment references.

    Try to assign payments in 3 steps:
    1. payment to sale assingment
    2. multiple payments to sale assingment
    3. payment to sale assingment

    :return: List of assigned payments
    """
    # Permission gate: bulk-creating Payment Entries requires write rights.
    frappe.has_permission("Payment Entry", ptype="write", throw=True)

    from kefiya.utils.assign_payment_controller import \
        AssignmentController

    return AssignmentController().auto_assign_payments()


def create_mastercard_journal_entry_from_purchase_invoice(invoice_doc, bank_transaction_name, mastercard_account):
    """Create a journal entry/payment entry for refund/normal a purchase invoice."""
    bt = frappe.get_doc("Bank Transaction", bank_transaction_name)
    paid_amount = abs(invoice_doc.outstanding_amount)
    payment_document, payment_entry = '',''
    allocated_amount = 0

    if invoice_doc.status == "Return":
        je = frappe.new_doc("Journal Entry")
        je.posting_date = bt.date
        je.company = invoice_doc.company
        je.voucher_type = "Journal Entry"
        je.user_remark = f"Refund for Return Invoice {invoice_doc.name}"
        payment_document = "Journal Entry"

        
        je.append("accounts", {
            "account": mastercard_account,
            "debit_in_account_currency": paid_amount,
            "credit_in_account_currency": 0,
        })

        je.append("accounts", {
            "account": invoice_doc.credit_to,
            "party_type": "Supplier",
            "party": invoice_doc.supplier,
            "credit_in_account_currency": paid_amount,
            "debit_in_account_currency": 0,
            "reference_type": "Purchase Invoice",
            "reference_name": invoice_doc.name
        })

        je.insert()
        je.submit()
        payment_entry = je.name
        if bt.deposit > 0:
            allocated_amount = paid_amount
        elif bt.withdrawal > 0:
            allocated_amount= -paid_amount # NEGATIVE to increase unallocated
    else: # create payment entry for normal purchase invoice
        payment_entry_doc = frappe.call("erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry", "Purchase Invoice", invoice_doc.name)
        payment_entry_doc.payment_type = "Pay"
        payment_entry_doc.paid_amount = paid_amount
        payment_entry_doc.posting_date = bt.date
        payment_entry_doc.reference_date = bt.date
        payment_entry_doc.reference_no = 'BTN Wizard ' + bt.date.strftime("%Y-%m-%d") if hasattr(bt.date, "strftime") else 'BTN Wizard ' + str(bt.date)
        payment_entry_doc.bank_account = bt.bank_account
        payment_entry_doc.paid_from = mastercard_account

        for reference in payment_entry_doc.references:
            reference.allocated_amount = paid_amount

        payment_entry_doc.insert()
        payment_entry_doc.submit()
        payment_document = "Payment Entry"
        payment_entry = payment_entry_doc.name
        if bt.deposit > 0:
            allocated_amount = -paid_amount # NEGATIVE to increase unallocated
        elif bt.withdrawal > 0:
            allocated_amount = paid_amount

    bt.append("payment_entries", {
        "payment_document": payment_document,
        "payment_entry": payment_entry,
        "allocated_amount": allocated_amount
    })
    bt.save()

    return bt.unallocated_amount, frappe.format(bt.unallocated_amount, "Currency")


# Create Payment Entry record when reconcile button is clicked
@frappe.whitelist()
def create_payment_entry(bank_transaction_name, invoice_name, match_against):
    """Create payment entry document from sales or purchase invoice doctype.
    """
    # Permission gate: reconciling a Bank Transaction into a Payment Entry
    # requires write rights on that Bank Transaction.
    frappe.has_permission(
        "Bank Transaction", ptype="write",
        doc=bank_transaction_name, throw=True)

    if match_against == "Mastercard":
        invoice_doc = frappe.get_doc("Purchase Invoice", invoice_name)
        bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
        bank_account = ''
        if bank_transaction.bank_account:
            bank_account = frappe.db.get_values(
                "Bank Account", bank_transaction.bank_account, ["account", "company"], as_dict=True
            )[0]
        if bank_account:
            if bank_account.account:
                return create_mastercard_journal_entry_from_purchase_invoice(invoice_doc, bank_transaction_name, bank_account.account)
            else:
                frappe.throw("Bank Account {} has no account set.".format(bank_transaction.bank_account))
    else:
        bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
        invoice_doc = frappe.get_doc(match_against, invoice_name)

        unallocated_amount = bank_transaction.unallocated_amount
        outstanding_amount = invoice_doc.outstanding_amount
        diff = frappe.format(abs(unallocated_amount - outstanding_amount), "Currency")
        paid_amount = outstanding_amount

        if unallocated_amount <= outstanding_amount:
            paid_amount = unallocated_amount

        payment_entry = frappe.call("erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry", match_against, invoice_name)

        payment_entry.paid_amount = paid_amount
        payment_entry.posting_date = bank_transaction.date
        payment_entry.reference_date = bank_transaction.date
        payment_entry.reference_no = 'BTN Wizard ' + str(bank_transaction.date)
        payment_entry.payment_type = "Receive" if bank_transaction.deposit > 0.0 else "Pay"
        account_from_to = "paid_to" if bank_transaction.deposit > 0.0 else "paid_from"

        bank_account = ''
        if bank_transaction.bank_account:
            bank_account = frappe.db.get_values(
                "Bank Account", bank_transaction.bank_account, ["account", "company"], as_dict=True
            )[0]

        if bank_account and bank_account.account:
            (gl_account, company) = (bank_account.account, bank_account.company)

            payment_entry.bank_account = bank_transaction.bank_account

            if account_from_to == "paid_to":
                payment_entry.paid_to = gl_account
            else:
                payment_entry.paid_from = gl_account

        for reference in payment_entry.references:
            reference.allocated_amount = paid_amount

        payment_entry.insert()
        payment_entry.submit()

        return paid_amount, payment_entry.name, unallocated_amount, outstanding_amount, diff


def _get_priority_parties(assign_against):
	"""Return set of party names (customer/supplier) that have at least one
	unreconciled Bank Transaction, so we can show those invoices first in the wizard.
	"""
	if assign_against == "Sales Invoice":
		bt_filters = [
			["docstatus", "=", 1],
			["party_type", "=", "Customer"],
			["deposit", ">", 0],
			["unallocated_amount", "!=", 0],
		]
	elif assign_against == "Purchase Invoice":
		bt_filters = [
			["docstatus", "=", 1],
			["party_type", "=", "Supplier"],
			["withdrawal", ">", 0],
			["unallocated_amount", "!=", 0],
		]
	elif assign_against == "Mastercard":
		bt_filters = [
			["docstatus", "=", 1],
			["party_type", "=", "Supplier"],
			["unallocated_amount", "!=", 0],
		]
	else:
		return set()
	parties = frappe.get_all(
		"Bank Transaction",
		filters=bt_filters,
		pluck="party",
		distinct=True,
		ignore_permissions=True,
	)
	return set(p for p in parties if p)


# Params that must not be passed to reportview execute (request-only / reserved)
_REPORTVIEW_DISALLOWED = frozenset(
	("cmd", "data", "ignore_permissions", "view", "user", "csrf_token", "join", "assign_against")
)


def _reportview_kwargs(kwargs):
	"""Return kwargs suitable for reportview.execute (strip request-only params)."""
	return {k: v for k, v in kwargs.items() if k not in _REPORTVIEW_DISALLOWED}


@frappe.whitelist()
def get_bank_transaction_wizard_list(doctype, fields, filters, order_by, start, page_length, assign_against=None, **kwargs):
	"""Return list data for Bank Transaction Wizard with invoices that have
	matching bank transactions prioritised (first in the list), so the first
	page shows reconcilable items even when pagination is 20 or 100.
	Returns the same compressed format as frappe.desk.reportview.get.
	"""
	from frappe.desk.reportview import compress, execute

	if isinstance(filters, str):
		filters = frappe.parse_json(filters) or []
	if isinstance(fields, str):
		fields = frappe.parse_json(fields) if fields != "*" else ["*"]

	start = int(start or 0)
	page_length = int(page_length or 20)
	assign_against = assign_against or "Sales Invoice"
	rv_kw = _reportview_kwargs(kwargs)

	# Journal Entry view lists Bank Transactions directly; no prioritisation needed
	if assign_against == "Journal Entry":
		result = execute(
			doctype=doctype,
			fields=fields,
			filters=filters,
			order_by=order_by,
			start=start,
			page_length=page_length,
			**rv_kw,
		)
		return compress(result, {"doctype": doctype, **rv_kw})

	# For Sales Invoice / Purchase Invoice / Mastercard, put invoices with matching BT first
	party_field = "customer" if assign_against == "Sales Invoice" else "supplier"
	priority_parties = _get_priority_parties(assign_against)

	if not priority_parties:
		result = execute(
			doctype=doctype,
			fields=fields,
			filters=filters,
			order_by=order_by,
			start=start,
			page_length=page_length,
			**rv_kw,
		)
		return compress(result, {"doctype": doctype, **rv_kw})

	# Build priority and non-priority filters (same base filters + party in/not in)
	priority_filters = list(filters or []) + [[doctype, party_field, "in", list(priority_parties)]]
	non_priority_filters = list(filters or []) + [[doctype, party_field, "not in", list(priority_parties)]]

	# Count priority rows: pass only allowed args so "fields" is always a list of strings
	_count_args = {
		"doctype": doctype,
		"fields": [f"`tab{doctype}`.name"],
		"filters": priority_filters,
		"order_by": None,
		"start": 0,
		"page_length": 0,
		"run": 0,
	}
	partial_query = execute(**_count_args)
	total_priority = int(
		frappe.db.sql(f"""select count(*) from ( {partial_query} ) _p""")[0][0]
	)

	if start >= total_priority:
		# Page is entirely in non-priority range
		result = execute(
			doctype=doctype,
			fields=fields,
			filters=non_priority_filters,
			order_by=order_by,
			start=start - total_priority,
			page_length=page_length,
			**rv_kw,
		)
	elif start + page_length <= total_priority:
		# Page is entirely in priority range
		result = execute(
			doctype=doctype,
			fields=fields,
			filters=priority_filters,
			order_by=order_by,
			start=start,
			page_length=page_length,
			**rv_kw,
		)
	else:
		# Page spans priority and non-priority
		priority_len = total_priority - start
		priority_result = execute(
			doctype=doctype,
			fields=fields,
			filters=priority_filters,
			order_by=order_by,
			start=start,
			page_length=priority_len,
			**rv_kw,
		)
		non_priority_result = execute(
			doctype=doctype,
			fields=fields,
			filters=non_priority_filters,
			order_by=order_by,
			start=0,
			page_length=page_length - priority_len,
			**rv_kw,
		)
		result = list(priority_result) + list(non_priority_result)

	return compress(result, {"doctype": doctype, **rv_kw})


@frappe.whitelist()
def change_match_against(selected_match):
    # Permission gate: changing the global match strategy requires write rights
    # on Kefiya Settings.
    frappe.has_permission("Kefiya Settings", ptype="write", throw=True)

    kefiya_setting = frappe.get_single("Kefiya Settings")
    kefiya_setting.assign_against = selected_match
    kefiya_setting.save()


@frappe.whitelist()
def resolve_tan_interaction(fints_login: str, values: str | dict):
    """
    When a user was requested to perform a 2FA, this method is called as a callback
    to resolve the interaction. If TAN is disabled, this is effectively a no-op.
    """
    # Permission gate: this continues an authenticated bank dialog with a
    # user-supplied TAN, so it must require write rights on that login.
    frappe.has_permission("Kefiya Login", ptype="write",
                          doc=fints_login, throw=True)
    if not _use_tan_authentication():
        # Old banks / legacy mode: nothing to resolve
        return

    from kefiya.utils.fints_controller import FinTSController, TanInteractionRequired

    if isinstance(values, str):
        values = frappe.parse_json(values)

    tan_mode = None

    if values.get("possible_tan_modes") and values.get("tan_mode") and isinstance(values["possible_tan_modes"], list) and isinstance(values["tan_mode"], str):
        tan_mode = values["tan_mode"]

    tan_medium = values.get("tan_medium") if tan_mode else None

    try:
        if values.get("mfa_confirmation"):
            # for tan generators, the TAN is permitted here (may also be empty for Mobile TAN 2.0)
            FinTSController(fints_login, {"docname": fints_login, "enabled": True}, tan_mode=tan_mode, tan_medium=tan_medium, tan=values.get("tan"))
        else:
            # get index of tan_mode in possible_tan_modes
            FinTSController(fints_login, {"docname": fints_login, "enabled": True}, tan_mode=tan_mode, tan_medium=tan_medium)
    except TanInteractionRequired:
        # will have triggered user interaction via socket
        pass
