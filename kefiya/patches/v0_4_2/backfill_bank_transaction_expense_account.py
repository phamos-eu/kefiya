import frappe
from kefiya.overrides.bank_transaction.bank_transaction import (
    get_journal_entry_party,
    get_voucher_expense_account,
)


def execute():
    """Backfill Expense Account (and, where missing, Party) on already
    reconciled Bank Transactions from their linked Purchase Invoice /
    Journal Entry."""
    if not frappe.db.has_column("Bank Transaction", "expense_account"):
        return

    bank_transactions = frappe.get_all(
        "Bank Transaction",
        filters={"docstatus": 1, "unallocated_amount": ["<=", 0]},
        fields=["name", "bank_account", "expense_account", "party"],
    )

    company_accounts = {}

    for bt in bank_transactions:
        if bt.expense_account and bt.party:
            continue

        if bt.bank_account not in company_accounts:
            company_accounts[bt.bank_account] = frappe.get_value("Bank Account", bt.bank_account, "account")
        company_account = company_accounts[bt.bank_account]

        payment_entries = frappe.get_all(
            "Bank Transaction Payments",
            filters={"parent": bt.name},
            fields=["payment_document", "payment_entry"],
        )

        updates = {}

        if not bt.expense_account:
            expense_account = next(
                filter(
                    None,
                    (
                        get_voucher_expense_account(pe.payment_document, pe.payment_entry, company_account)
                        for pe in payment_entries
                    ),
                ),
                None,
            )
            if expense_account:
                updates["expense_account"] = expense_account

        if not bt.party:
            for pe in payment_entries:
                if pe.payment_document != "Journal Entry":
                    continue
                party_type, party = get_journal_entry_party(pe.payment_entry)
                if party_type and party:
                    updates["party_type"] = party_type
                    updates["party"] = party
                    break

        if updates:
            frappe.db.set_value("Bank Transaction", bt.name, updates)
