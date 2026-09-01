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
  Stage 4   - our own transfers: an *outgoing* transaction carrying the
              End-To-End ID of a transfer we sent is not a puzzle to be solved
              by amount and date. We know what it paid, so it is booked against
              that voucher.

Every stage is fully guarded: reconciliation must never break the import.
"""

import frappe
from frappe.utils import flt, getdate

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
# ERPNext core: link existing vouchers to a Bank Transaction.
RECONCILE_VOUCHERS = (
    "erpnext.accounts.doctype.bank_reconciliation_tool."
    "bank_reconciliation_tool.reconcile_vouchers"
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

    # Serialise reconciliation across the whole site, not per bank account.
    #
    # Matching reads an invoice's outstanding amount and then allocates against
    # it. That is safe while fetches run one after another: the first
    # allocation lowers the outstanding, so the second no longer matches. Since
    # the collective fetch runs one chain per bank side by side, two chains can
    # read the same outstanding before either writes -- and an invoice payable
    # from two accounts would be allocated twice.
    #
    # The lock is held only for the matching, never for the bank dialog, so the
    # parallel fetch keeps its speed. A timeout is not an error worth failing
    # the import for: the transactions are imported either way and the next run
    # reconciles them.
    from frappe.utils.synchronization import filelock

    try:
        with filelock("kefiya_auto_reconcile", timeout=120):
            _alyf_auto_reconcile(
                company,
                bank_account_doc.bank,
                bank_account,
                from_date,
                to_date,
            )

            if settings.auto_create_advance_payments:
                _create_advance_payments(bank_account, from_date, to_date)

            if settings.get("auto_reconcile_own_transfers"):
                _book_own_transfers(bank_account, from_date, to_date)
    except Exception:
        # Includes the lock timeout. Every stage inside guards itself, so
        # anything reaching here is the lock or a bug -- and neither may fail
        # an import whose transactions are already booked.
        frappe.log_error(
            title="Kefiya auto-reconcile skipped",
            message=frappe.get_traceback(),
            reference_doctype="Kefiya Login",
            reference_name=kefiya_login,
        )


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


def _book_own_transfers(bank_account, from_date, to_date):
    """Stage 4: book outgoing money that we ordered ourselves.

    Every transfer we send carries an End-To-End ID that names the voucher it
    pays, e.g. ``EXP-HR-EXP-2026-00009``. Banks return that ID in the statement
    text, so the withdrawal does not have to be guessed from amount and date --
    it says what it paid for.

    Booked is a Journal Entry, payable against bank, referencing the voucher.
    Not before the money is gone: sending a transfer is an instruction to the
    bank, and an instruction can still fail at the TAN.
    """
    filters = {
        "bank_account": bank_account,
        "docstatus": 1,
        "unallocated_amount": [">", 0],
        # only money that left the account
        "withdrawal": [">", 0],
    }
    if from_date and to_date:
        filters["date"] = ["between", [getdate(from_date), getdate(to_date)]]

    txns = frappe.get_all(
        "Bank Transaction",
        filters=filters,
        fields=[
            "name",
            "date",
            "withdrawal",
            "description",
            "reference_number",
            "company",
        ],
    )
    if not txns:
        return

    markers = _sent_transfer_markers(bank_account)
    if not markers:
        return

    for txn in txns:
        # A transaction that already carries a voucher is none of our business.
        if frappe.db.exists("Bank Transaction Payments", {"parent": txn.name}):
            continue

        marker = _find_marker(txn, markers)
        if not marker:
            continue

        try:
            _book_expense_claim(txn, marker, bank_account)
            # Make each booking durable on its own: a later failure must not
            # roll back a payment that already went through.
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title="Kefiya booking of own transfer failed",
                message="{0} / {1}\n\n{2}".format(
                    txn.name, marker, frappe.get_traceback()
                ),
            )


def _sent_transfer_markers(bank_account):
    """End-To-End IDs of released transfers sent from this account.

    Drafts are deliberately excluded: nothing that was never released can have
    left the account, so a matching withdrawal would be a coincidence.
    """
    transfers = frappe.get_all(
        "Kefiya Transfer",
        filters={"docstatus": 1},
        or_filters={"status": ("in", ("Sent", "Scheduled at Bank", "Due", "Approved"))},
        pluck="name",
    )
    if not transfers:
        return {}

    rows = frappe.get_all(
        "Kefiya Transfer Item",
        filters={"parent": ("in", transfers), "end_to_end_id": ("!=", "")},
        fields=["end_to_end_id", "amount", "parent"],
        parent_doctype="Kefiya Transfer",
    )
    markers = {}
    for row in rows:
        if row.end_to_end_id:
            markers[row.end_to_end_id] = row.amount
    return markers


def _find_marker(txn, markers):
    """Return the single marker this transaction carries, or None.

    Two markers in one text means a collective transfer whose split we cannot
    read from the statement -- then no automatic booking. And the amount has to
    match: a marker on a transaction of a different size is not the payment we
    are looking for.
    """
    haystack = "{0} {1}".format(
        txn.get("description") or "", txn.get("reference_number") or ""
    )
    found = [marker for marker in markers if marker in haystack]
    if len(found) != 1:
        return None

    marker = found[0]
    if abs(flt(markers[marker]) - flt(txn.withdrawal)) > 0.01:
        return None

    return marker


def _book_expense_claim(txn, marker, bank_account):
    """Book an expense claim as paid, against the account the money left from."""
    if not marker.startswith("EXP-"):
        return

    claim = marker[len("EXP-") :]
    claim_doc = frappe.db.get_value(
        "Expense Claim",
        claim,
        ["docstatus", "grand_total", "total_amount_reimbursed", "company"],
        as_dict=True,
    )
    if not claim_doc or claim_doc.docstatus != 1:
        return

    outstanding = flt(claim_doc.grand_total) - flt(claim_doc.total_amount_reimbursed)
    if outstanding <= 0:
        # already settled -- the statement is only telling us again
        return

    make_bank_entry = _callable(
        "hrms.hr.doctype.expense_claim.expense_claim.make_bank_entry"
    )
    entry = frappe.get_doc(make_bank_entry("Expense Claim", claim))

    # hrms fills the bank leg with the company default. The money left a
    # specific account, and that is the one that has to be credited.
    account = frappe.db.get_value("Bank Account", bank_account, "account")
    for row in entry.accounts:
        if not row.party:
            row.account = account

    entry.posting_date = txn.date
    entry.cheque_no = marker
    entry.cheque_date = txn.date
    entry.user_remark = "Kefiya: {0}".format(marker)
    entry.insert()
    entry.submit()

    reconcile = _callable(RECONCILE_VOUCHERS)
    reconcile(
        bank_transaction_name=txn.name,
        vouchers=frappe.as_json(
            [{"payment_doctype": "Journal Entry", "payment_name": entry.name}]
        ),
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
