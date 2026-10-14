# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Optional automatic bank reconciliation right after a FinTS import.

Runs only when enabled in Kefiya Settings. Confidence cascade:

  Stage 1+2 - exact matches + Bank Reconciliation Rules, delegated to the
              installed ALYF Banking app (auto_reconcile_vouchers).
  Stage 3   - prepayments: a still-unreconciled *incoming* transaction whose
              payer is identifiable via its IBAN gets an on-account Payment
              Entry (advance), so a tenant paying before the invoice exists is
              assigned to the right party and allocated automatically once the
              invoice is created.

Every stage is fully guarded: reconciliation must never break the import.
"""

import frappe
from frappe.utils import getdate

# ALYF Banking: exact + rule-based auto reconcile for a date range.
ALYF_AUTO_RECONCILE = (
    "banking.klarna_kosma_integration.doctype."
    "bank_reconciliation_tool_beta.bank_reconciliation_tool_beta."
    "auto_reconcile_vouchers"
)
# ERPNext core: create a Payment Entry from a Bank Transaction and reconcile it.
CREATE_PAYMENT_ENTRY_BTS = (
    "erpnext.accounts.doctype.bank_reconciliation_tool."
    "bank_reconciliation_tool.create_payment_entry_bts"
)


def _callable(path):
    """Resolve a possibly-whitelisted function to its underlying callable."""
    fn = frappe.get_attr(path)
    return getattr(fn, "__wrapped__", fn)


def run_after_import(kefiya_login, from_date, to_date):
    """Entry point called by the FinTS controllers after a successful import.

    :param kefiya_login: Kefiya Login name
    :param from_date: imported window start
    :param to_date: imported window end
    """
    settings = frappe.get_single("Kefiya Settings")
    if not settings.auto_reconcile_after_import:
        return

    login = frappe.get_doc("Kefiya Login", kefiya_login)
    bank_account = login.bank_account
    if not bank_account:
        return

    bank_account_doc = frappe.get_doc("Bank Account", bank_account)
    company = login.company or bank_account_doc.company

    _alyf_auto_reconcile(
        company,
        bank_account_doc.bank,
        bank_account,
        from_date,
        to_date,
    )

    if settings.auto_create_advance_payments:
        _create_advance_payments(bank_account, from_date, to_date)


def _alyf_auto_reconcile(company, bank, bank_account, from_date, to_date):
    """Stage 1+2: delegate exact + rule-based matching to ALYF Banking."""
    if not (company and bank and bank_account):
        frappe.log_error(
            title="Kefiya auto-reconcile skipped: missing company/bank",
            message="company={0} bank={1} bank_account={2}".format(
                company, bank, bank_account
            ),
        )
        return

    try:
        auto_reconcile = _callable(ALYF_AUTO_RECONCILE)
    except Exception:
        # ALYF Banking not installed / method renamed -- skip silently, the
        # core import is unaffected.
        frappe.log_error(
            title="Kefiya auto-reconcile: ALYF method unavailable",
            message=frappe.get_traceback(),
        )
        return

    try:
        auto_reconcile(
            company=company,
            bank=bank,
            bank_account=bank_account,
            from_date=getdate(from_date) if from_date else None,
            to_date=getdate(to_date) if to_date else None,
            filter_by_reference_date=0,
            from_reference_date=None,
            to_reference_date=None,
        )
    except Exception:
        frappe.log_error(
            title="Kefiya auto-reconcile (ALYF) failed",
            message=frappe.get_traceback(),
        )


def _create_advance_payments(bank_account, from_date, to_date):
    """Stage 3: on-account Payment Entry for identifiable, unreconciled payers."""
    filters = {
        "bank_account": bank_account,
        "docstatus": 1,
        "unallocated_amount": [">", 0],
        # only incoming money: a customer/tenant prepayment
        "deposit": [">", 0],
    }
    if from_date and to_date:
        filters["date"] = ["between", [getdate(from_date), getdate(to_date)]]

    txns = frappe.get_all(
        "Bank Transaction",
        filters=filters,
        fields=["name", "bank_party_iban"],
    )
    if not txns:
        return

    try:
        create_payment_entry = _callable(CREATE_PAYMENT_ENTRY_BTS)
    except Exception:
        frappe.log_error(
            title="Kefiya advance payments: core method unavailable",
            message=frappe.get_traceback(),
        )
        return

    for txn in txns:
        # skip if this transaction already has any linked voucher -- avoids a
        # duplicate advance on re-import / overlap windows or partial failures.
        if frappe.db.exists("Bank Transaction Payments", {"parent": txn.name}):
            continue

        party, party_type = _identify_party(txn.bank_party_iban)
        # only customer prepayments (e.g. a tenant paying before the invoice);
        # an incoming supplier refund must not become a customer advance.
        if not party or party_type != "Customer":
            continue

        try:
            create_payment_entry(
                bank_transaction_name=txn.name,
                party_type=party_type,
                party=party,
                allow_edit=False,
            )
            # make each advance durable so a later failure cannot both keep the
            # snapshot stale and recreate it on the next run.
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title="Kefiya advance Payment Entry failed",
                message="{0}\n\n{1}".format(txn.name, frappe.get_traceback()),
            )


def _identify_party(iban):
    """Resolve (party, party_type) from a counterparty IBAN via Bank Account.

    Returns (None, None) when the IBAN is unknown or ambiguous (more than one
    distinct party shares it) -- never guess the party for an auto-created
    financial document.
    """
    if not iban:
        return None, None
    normalized = iban.replace(" ", "").upper()
    rows = frappe.get_all(
        "Bank Account",
        filters={"iban": normalized},
        fields=["party", "party_type"],
    )
    parties = {(r.party, r.party_type) for r in rows if r.party and r.party_type}
    if len(parties) == 1:
        return next(iter(parties))
    return None, None
