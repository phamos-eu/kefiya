# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Fetch statement documents from a service that holds a connection we cannot.

Some card issuers -- American Express in Germany is the case at hand -- offer
no FinTS at all. Their bookings are reachable through the PSD2 account
information route, which a licensed account information service provides; but
that route carries bookings and balances and, by design, NO documents. There is
no statement PDF in PSD2. So an account can be fully up to date on its
transactions and still have not one statement filed against it.

A document service that already holds the portal connection has exactly that
missing half. This module fetches from one, in this app, on a schedule -- no
external workflow engine in between, because there is nothing here that needs
one: it is one HTTPS call per document and the decision what to do with it
belongs on this side anyway.

What lands where is deliberately unchanged from a FinTS fetch: the document is
attached to the BANK ACCOUNT as a private file under a deterministic name, so
handing the same statement over twice is recognised rather than filed twice.
See fetch_persistence.download_statements(), which does the same for the banks
that do speak FinTS.

Two things this module is careful about:

  * The API key is read at the moment of the call and goes nowhere else. It
    lives in a Password field -- either here or, where the instance already
    keeps its API credentials, in that place instead, so a rotated key is
    rotated once. No code path puts it into a message, a summary or the Error
    Log; a traceback quoting the request body would otherwise publish the key
    to everyone who may read an error.
  * A run writes nothing until it is asked to. The default is a dry run that
    reports what it found.
