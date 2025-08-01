# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt
from __future__ import unicode_literals

import frappe
from frappe.utils import today

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
    from kefiya.utils.fints_controller import FinTSController
    interactive = {"docname": user_scope, "enabled": True}

    return FinTSController(kefiya_login, interactive) \
        .import_fints_transactions(kefiya_import)


@frappe.whitelist()
def get_accounts(kefiya_login, user_scope):
    """Create payment entries by FinTS transactions.

    :param kefiya_login: kefiya_login doc name
    :param user_scope: Current open doctype page
    :type kefiya_login: str
    :type user_scopet: str
    :return: FinTS accounts json formated
    """
    from kefiya.utils.fints_controller import FinTSController
    interactive = {"docname": user_scope, "enabled": True}

    return {
        "accounts": FinTSController(
            kefiya_login,
            interactive).get_fints_accounts()
    }


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
    from kefiya.utils.bank_account_controller import \
        BankAccountController
    return BankAccountController().new_bank_account(payment_doc, bankData)


@frappe.whitelist()
def get_missing_bank_accounts():
    """Get possibly missing bank accounts.

    Query payment entries for missing bank accounts.
    :return: List of payment entry data
    """
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
    from kefiya.utils.assign_payment_controller import \
        AssignmentController

    return AssignmentController().add_payment_reference(
        payment_entry,
        sales_invoice
    )


@frappe.whitelist()
def auto_assign_payments():
    """Query assignable payments and create payment references.

    Try to assign payments in 3 steps:
    1. payment to sale assingment
    2. multiple payments to sale assingment
    3. payment to sale assingment

    :return: List of assigned payments
    """
    from kefiya.utils.assign_payment_controller import \
        AssignmentController

    return AssignmentController().auto_assign_payments()


def create_refund_journal_entry_from_purchase_invoice(invoice_doc, bank_transaction_name, refund_account):
    """Create a journal entry for refunding a purchase invoice."""

    je = frappe.new_doc("Journal Entry")
    je.posting_date = today()
    je.company = invoice_doc.company
    je.voucher_type = "Journal Entry"
    je.user_remark = f"Refund for Return Invoice {invoice_doc.name}"
    
    je.append("accounts", {
        "account": refund_account,
        "debit_in_account_currency": abs(invoice_doc.outstanding_amount),
        "credit_in_account_currency": 0,
    })

    je.append("accounts", {
        "account": invoice_doc.credit_to,
        "party_type": "Supplier",
        "party": invoice_doc.supplier,
        "credit_in_account_currency": abs(invoice_doc.outstanding_amount),
        "debit_in_account_currency": 0,
        "reference_type": "Purchase Invoice",
        "reference_name": invoice_doc.name
    })

    je.insert()
    je.submit()

    bt = frappe.get_doc("Bank Transaction", bank_transaction_name)
    
    bt.append("payment_entries", {
        "payment_document": "Journal Entry",
        "payment_entry": je.name,
        "allocated_amount": -abs(invoice_doc.outstanding_amount)  # NEGATIVE to increase unallocated
    })
    bt.save()

    return frappe.format(abs(bt.unallocated_amount), "Currency")


# Create Payment Entry record when reconcile button is clicked
@frappe.whitelist()
def create_payment_entry(bank_transaction_name, invoice_name, match_against):
    """Create payment entry document from sales or purchase invoice doctype.
    """
    if match_against == "Refund":
        invoice_doc = frappe.get_doc("Purchase Invoice", invoice_name)
        bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
        bank_account = ''
        if bank_transaction.bank_account:
            bank_account = frappe.db.get_values(
                "Bank Account", bank_transaction.bank_account, ["account", "company"], as_dict=True
            )[0]
        if bank_account:
            if bank_account.account:
                return create_refund_journal_entry_from_purchase_invoice(invoice_doc, bank_transaction_name, bank_account.account)
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
        payment_entry.reference_date = today()
        payment_entry.reference_no = 'BTN Wizard '+ today()
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

@frappe.whitelist()
def change_match_against(selected_match):
    kefiya_setting = frappe.get_single("Kefiya Settings")
    kefiya_setting.assign_against = selected_match
    kefiya_setting.save()