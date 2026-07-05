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

DOCTYPE = "Kefiya Security Holding"


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
    :return: dict with created / updated counts
    """
    login = frappe.get_doc("Kefiya Login", kefiya_login)
    company = login.company
    now = now_datetime()

    created = updated = 0
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
            key = compute_holding_key(account, isin, valuation_date)
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
                "fetched_on": now,
            }

            existing = frappe.db.get_value(DOCTYPE, {"reference_key": key}, "name")
            if existing:
                doc = frappe.get_doc(DOCTYPE, existing)
                doc.update(values)
                doc.save(ignore_permissions=True)
                updated += 1
            else:
                doc = frappe.get_doc(dict(doctype=DOCTYPE, reference_key=key, **values))
                doc.insert(ignore_permissions=True)
                created += 1
        except Exception:
            # one malformed holding must not drop the whole snapshot
            frappe.log_error(
                title="Kefiya security holding upsert failed",
                message="{0}\n\n{1}".format(isin, frappe.get_traceback()),
            )

    return {"created": created, "updated": updated}