"""

import base64
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime, today

from kefiya.utils import statement_import

SETTINGS = "Kefiya Document Service"

#: Response field names differ between providers and versions, and the
#: published client for this one documents the REQUEST shape only. So every
#: field is looked for under the names it plausibly carries, and probe() below
#: reports what actually arrived rather than leaving anyone to guess.
_ID_KEYS = ("prim_uid", "document_prim_uid", "documentPrimUid", "id", "uid")
_CONTENT_KEYS = ("file_content", "fileContent", "content", "document", "file")
_NAME_KEYS = ("file_name", "fileName", "filename", "document_number",
              "documentNumber", "name")
_DATE_KEYS = ("document_date", "documentDate", "date", "created")


def _credentials(doc):
    """Endpoint and key -- from here, or from wherever this instance keeps
    its API credentials already.

    An instance that has a place for API credentials should not grow a second
    one just because this app arrived. A key kept in two places is a key that
    gets rotated in one of them, and the failure then looks like the service
    is down.

    So the settings say WHERE to read rather than holding the value: a
    DocType, optionally a row in one of its child tables picked by a field
    value, and the name of the Password field. Which DocType that is, is a
    fact about the instance and stays in its configuration -- this app is not
    written against anybody's particular setup.

    :return: (base_url, api_key)
    """
    source = doc.credential_source or "Inline"
    base = (doc.base_url or "").strip()

    if source == "Inline":
        return base, doc.get_password("api_key")

    if not doc.credential_doctype:
        frappe.throw(_("No credential DocType is configured."))

    meta = frappe.get_meta(doc.credential_doctype)
    if meta.issingle:
        holder = frappe.get_single(doc.credential_doctype)
    else:
        if not doc.credential_docname:
            frappe.throw(_("No credential document is named."))
        holder = frappe.get_doc(doc.credential_doctype, doc.credential_docname)

    if doc.credential_child_table:
        rows = holder.get(doc.credential_child_table) or []
        field = doc.credential_match_field or "service"
        wanted = str(doc.credential_match_value or "").strip()
        holder = next(
            (r for r in rows
             if str(r.get(field) or "").strip() == wanted), None)
        if holder is None:
            # Named rather than silently falling back to an empty key: an
            # empty key produces an authentication error at the service,
            # which reads like the key is wrong rather than absent.
            frappe.throw(_(
                "No credential row where {0} is {1}.").format(field, wanted))
        # A row that is switched off is a decision, not an oversight.
        if "enabled" in (holder.as_dict() or {}) and not holder.get("enabled"):
            frappe.throw(_("The credential row {0} is not active.").format(
                wanted))

    if doc.credential_url_field:
        base = str(holder.get(doc.credential_url_field) or base).strip()

    return base, holder.get_password(doc.credential_token_field or "api_token")


def _settings():
    doc = frappe.get_single(SETTINGS)
    if not doc.enabled:
        frappe.throw(_("The document service is switched off."))

    base, key = _credentials(doc)
    if not key:
        frappe.throw(_("No API key is configured for the document service."))

    base = (base or "").rstrip("/")
    if not base.startswith("https://"):
        # An API key travels in the body of every one of these calls. Over
        # plain http it travels in the clear.
        frappe.throw(_("The base URL must be an https address."))
    return doc, base, key


def _call(path, payload=None):
    """One POST against the service, with the key added here and nowhere else.

    Errors are re-raised WITHOUT the request body. The body carries the API
    key, and a traceback quoting it would put the key into the Error Log,
    which is far more broadly readable than the settings document it came
    from.
    """
    import requests

    _doc, base, key = _settings()
    body = dict(payload or {})
    body["api_key"] = key

    try:
        response = requests.post(
            base + path, json=body,
            timeout=(15, 120),
            headers={"Content-Type": "application/json"})
    except Exception as exc:
        # Nothing came back at all -- DNS, TLS, timeout. There is no response
        # to quote, and the request must not be quoted: it carries the key.
        raise DocumentServiceError(
            _("The document service could not be reached ({0}): {1}")
            .format(path, type(exc).__name__)) from None

    if response.status_code >= 400:
        # The STATUS and the RESPONSE are what make this diagnosable, and
        # neither contains the key -- the key travels in the request. The
        # first version of this reported only "HTTPError", which is the same
        # sentence for a wrong key, a wrong path and a retired API version;
        # the dialog then had nothing to show and stayed silent.
        detail = (response.text or "").strip().replace("\n", " ")[:300]
        raise DocumentServiceError(
            _("The document service answered {0} on {1}: {2}").format(
                response.status_code, path, detail or _("no detail given"))
        ) from None

    try:
        return response.json()
    except Exception:
        raise DocumentServiceError(
            _("The document service answered {0} on {1}, but not in JSON: {2}")
            .format(response.status_code, path,
                    (response.text or "").strip()[:200])) from None


class DocumentServiceError(frappe.ValidationError):
    pass


def _pick(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _documents_in(response):
    """The list of documents inside a response, whatever it is called.

    Providers wrap their payload differently ("documents", "data", "result").
    Rather than hard-code one, the first list of dicts wins -- and if there is
    none, that is reported instead of read as "nothing to do".
    """
    if isinstance(response, list):
        return response
    if not isinstance(response, dict):
        return None
    for key in ("documents", "data", "result", "items", "invoices"):
        value = response.get(key)
        if isinstance(value, list):
            return value
    for value in response.values():
        if isinstance(value, list) and (not value or isinstance(value[0], dict)):
            return value
    return None


@frappe.whitelist()
def test_connection():
    """Ask the service whether the key works. Fetches nothing."""
    frappe.only_for("System Manager")
    response = _call("/apiStatus")
    return {"ok": True, "response": response}


@frappe.whitelist()
def probe(limit=1):
    """Report the FIELD NAMES the service actually returns, not their values.

    The published client for this service documents the request shape only, so
    the names a document carries cannot be known from here in advance. Rather
    than guess in code and fail silently on the first real run, this asks for
    one document and reports which keys came back -- enough to confirm or
    correct the mapping in one step, without any account data leaving the
    site: the values are not returned, only the names and their types.
    """
    frappe.only_for("System Manager")
    doc, _base, _key = _settings()

    response = _call("/listDocuments", _list_filters(doc, None))
    documents = _documents_in(response)
    if documents is None:
        return {"ok": False,
                "reason": _("no list of documents found in the response"),
                "top_level_keys": sorted(response.keys())
                if isinstance(response, dict) else None}

    sample = documents[:cint(limit) or 1]
    return {
        "ok": True,
        "count": len(documents),
        "top_level_keys": sorted(response.keys())
        if isinstance(response, dict) else None,
        # Names and types only -- deliberately not the values.
        "document_keys": [
            {k: type(v).__name__ for k, v in entry.items()}
            for entry in sample if isinstance(entry, dict)
        ],
        "recognised": [
            {
                "id": _pick(entry, _ID_KEYS) is not None,
                "name": _pick(entry, _NAME_KEYS) is not None,
                "date": _pick(entry, _DATE_KEYS) is not None,
            }
            for entry in sample if isinstance(entry, dict)
        ],
    }


def _list_filters(doc, account_row):
    filters = {}
    if doc.document_type_filter:
        filters["document_type_filter"] = doc.document_type_filter
    if cint(doc.lookback_days):
        filters["start_date_filter"] = add_days(
            today(), -abs(cint(doc.lookback_days)))
        filters["end_date_filter"] = today()
    if account_row is not None and account_row.external_id:
        filters["company_filter"] = account_row.external_id
    return filters


def _statement_name(entry, index):
    """A deterministic file name, so the same statement is recognised again."""
    name = _pick(entry, _NAME_KEYS)
    date = _pick(entry, _DATE_KEYS)
    identifier = _pick(entry, _ID_KEYS)
    parts = [str(p) for p in (date, name or identifier or index) if p]
    stem = "-".join(parts) or "Kontoauszug-{0}".format(index)
    if not stem.lower().endswith((".pdf", ".txt", ".csv", ".xml")):
        stem += ".pdf"
    return "Kontoauszug-" + stem


@frappe.whitelist()
def fetch_statements(dry_run=1):
    """Fetch the statements for every configured account.

    :param dry_run: 1 = report what would be filed, attach nothing
    :return: {"accounts": [...], "found": int, "stored": int,
        "already_present": int, "failed": int, "dry_run": bool}
    """
    frappe.only_for("System Manager")
    doc, _base, _key = _settings()
    dry = bool(cint(dry_run))

    summary = {"accounts": [], "found": 0, "stored": 0,
               "already_present": 0, "failed": 0, "dry_run": dry}

    rows = [r for r in (doc.accounts or []) if r.enabled and r.bank_account]
    if not rows:
        summary["reason"] = _(
            "No account is configured. A statement with no bank account has"
            " nowhere to go.")
        return summary

    budget = cint(doc.max_documents_per_run) or 50

    for row in rows:
        entry_summary = {"bank_account": row.bank_account, "found": 0,
                         "stored": 0, "already_present": 0, "failed": 0}
        try:
            response = _call("/listDocuments", _list_filters(doc, row))
            documents = _documents_in(response)
            if documents is None:
                entry_summary["reason"] = _("no list of documents in the response")
                summary["accounts"].append(entry_summary)
                continue

            entry_summary["found"] = len(documents)
            summary["found"] += len(documents)

            for index, entry in enumerate(documents):
                if budget <= 0:
                    entry_summary["reason"] = _("run budget reached")
                    break
                if not isinstance(entry, dict):
                    continue

                filename = _statement_name(entry, index)
                if frappe.db.exists("File", {
                        "attached_to_doctype": "Bank Account",
                        "attached_to_name": row.bank_account,
                        "file_name": filename}):
                    entry_summary["already_present"] += 1
                    summary["already_present"] += 1
                    continue

                if dry:
                    entry_summary["stored"] += 1
                    summary["stored"] += 1
                    continue

                stored = _download_and_attach(entry, row, filename)
                budget -= 1
                if stored:
                    entry_summary["stored"] += 1
                    summary["stored"] += 1
                else:
                    entry_summary["failed"] += 1
                    summary["failed"] += 1

            if not dry:
                row.db_set("last_fetched_on", now_datetime(),
                           update_modified=False)

        except Exception as exc:
            entry_summary["failed"] += 1
            summary["failed"] += 1
            entry_summary["reason"] = str(exc)[:200]
            # The traceback of a call that failed inside _call carries no
            # request body, so this is safe to log in full.
            frappe.log_error(
                title="Kefiya document service: fetch failed",
                message=frappe.get_traceback(),
            )

        summary["accounts"].append(entry_summary)

    if not dry:
        doc.db_set("last_run_on", now_datetime(), update_modified=False)
        doc.db_set("last_run_summary", json.dumps(summary, default=str)[:2000],
                   update_modified=False)

    return summary


def _download_and_attach(entry, row, filename):
    """Fetch one document and file it against the Bank Account."""
    identifier = _pick(entry, _ID_KEYS)

    payload = _pick(entry, _CONTENT_KEYS)
    if payload is None and identifier is not None:
        # The list rarely carries the file itself; each document is a separate
        # request, which is also why a run is bounded.
        detail = _call("/getDocument", {"document_prim_uid": identifier})
        if isinstance(detail, dict):
            inner = detail.get("document")
            payload = _pick(
                inner if isinstance(inner, dict) else detail, _CONTENT_KEYS)

    if payload is None:
        return False

    if isinstance(payload, bytes):
        payload = base64.b64encode(payload).decode("ascii")
    payload = str(payload)
    # Some services hand the file over as a data URI rather than bare base64.
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]

    result = statement_import.attach_statement(
        bank_account=row.bank_account,
        filename=filename,
        content_base64=payload,
    )
    return bool(result.get("stored"))


def fetch_statements_scheduled():
    """Daily entry point. Silent and harmless while the service is off."""
    try:
        doc = frappe.get_single(SETTINGS)
    except Exception:
        return
    if not doc.enabled:
        return
    fetch_statements(dry_run=0)
