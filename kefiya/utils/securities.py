# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Storage layer for FinTS securities holdings (Depot / Wertpapiere).

Holdings are an informational point-in-time snapshot, never a GL posting.
``refresh_holdings`` is the source-agnostic ingest entry point; the FinTS
controllers fetch the raw holdings (get_holdings / HKWPD) and hand a list of
plain dicts to it. One row per (login, ISIN, valuation date) yields a time
series for portfolio reporting.
"""

import hashlib

import frappe
from frappe.utils import now_datetime, getdate, flt

from kefiya.utils.securities_rows import depot_fetch_due, sold_positions

DOCTYPE = "Kefiya Security Holding"
ACCOUNT_DOCTYPE = "Kefiya Securities Account"

SOURCE_BANK = "Bank"


def compute_holding_key(securities_account, isin, valuation_date):
    """Stable hash for one snapshot (per securities account, ISIN, valuation date)."""
    parts = [
        (securities_account or "").strip(),
        (isin or "").strip().upper(),
        str(getdate(valuation_date)) if valuation_date else "",
    ]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def get_or_create_securities_account(account_number, company, kefiya_login=None):
    """Return the Kefiya Securities Account (Depot) for an account number,
    creating it (owned by ``company``) if it does not exist yet."""
    if not account_number:
        return None
    name = frappe.db.get_value(
        "Kefiya Securities Account", {"account_number": account_number}, "name"
    )
    if name:
        return name
    account = frappe.get_doc({
        "doctype": "Kefiya Securities Account",
        "account_number": account_number,
        "company": company,
        "kefiya_login": kefiya_login,
    })
    account.insert(ignore_permissions=True)
    return account.name


def refresh_holdings(kefiya_login, holdings):
    """Idempotently upsert a securities snapshot for a login.

    :param kefiya_login: Kefiya Login name
    :param holdings: list of dicts with keys isin, security_name, quantity,
        price, market_value, currency, valuation_date, securities_account
    :return: dict with created / updated / closed counts
    """
    login = frappe.get_doc("Kefiya Login", kefiya_login)
    company = login.company
    now = now_datetime()

    created = updated = 0
    # Per depot: the valuation date of this snapshot and the ISINs it
    # reported. Both are needed afterwards to recognise what the bank has
    # STOPPED reporting -- see _close_sold_positions.
    snapshots = {}

    for item in holdings or []:
        isin = item.get("isin")
        valuation_date = item.get("valuation_date")
        securities_account = item.get("securities_account")
        if not isin or not valuation_date or not securities_account:
            # without a stable identity / depot we cannot store it -- skip
            continue

        try:
            account = get_or_create_securities_account(
                securities_account, company, kefiya_login
            )
            # only set the Currency link when it resolves to a real Currency,
            # otherwise a non-ISO value_symbol would abort the row.
            currency = (item.get("currency") or "").strip().upper()
            if currency and not frappe.db.exists("Currency", currency):
                currency = None
            values = {
                "kefiya_login": kefiya_login,
                "securities_account": account,
                "company": company,
                "valuation_date": getdate(valuation_date),
                "isin": isin,
                "security_name": item.get("security_name"),
                "currency": currency,
                "quantity": flt(item.get("quantity")),
                "price": flt(item.get("price")),
                "market_value": flt(item.get("market_value")),
                "acquisition_price":
                    flt(item.get("acquisition_price")) or None,
                "source": SOURCE_BANK,
                "fetched_on": now,
            }

            if _upsert_snapshot_row(values):
                created += 1
            else:
                updated += 1

            snapshot = snapshots.setdefault(account, {
                "valuation_date": values["valuation_date"], "isins": set()})
            snapshot["isins"].add(str(isin).strip().upper())
            # Reports several valuation dates in one run: the youngest is
            # this depot's state.
            if values["valuation_date"] > snapshot["valuation_date"]:
                snapshot["valuation_date"] = values["valuation_date"]
        except Exception:
            # one malformed holding must not drop the whole snapshot
            frappe.log_error(
                title="Kefiya security holding upsert failed",
                message="{0}\n\n{1}".format(isin, frappe.get_traceback()),
            )

    closed = 0
    for account, snapshot in snapshots.items():
        closed += _close_sold_positions(
            kefiya_login, company, account, snapshot, now)

    return {"created": created, "updated": updated, "closed": closed}


def _upsert_snapshot_row(values):
    """Write one snapshot row. True when it was new, False when it existed.

    The identity is (depot, ISIN, valuation date): the same day fetched
    again updates instead of adding a second row. One writer for both the
    positions the bank reports and the zero rows that close the ones it no
    longer does.
    """
    key = compute_holding_key(
        values["securities_account"], values["isin"],
        values["valuation_date"])
    existing = frappe.db.get_value(DOCTYPE, {"reference_key": key}, "name")
    if existing:
        doc = frappe.get_doc(DOCTYPE, existing)
        doc.update(values)
        doc.save(ignore_permissions=True)
        return False
    doc = frappe.get_doc(dict(doctype=DOCTYPE, reference_key=key, **values))
    doc.insert(ignore_permissions=True)
    return True


def _close_sold_positions(kefiya_login, company, account, snapshot, now):
    """Carry positions the bank no longer reports forward with quantity 0.

    A sold position otherwise just stops appearing. The last snapshot stays
    as history, and nothing says whether it was closed or whether the fetch
    came back short -- put two days side by side and a missing line is
    unreadable. So every ISIN that was held at the previous snapshot and is
    absent from this one gets a row at this valuation date with quantity 0.

    All three numbers are 0, the price included: what is no longer in the
    depot has no price for this depot, and a price left standing reads as a
    current one.

    Only rows the BANK delivered count. A holding entered by hand (source
    Manual) is somebody's own record of a depot the bank does not report;
    zeroing it would throw their work away.

    Compared against the latest EARLIER valuation date of this depot, not
    against everything ever held -- otherwise every position sold once would
    be zeroed again at every single fetch.

    Never raises: by the time this runs, the snapshot itself is stored.

    :return: how many positions were closed
    """
    try:
        previous_date = frappe.db.get_value(
            DOCTYPE,
            {"securities_account": account,
             "source": SOURCE_BANK,
             "valuation_date": ("<", snapshot["valuation_date"])},
            "valuation_date",
            order_by="valuation_date desc")
        if not previous_date:
            return 0

        held = {}
        for row in frappe.get_all(
                DOCTYPE,
                filters={"securities_account": account,
                         "source": SOURCE_BANK,
                         "valuation_date": getdate(previous_date)},
                fields=["isin", "security_name", "currency", "quantity"],
                limit_page_length=0):
            if flt(row.get("quantity")) == 0:
                # already closed -- not a second time
                continue
            held[str(row.get("isin") or "").strip().upper()] = row

        closed = 0
        for isin in sold_positions(held.keys(), snapshot["isins"]):
            row = held.get(isin) or {}
            # compute_holding_key does not know the source, so a zero row
            # would land on top of a position somebody keeps BY HAND at this
            # very date -- and the bank not reporting it is exactly why they
            # do. No row yet, or one of ours, is written; a manual one is
            # left alone.
            standing = frappe.db.get_value(
                DOCTYPE,
                {"reference_key": compute_holding_key(
                    account, isin, snapshot["valuation_date"])},
                "source")
            if standing not in (None, SOURCE_BANK):
                continue
            _upsert_snapshot_row({
                "kefiya_login": kefiya_login,
                "securities_account": account,
                "company": company,
                "valuation_date": snapshot["valuation_date"],
                "isin": row.get("isin") or isin,
                "security_name": row.get("security_name"),
                "currency": row.get("currency"),
                "quantity": 0.0,
                "price": 0.0,
                "market_value": 0.0,
                "acquisition_price": None,
                "source": SOURCE_BANK,
                "fetched_on": now,
            })
            closed += 1
        return closed
    except Exception:
        frappe.log_error(
            title="Kefiya: closing sold securities positions failed",
            message="{0}\n\n{1}".format(account, frappe.get_traceback()),
        )
        return 0


def depots_due(kefiya_login, now=None):
    """The depots hanging off ``kefiya_login`` that are due for a fetch.

    A depot is due when it is set to fetch holdings and was not tried yet
    on this calendar day (``securities_rows.depot_fetch_due``).
    """
    now = now or now_datetime()
    due = []
    for row in frappe.get_all(
            ACCOUNT_DOCTYPE,
            filters={"kefiya_login": kefiya_login, "fetch_holdings": 1},
            fields=["name", "account_number", "subaccount_number",
                    "last_holdings_fetch"]):
        last = row.get("last_holdings_fetch")
        if not depot_fetch_due(last, now):
            continue
        due.append(row)
    return due


def _note_depot_fetch(name, status, message=None):
    """Record the outcome of one depot fetch on the depot itself.

    Written with db.set_value: the depot form may be open elsewhere, and a
    status line must not collide with a user's edit of the depot.
    """
    frappe.db.set_value(ACCOUNT_DOCTYPE, name, {
        "last_holdings_fetch": now_datetime(),
        "last_holdings_status": status,
        "last_holdings_message": (message or "")[:500],
    }, update_modified=False)


def refresh_one_depot(kefiya_login, depot, controller):
    """Fetch and store the holdings of one depot, recording the outcome.

    :param kefiya_login: Kefiya Login name the depot hangs off
    :param depot: dict-like with name, account_number, subaccount_number
    :param controller: a FinTS controller instance for ``kefiya_login``
    :return: {"status", "created", "updated", "message"}
    """
    try:
        rows = controller.get_fints_depot_holdings(
            depot.get("account_number"),
            depot.get("subaccount_number") or None)
        for row in rows:
            # store under the depot record, whatever spelling the bank used
            row["securities_account"] = depot.get("account_number")
        counts = refresh_holdings(kefiya_login, rows)
        status = "OK" if rows else "Empty"
        _note_depot_fetch(depot.get("name"), status)
        return dict(status=status, message=None, **counts)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        _note_depot_fetch(depot.get("name"), "Failed", message)
        frappe.log_error(
            title="Kefiya: securities account fetch failed",
            message="{0}\n\n{1}".format(
                depot.get("name"), frappe.get_traceback()),
        )
        return {"status": "Failed", "created": 0, "updated": 0,
                "message": message}


def refresh_linked_depots(kefiya_login, controller_class):
    """Fetch and store the holdings of every due depot of a login.

    Each depot is tried on its own: one the bank refuses must not hide the
    others. A refusal is logged, not swallowed -- a depot that silently
    delivers nothing is exactly how holdings went missing unnoticed before.

    :param kefiya_login: Kefiya Login name
    :param controller_class: the FinTS controller class to use
    :return: {depot name: {"status", "created", "updated", "message"}}
    """
    return {
        depot.name: refresh_one_depot(
            kefiya_login, depot, controller_class(kefiya_login))
        for depot in depots_due(kefiya_login)
    }
