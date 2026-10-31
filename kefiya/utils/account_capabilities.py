# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What the bank lets you do on THIS account -- read from the bank, not guessed.

At logon a bank sends HIUPD, one segment per account, and inside it the list of
business transactions it will accept for that account: HKCCS for a transfer,
HKCSE for a dated one, HKCDB to read the standing orders, HKWPD for a
portfolio, and so on. Two accounts of the same customer at the same bank
routinely differ -- a savings account takes no transfer at all, a Tagesgeld
account often allows a transfer only back to the reference account, and a
guarantee line accepts nothing.

Without that list every button is offered everywhere, and the account that
cannot do the thing says so only after the order was written, approved, and a
TAN was spent on it. The bank's refusal arrives as a FinTS return code in the
middle of a send, which is the worst possible moment and the least readable
place to learn that this account was never able to do it.

So the list is read at every fetch and kept on the Bank Account. Three rules
hold everywhere it is used:

  * The bank is the source. What is stored is what the bank said, per account,
    with the segment code it used.
  * Silence is not permission and not refusal. An account nobody has fetched
    yet has no rows at all, and that is answered with "unknown" -- which lets
    everything through exactly as before this module existed. Only a fetched
    list that does NOT name a transaction refuses it.
  * The refusal happens before the TAN, not after it.

FinTS segments, for the ones this app can actually issue:

    HKCCS / HKCCM   SEPA transfer, single / collective
    HKCSE / HKCME   dated (terminierte) transfer, single / collective
    HKIPZ / HKIPM   instant payment (SEPA Instant), single / collective
    HKCDB           read the standing orders
    HKCDE/N/L       create / change / delete a standing order
    HKDSE / HKDME   SEPA direct debit, single / collective
    HKDBS / HKDMB   read the dated direct debits
    HKKAZ / HKCAZ   transactions, MT940 / camt XML
    HKSAL           balance
    HKWPD           portfolio
    HKEKA / HKEKP   electronic statement, data / PDF
    HKPRO           status protocol
    DKKKU           credit-card transactions
