# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""PayPal movements as Bank Transactions -- through PayPal's REST API.

PayPal offers no FinTS, so a Kefiya Login cannot reach it. Instead each
PayPal account gets a Kefiya PayPal Account: the Bank Account it books to,
and the Client ID and Secret of an app created in PayPal's developer portal
with the "Transaction Search" permission. What a transaction becomes is
decided in paypal_rows; this module asks and writes.

Run daily by the scheduler and on demand from the form. Each run starts a
few days before where the last one ended: PayPal makes a transaction
searchable up to three hours after it happened, and a window that ended
"now" would miss those for good. What comes twice is recognised by its
transaction id and skipped.

Bookings are created as DRAFTS, like every other import
(statement_import.create_booking): submitting is an approval.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from kefiya.utils import paypal_rows

LIVE = "https://api-m.paypal.com"
SANDBOX = "https://api-m.sandbox.paypal.com"

#: How far each run reaches back past the end of the last one.
OVERLAP_DAYS = 3

#: PayPal's page size limit for transaction search.
PAGE_SIZE = 500

#: Pages per window before giving up -- a guard against a loop, not a limit
#: anybody should reach (500 x 50 transactions in 31 days).
MAX_PAGES = 50


class PayPalError(frappe.ValidationError):
    pass


def _base(doc):
    return SANDBOX if doc.sandbox else LIVE


def _token(doc):
    """An OAuth access token for this app. Errors never quote the secret."""
    import requests

    try:
        response = requests.post(
            _base(doc) + "/v1/oauth2/token",
            auth=(doc.client_id or "", doc.get_password("client_secret") or ""),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
            timeout=(15, 60))
    except Exception as exc:
        raise PayPalError(_("PayPal could not be reached: {0}").format(
            type(exc).__name__)) from None
    if response.status_code >= 400:
        raise PayPalError(_(
            "PayPal refused the credentials ({0}). Check Client ID and"
            " Secret, and whether the app is a Live app.").format(
                response.status_code)) from None
    return (response.json() or {}).get("access_token")


def _search(doc, token, start, end, page):
    import requests

    try:
        response = requests.get(
            _base(doc) + "/v1/reporting/transactions",
            params={"start_date": paypal_rows.as_iso(start),
                    "end_date": paypal_rows.as_iso(end),
                    "fields": "all", "page_size": PAGE_SIZE, "page": page},
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/json"},
            timeout=(15, 120))
    except Exception as exc:
        raise PayPalError(_("PayPal could not be reached: {0}").format(
            type(exc).__name__)) from None
    if response.status_code == 403:
        raise PayPalError(_(
            "PayPal does not allow this app to search transactions. Enable"
            " \"Transaction Search\" for the app in the PayPal developer"
            " portal.")) from None
    if response.status_code >= 400:
        detail = (response.text or "").strip().replace("\n", " ")[:300]
        raise PayPalError(_("PayPal answered {0}: {1}").format(
            response.status_code, detail)) from None
    return response.json() or {}


def fetch_account(name):
    """Fetch one PayPal account up to now. Answers a summary."""
    from kefiya.utils import statement_import

    doc = frappe.get_doc("Kefiya PayPal Account", name)
    currency = statement_import.account_currency_of(doc.bank_account) \
        or "EUR"
    company = frappe.db.get_value("Bank Account", doc.bank_account, "company")

    end = now_datetime()
    if doc.fetched_until:
        start = get_datetime(doc.fetched_until) - datetime.timedelta(
            days=OVERLAP_DAYS)
    else:
        start = get_datetime(doc.fetch_from)
    start = max(start, get_datetime(doc.fetch_from))

    token = _token(doc)
    created = skipped = 0
    for lower, upper in paypal_rows.windows(start, end):
        page = 1
        while page <= MAX_PAGES:
            answer = _search(doc, token, lower, upper, page)
            for detail in answer.get("transaction_details") or []:
                for entry in paypal_rows.rows_from(detail, currency):
                    if frappe.db.exists("Bank Transaction", {
                            "reference_number": entry["reference"]}):
                        skipped += 1
                        continue
                    statement_import.create_booking(
                        doc.bank_account, entry, entry["reference"], company)
                    created += 1
            if page >= int(answer.get("total_pages") or 1):
                break
            page += 1
        # Each window settled before the next: a failure later costs only
        # the windows after it, and the next run resumes from here.
        doc.db_set("fetched_until", upper, update_modified=False)
        frappe.db.commit()

    summary = _("{0} new, {1} already there, up to {2}").format(
        created, skipped, frappe.utils.format_datetime(end))
    doc.db_set("last_run_on", now_datetime(), update_modified=False)
    doc.db_set("last_run_summary", summary, update_modified=False)
    return {"created": created, "skipped": skipped, "summary": summary}


@frappe.whitelist()
def fetch_now(name):
    """The button on the form."""
    frappe.has_permission("Kefiya PayPal Account", ptype="write", doc=name,
                          throw=True)
    return fetch_account(name)


def fetch_all_accounts():
    """Daily: every enabled PayPal account, each on its own."""
    for name in frappe.get_all("Kefiya PayPal Account",
                               filters={"enabled": 1}, pluck="name"):
        try:
            fetch_account(name)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title="Kefiya: PayPal fetch failed",
                message=frappe.get_traceback(),
                reference_doctype="Kefiya PayPal Account",
                reference_name=name)