"""

import time

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

#: Where the answer is kept. Both are Custom Fields on Bank Account, declared
#: in kefiya/setup/install.py.
FIELD_TABLE = "custom_fints_capabilities"
FIELD_CHECKED_ON = "custom_capabilities_checked_on"

CHILD_DOCTYPE = "Kefiya Account Capability"

#: Every business transaction this app can issue or read, in the order a
#: person would look for them. `key` is what the code asks for and never
#: changes; `segments` are the FinTS codes that satisfy it -- more than one
#: where the bank may name either.
CATALOGUE = (
    ("transfer", ("HKCCS",), "Transfer"),
    ("transfer_collective", ("HKCCM",), "Collective transfer"),
    ("scheduled_transfer", ("HKCSE",), "Scheduled transfer"),
    ("scheduled_transfer_collective", ("HKCME",), "Scheduled collective transfer"),
    ("instant_transfer", ("HKIPZ",), "Instant payment"),
    ("instant_transfer_collective", ("HKIPM",), "Collective instant payment"),
    ("standing_order_read", ("HKCDB",), "Read standing orders"),
    ("standing_order_create", ("HKCDE",), "Create standing order"),
    ("standing_order_change", ("HKCDN",), "Change standing order"),
    ("standing_order_delete", ("HKCDL",), "Delete standing order"),
    ("scheduled_transfer_read", ("HKCSB",), "Read scheduled transfers"),
    ("direct_debit", ("HKDSE", "HKDSC"), "Direct debit"),
    ("direct_debit_collective", ("HKDME", "HKDMC"), "Collective direct debit"),
    ("scheduled_debit_read", ("HKDBS", "HKDMB"), "Read scheduled direct debits"),
    ("transactions", ("HKKAZ",), "Transactions"),
    ("transactions_xml", ("HKCAZ",), "Transactions as camt XML"),
    ("balance", ("HKSAL",), "Balance"),
    ("holdings", ("HKWPD",), "Portfolio"),
    ("statements", ("HKEKA", "HKEKP"), "Electronic statements"),
    ("status_protocol", ("HKPRO",), "Status protocol"),
    ("credit_card", ("DKKKU",), "Credit-card transactions"),
)

#: key -> segments, built once.
SEGMENTS_BY_KEY = {row[0]: row[1] for row in CATALOGUE}
LABEL_BY_KEY = {row[0]: row[2] for row in CATALOGUE}

#: segment -> key, for naming a segment the bank sent.
#: The loop variables are named _cat_* on purpose: a plain `_label` here would
#: leak into the module and collide with the function of that name below. It
#: happens to work -- the def comes later and wins -- but a reader should not
#: have to check the order of two unrelated lines to know what `_label` is.
KEY_BY_SEGMENT = {}
for _cat_row in CATALOGUE:
    for _cat_segment in _cat_row[1]:
        KEY_BY_SEGMENT[_cat_segment] = _cat_row[0]

ALLOWED = "allowed"
REFUSED = "refused"
UNKNOWN = "unknown"


def normalize_segment(value):
    """A FinTS segment code as five upper-case letters.

    Banks and the library alike sometimes carry a version behind the code
    (``HKCCS1``); the transaction is the same one either way.
    """
    return (str(value or "").strip().upper())[:5]


# --------------------------------------------------------------------------
# Reading it from the bank
# --------------------------------------------------------------------------

def read_from_connection(connection):
    """The allowed transactions per account, straight off HIUPD.

    Kept separate from the controller so it can be exercised against a plain
    object with an ``upd`` -- no dialog, no bank, no TAN.

    :return: list of {"iban", "account_number", "subaccount", "segments"},
        where segments maps a five-letter code to what the bank said about it.
        Empty when the bank sent no HIUPD, which means unknown, NOT nothing
        allowed.
    """
    upd = getattr(connection, "upd", None)
    if not upd or not getattr(upd, "segments", None):
        return []

    accounts = []
    for seg in upd.find_segments("HIUPD"):
        info = getattr(seg, "account_information", None)
        segments = {}
        for allowed in (getattr(seg, "allowed_transactions", None) or []):
            code = normalize_segment(getattr(allowed, "transaction", None))
            if not code:
                continue
            limit = getattr(allowed, "limit_amount", None)
            segments[code] = {
                "required_signatures": getattr(
                    allowed, "required_signatures", None),
                "limit_type": _as_text(getattr(allowed, "limit_type", None)),
                "limit_amount": _as_amount(getattr(limit, "amount", None)),
                "limit_days": getattr(allowed, "limit_days", None),
            }

        accounts.append({
            "iban": _clean_iban(getattr(seg, "iban", None)),
            "account_number": _as_text(
                getattr(info, "account_number", None) if info else None),
            "subaccount": _as_text(
                getattr(info, "subaccount_number", None) if info else None),
            "segments": segments,
        })
    return accounts


def _as_text(value):
    return None if value is None else str(value)


def _as_amount(value):
    if value is None:
        return None
    try:
        return flt(value)
    except (TypeError, ValueError):
        return None


def _clean_iban(value):
    return (str(value or "")).replace(" ", "").upper() or None


# --------------------------------------------------------------------------
# Writing it onto the Bank Account
# --------------------------------------------------------------------------

def refresh_account_capabilities(kefiya_login, controller=None):
    """Read the list from the bank and keep it on the Bank Accounts it names.

    A login usually covers several accounts, and what the bank says holds for
    the ACCOUNT, not for the login that happened to ask. So every account the
    bank names is matched to its Bank Account by IBAN and written there; the
    login's own account is matched by its link as well, for the case of a bank
    that sends no IBAN on HIUPD.

    :return: {"stored": [names], "skipped": [...], "checked_on": ...}
    """
    if controller is None:
        from kefiya.utils.fints_controller import FinTSController

        controller = FinTSController(kefiya_login)

    rows = controller.get_fints_account_capabilities() or []
    login = frappe.get_doc("Kefiya Login", kefiya_login)

    if not rows:
        # "The bank named nothing" and "we never asked" must not look the same
        # on the document, so the attempt is recorded even when it was empty.
        return {"stored": [], "skipped": [{"reason": _("the bank sent no HIUPD")}],
                "checked_on": None}

    stored, skipped = [], []
    for row in rows:
        account = _match_bank_account(row, login)
        if not account:
            skipped.append({
                "iban": _mask(row.get("iban")),
                "reason": _("no bank account with this IBAN"),
            })
            continue
        if store_for_account(account, row.get("segments") or {}):
            stored.append(account)
        else:
            skipped.append({"bank_account": account,
                            "reason": _("capability fields not installed")})

    return {"stored": stored, "skipped": skipped,
            "checked_on": str(now_datetime())}


def _mask(iban):
    """An IBAN in a log or a message: the last four digits and nothing else."""
    iban = _clean_iban(iban)
    if not iban:
        return None
    return "..." + iban[-4:]


def _match_bank_account(row, login):
    """The Bank Account this HIUPD segment is about, or None."""
    iban = _clean_iban(row.get("iban"))
    if iban:
        for name, other in frappe.get_all(
                "Bank Account",
                filters={"iban": ["is", "set"]},
                fields=["name", "iban"], as_list=True):
            if _clean_iban(other) == iban:
                return name

    # A bank that sends no IBAN on HIUPD still sends the account number, and
    # for the login's own account we know which record that is.
    own = _clean_iban(login.get("account_iban"))
    if login.get("bank_account") and (not iban or (own and own == iban)):
        return login.bank_account
    return None


def store_for_account(bank_account, segments):
    """Replace the stored list on one Bank Account.

    Every catalogue entry gets a row, ticked or not, so the form answers "may
    this account do a dated transfer?" with yes or no rather than with silence.
    A segment the bank named that this app does not know gets a row too, under
    its own code: it is what the bank said, and dropping it would hide it.

    :return: True when written, False where the fields are not installed.
    """
    doc = frappe.get_doc("Bank Account", bank_account)
    if not doc.meta.has_field(FIELD_TABLE):
        return False

    desired = _rows_for(segments)

    # Replacing the table gives every row a new name, and a Bank Account with
    # tracked changes records that as a change -- once per account per fetch,
    # for a list that hardly ever moves. So it is only rewritten when it says
    # something different; when the bank repeats itself, only the date does.
    changed = not _same_rows(doc.get(FIELD_TABLE), desired)

    # ... and when only the date moves, it is written as a field, not through a
    # save. A save runs Bank Account.validate(), whose
    # update_default_bank_account() clears is_default across every account of
    # the company in one UPDATE -- a range lock, taken here for a timestamp.
    # The bank repeats its capability list on nearly every fetch, so this was
    # the common case: dozens of range locks per collective run, on rows the
    # write never meant to touch. Two accesses fetched side by side then took
    # them in opposite order and MariaDB killed one with 1213 ("Deadlock
    # found") -- five accounts in a single observed run.
    if not changed:
        if doc.meta.has_field(FIELD_CHECKED_ON):
            # Only the "last checked" stamp: no field the form validates, and
            # not worth a version entry either.
            doc.db_set(FIELD_CHECKED_ON, now_datetime(), update_modified=False)
        return True

    # The list itself changed, so the child table has to be written and that
    # needs the document lifecycle. Rare -- a bank changes what it allows on an
    # account maybe twice a year -- but it is the one path that can still meet
    # the range lock above, so it is the one path that retries.
    _write_rows_through_deadlock(bank_account, desired)
    return True


def _write_rows_through_deadlock(bank_account, desired, attempts=3):
    """Write the capability list onto one Bank Account, giving way when
    another fetch holds the lock.

    A deadlock is not a broken document, it is two writers that arrived in an
    unlucky order -- MariaDB picks one, rolls its statement back and reports
    1213 to it. The losing side's correct answer is to try again, not to give
    up: giving up is what left an account without its capability list for a
    whole day.

    The document is re-read on every attempt rather than re-saved from memory.
    A save that was rolled back leaves the in-memory copy holding child rows
    the database no longer has and a `modified` stamp it never got, so saving
    that same object again invites a timestamp mismatch on top of the deadlock.
    Reading it back costs one query and starts from what is actually stored.

    Two things this deliberately does NOT do:

      * It does not call frappe.db.rollback(). That would discard the whole
        transaction -- the transactions this fetch imported above all -- to fix
        one account's capability list.
      * It does not swallow the last failure. After the final attempt the
        exception travels on to _optional_fetch, which records the account as
        failed and logs it, exactly as before.
    """
    for attempt in range(attempts):
        doc = frappe.get_doc("Bank Account", bank_account)
        doc.set(FIELD_TABLE, [])
        for row in desired:
            doc.append(FIELD_TABLE, row)
        if doc.meta.has_field(FIELD_CHECKED_ON):
            doc.set(FIELD_CHECKED_ON, now_datetime())

        try:
            doc.save(ignore_permissions=True)
            return
        except frappe.QueryDeadlockError:
            if attempt == attempts - 1:
                raise
            # Short and rising, so the two writers do not walk back into each
            # other on the next attempt. The winner needs a fraction of a
            # second to finish; anything longer would hold the bank dialog
            # open waiting on a timestamp.
            time.sleep(0.2 * (attempt + 1))


#: The fields that carry meaning. `business_transaction` is left out on
#: purpose: it is a label, it changes with the reader's language, and a German
#: session must not rewrite every account a English one just wrote.
COMPARED = ("capability", "transaction", "allowed", "required_signatures",
            "limit_type", "limit_amount", "limit_days")


def _label(text):
    """The label in the site's language, not the session's.

    A fetch normally runs as a background job, and a background job has no
    user and therefore no language: `_()` would fall back to English and
    write English labels onto a German site. They would stay English, too --
    a later fetch only rewrites the table when something MEANINGFUL changed,
    and the label is deliberately not part of that comparison.

    So the language is taken from the site rather than from whoever (or
    whatever) happens to be running.
    """
    try:
        lang = frappe.db.get_single_value("System Settings", "language")
    except Exception:
        lang = None
    if not lang:
        return _(text)
    try:
        return frappe._(text, lang=lang)
    except TypeError:
        # Older signatures take no lang; the session's language is then the
        # best available answer, and still better than failing.
        return _(text)


def _rows_for(segments):
    """The full list for one account: the catalogue, then anything extra."""
    segments = {normalize_segment(k): (v or {}) for k, v in
                (segments or {}).items()}

    rows = []
    for key, codes, label in CATALOGUE:
        code = next((c for c in codes if c in segments), None)
        detail = segments.get(code, {}) if code else {}
        rows.append({
            "capability": key,
            # Display only. The code never compares against this -- it asks by
            # `capability` or by `transaction` -- so translating it here gives
            # the reader German without turning stored data into a language.
            "business_transaction": _label(label),
            "transaction": code or codes[0],
            "allowed": 1 if code else 0,
            "required_signatures": cint(detail.get("required_signatures")),
            "limit_type": detail.get("limit_type"),
            "limit_amount": detail.get("limit_amount"),
            "limit_days": cint(detail.get("limit_days")),
        })

    # A transaction the bank named that this app has no name for. Kept under
    # its own code: it is what the bank said, and dropping it would hide it.
    for code in sorted(segments):
        if code in KEY_BY_SEGMENT:
            continue
        detail = segments[code]
        rows.append({
            "capability": "",
            "business_transaction": _label("Other business transaction"),
            "transaction": code,
            "allowed": 1,
            "required_signatures": cint(detail.get("required_signatures")),
            "limit_type": detail.get("limit_type"),
            "limit_amount": detail.get("limit_amount"),
            "limit_days": cint(detail.get("limit_days")),
        })
    return rows


def _same_rows(stored, desired):
    """Does the stored list already say this?"""
    stored = list(stored or [])
    if len(stored) != len(desired):
        return False
    for old, new in zip(stored, desired):
        for field in COMPARED:
            old_value = old.get(field) if isinstance(old, dict) \
                else getattr(old, field, None)
            if field in ("allowed", "required_signatures", "limit_days"):
                if cint(old_value) != cint(new.get(field)):
                    return False
            elif field == "limit_amount":
                if flt(old_value) != flt(new.get(field)):
                    return False
            elif (old_value or None) != (new.get(field) or None):
                return False
    return True


# --------------------------------------------------------------------------
# Asking about it
# --------------------------------------------------------------------------

def stored_rows(bank_account):
    """The list as it stands on one Bank Account. Empty means never asked.

    Answers with an empty list rather than raising, whatever goes wrong. This
    gate is an improvement on top of sending money, not a part of it: a
    transfer that would have gone out before this module existed must still go
    out when the module cannot answer. An empty list reads as "unknown", which
    blocks nothing -- and the failure is recorded rather than swallowed.
    """
    if not bank_account:
        return []

    # The kwarg that names the owning doctype is `parent_doctype`, not
    # `parent`: get_all() pops the former and passes anything else on to
    # DatabaseQuery.execute(), which has no `parent` -- so the wrong spelling
    # does not query the wrong thing, it raises TypeError. That mattered here
    # because this function sits in the send path: every transfer would have
    # died on it, and the traceback would have named a report query rather
    # than the gate that asked.
    # required_signatures belongs in this list, and its absence was not a
    # cosmetic omission.
    #
    # required_signatures() reads it to decide whether a dialog that ended
    # WITHOUT a TAN can have moved money. The field was never selected, so the
    # row came back with it unset, cint(None) made that a 0, and 0 reads as
    # "no signature required" -- which is the one reading that turns a missing
    # TAN into a successful payment. The guard built to stop exactly that has
    # therefore never fired once, on any account, and two transfers were
    # marked "Sent" that the bank never executed.
    #
    # 49 stored transfer capabilities on this instance demand a signature.
    try:
        return frappe.get_all(
            CHILD_DOCTYPE,
            parent_doctype="Bank Account",
            filters={"parenttype": "Bank Account", "parent": bank_account,
                     "parentfield": FIELD_TABLE},
            fields=["capability", "transaction", "allowed",
                    "required_signatures"],
            limit_page_length=0)
    except Exception:
        frappe.log_error(
            title="Kefiya: reading the account capabilities failed",
            message=frappe.get_traceback())
        return []


def verdict(bank_account, capability, rows=None):
    """ALLOWED, REFUSED or UNKNOWN for one capability on one account.

    UNKNOWN is the answer whenever there is nothing to go on: no account, no
    stored list, or a list that predates this capability. It is deliberately
    distinct from REFUSED -- see the module docstring.

    :param rows: the account's stored list, where the caller already has it --
        asking about twenty transactions is one query, not twenty.
    """
    if not bank_account:
        return UNKNOWN

    rows = stored_rows(bank_account) if rows is None else rows
    if not rows:
        return UNKNOWN

    codes = set(SEGMENTS_BY_KEY.get(capability, ()))
    for row in rows:
        matches = (row.get("capability") == capability
                   or normalize_segment(row.get("transaction")) in codes)
        if matches:
            return ALLOWED if cint(row.get("allowed")) else REFUSED

    # The account has a list, but this capability is not in it -- an older
    # list written before the catalogue knew this transaction. Nothing can be
    # concluded from a question that was never asked.
    return UNKNOWN


def supports(bank_account, capability):
    """May this be offered and attempted? Everything but an explicit refusal."""
    return verdict(bank_account, capability) != REFUSED


def refuses(bank_account, capability):
    """Did the bank say this account cannot do it?"""
    return verdict(bank_account, capability) == REFUSED


def required_signatures(bank_account, capability, rows=None):
    """How many signatures the bank demands for this transaction.

    HIUPD states this per account and per business transaction, and for a
    credit transfer the answer is one: the TAN. It is the number that decides
    whether a dialog that ended WITHOUT a TAN can have moved money.

    None where nothing is stored. An unknown answer must never be read as
    "none required" -- that is the reading that turns a missing TAN into a
    successful payment.

    :return: int, or None when the account has no stored list for this
    """
    if not bank_account:
        return None

    rows = stored_rows(bank_account) if rows is None else rows
    if not rows:
        return None

    codes = set(SEGMENTS_BY_KEY.get(capability, ()))
    for row in rows:
        matches = (row.get("capability") == capability
                   or normalize_segment(row.get("transaction")) in codes)
        if matches:
            return cint(row.get("required_signatures"))
    return None


def required_capability(payment_count=1, scheduled=False, instant=False):
    """Which business transaction an outgoing order needs.

    The four questions the bank distinguishes: one payment or several, now or
    on a date, normal or instant.
    """
    if instant:
        return ("instant_transfer_collective" if payment_count > 1
                else "instant_transfer")
    if scheduled:
        return ("scheduled_transfer_collective" if payment_count > 1
                else "scheduled_transfer")
    return "transfer_collective" if payment_count > 1 else "transfer"


def refusal_message(bank_account, capability):
    """Why this cannot be sent, in words that name the account and the deed."""
    account_name = frappe.db.get_value(
        "Bank Account", bank_account, "account_name") or bank_account
    return _(
        "The bank does not allow \"{0}\" on the account {1}. That is what it"
        " sent for this account at logon, so the order would be refused after"
        " the TAN rather than before it. Use an account that allows it, or"
        " have the bank enable it."
    ).format(_(LABEL_BY_KEY.get(capability, capability)), account_name)


# --------------------------------------------------------------------------
# For the user interface
# --------------------------------------------------------------------------

@frappe.whitelist()
def get_capabilities(bank_account=None, kefiya_login=None):
    """What to show and what to hide, for one account.

    Answers with every catalogue key mapped to "allowed", "refused" or
    "unknown". A form hides only what is refused: hiding the unknown would
    empty the screen of every account that has not been fetched yet.
    """
    if not bank_account and kefiya_login:
        bank_account = frappe.db.get_value(
            "Kefiya Login", kefiya_login, "bank_account")
    if bank_account:
        frappe.has_permission("Bank Account", doc=bank_account, throw=True)

    checked_on = None
    if bank_account:
        meta = frappe.get_meta("Bank Account")
        if meta.has_field(FIELD_CHECKED_ON):
            checked_on = frappe.db.get_value(
                "Bank Account", bank_account, FIELD_CHECKED_ON)

    rows = stored_rows(bank_account)
    return {
        "bank_account": bank_account,
        "checked_on": checked_on,
        "capabilities": {key: verdict(bank_account, key, rows=rows)
                         for key, _segments, _label in CATALOGUE},
    }
