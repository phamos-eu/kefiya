# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Turn the transactions of a statement file into Bank Transactions or Journal Entries."""

import frappe
from frappe import _
from frappe.utils import flt, getdate

BANK_TRANSACTION = "Bank Transaction"
JOURNAL_ENTRY = "Journal Entry"
TARGETS = (BANK_TRANSACTION, JOURNAL_ENTRY)

# Status of one transaction in the preview and in the import log
NEW, DUPLICATE, ERROR, SKIPPED, IMPORTED = "New", "Duplicate", "Error", "Skipped", "Imported"

# Journal Entry fields that remember which statement row an entry was made from
JE_ID_FIELD = "kefiya_transaction_id"
JE_BANK_ACCOUNT_FIELD = "kefiya_bank_account"

ID_CHUNK = 500


def existing_ids(bank_account: str, transaction_ids: list[str]) -> dict:
    """Return {transaction id: "Bank Transaction ACC-BTN-…" | "Journal Entry ACC-JV-…"} of rows imported before.

    Cancelled documents do not count, so a cancelled row can be imported again.
    """
    found = {}
    je_has_fields = frappe.get_meta(JOURNAL_ENTRY).has_field(JE_ID_FIELD)
    for start in range(0, len(transaction_ids), ID_CHUNK):
        chunk = transaction_ids[start:start + ID_CHUNK]
        for name, transaction_id in frappe.get_all(
            BANK_TRANSACTION,
            filters={"bank_account": bank_account, "transaction_id": ["in", chunk], "docstatus": ["<", 2]},
            fields=["name", "transaction_id"],
            as_list=True,
        ):
            found.setdefault(transaction_id, f"{BANK_TRANSACTION} {name}")
        if je_has_fields:
            for name, transaction_id in frappe.get_all(
                JOURNAL_ENTRY,
                filters={JE_BANK_ACCOUNT_FIELD: bank_account, JE_ID_FIELD: ["in", chunk], "docstatus": ["<", 2]},
                fields=["name", JE_ID_FIELD],
                as_list=True,
            ):
                found.setdefault(transaction_id, f"{JOURNAL_ENTRY} {name}")
    return found


def classify(transactions: list, errors: list, bank_account: str, import_as: str) -> list[dict]:
    """Return one entry per transaction and error, in file order, with its status and message."""
    existing = existing_ids(bank_account, [transaction["transaction_id"] for _line, transaction in transactions])
    entries = []
    for line, transaction in transactions:
        entry = {"line": line, "transaction": transaction, "status": NEW, "message": ""}
        if transaction["transaction_id"] in existing:
            entry.update(status=DUPLICATE, message=_("Imported before as {0}").format(existing[transaction["transaction_id"]]))
        elif not transaction["amount"]:
            entry.update(status=SKIPPED, message=_("The amount is zero"))
        entries.append(entry)
    entries += [{"line": line, "transaction": None, "status": ERROR, "message": message} for line, message in errors]
    entries.sort(key=lambda entry: entry["line"])
    return entries


def bank_account_details(bank_account: str, company: str) -> frappe._dict:
    """Return the bank account with its GL account and currency, or throw."""
    details = frappe.db.get_value(
        "Bank Account", bank_account, ["name", "company", "account", "is_company_account"], as_dict=True
    )
    if not details or details.company != company:
        frappe.throw(_("Bank Account {0} does not belong to company {1}.").format(bank_account, company))
    if details.account:
        details.account_currency = frappe.get_cached_value("Account", details.account, "account_currency")
    return details


def check_counter_account(account: str, company: str, bank_gl_account: str, label: str):
    """Throw unless the account can take the other side of a bank booking without a party."""
    values = frappe.db.get_value(
        "Account", account,
        ["company", "is_group", "disabled", "account_type", "account_currency", "root_type"],
        as_dict=True,
    )
    if not values or values.company != company:
        frappe.throw(_("{0}: account {1} does not belong to company {2}.").format(label, account, company))
    if values.is_group or values.disabled:
        frappe.throw(_("{0}: account {1} is a group or disabled.").format(label, account))
    if values.account_type in ("Receivable", "Payable", "Stock"):
        frappe.throw(_("{0}: {1} accounts need a party or an item and cannot be used here ({2}).").format(
            label, _(values.account_type), account))
    if account == bank_gl_account:
        frappe.throw(_("{0}: account {1} is the bank account itself.").format(label, account))
    company_currency = frappe.get_cached_value("Company", company, "default_currency")
    if values.account_currency and values.account_currency != company_currency:
        frappe.throw(_("{0}: account {1} is in {2}; only accounts in {3} are supported.").format(
            label, account, values.account_currency, company_currency))


def check_journal_entry_setup(company: str, bank: frappe._dict, counter_account: str, fee_account: str | None,
                              row_accounts: dict, submit: bool):
    """Throw unless Journal Entries can be made: permissions, bank GL account, counter accounts."""
    if not frappe.get_meta(JOURNAL_ENTRY).has_field(JE_ID_FIELD):
        frappe.throw(_("Kefiya's Journal Entry fields are missing. Please run bench migrate."))
    for ptype in ("create", "submit") if submit else ("create",):
        if not frappe.has_permission(JOURNAL_ENTRY, ptype):
            frappe.throw(_("You are not permitted to {0} Journal Entries.").format(_(ptype)), frappe.PermissionError)
    if not bank.is_company_account or not bank.account:
        frappe.throw(_("Bank Account {0} must be a company account with an account in the chart of accounts.").format(
            bank.name))
    company_currency = frappe.get_cached_value("Company", company, "default_currency")
    if bank.account_currency != company_currency:
        frappe.throw(_("The bank's account {0} is in {1}; Journal Entries can only be made in {2}.").format(
            bank.account, bank.account_currency, company_currency))
    if not counter_account:
        frappe.throw(_("Choose the counter account for the Journal Entries."))
    check_counter_account(counter_account, company, bank.account, _("Counter Account"))
    if fee_account:
        check_counter_account(fee_account, company, bank.account, _("Fee Account"))
    for transaction_id, account in row_accounts.items():
        check_counter_account(account, company, bank.account, _("Row {0}").format(transaction_id))


def counter_account_for(transaction: dict, counter_account: str, fee_account: str | None, row_accounts: dict) -> str:
    if transaction["transaction_id"] in row_accounts:
        return row_accounts[transaction["transaction_id"]]
    if fee_account and transaction["transaction_id"].endswith("-fee"):
        return fee_account
    return counter_account


def make_bank_transaction(transaction: dict, bank_account: str, company: str, submit: bool) -> str:
    from kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import import (
        get_bank_account_data,
    )

    party, party_type = (
        get_bank_account_data(transaction["party_iban"]) if transaction.get("party_iban") else ("", "")
    )
    amount = transaction["amount"]
    doc = frappe.new_doc(BANK_TRANSACTION)
    doc.update({
        "date": transaction["date"],
        "deposit": amount if amount > 0 else 0,
        "withdrawal": -amount if amount < 0 else 0,
        "bank_account": bank_account,
        "company": company,
        "description": transaction["description"],
        "bank_party_name": transaction["party_name"],
        "bank_party_iban": transaction.get("party_iban"),
        "party": party,
        "party_type": party_type,
        "reference_number": transaction.get("reference_number"),
        "transaction_id": transaction["transaction_id"],
    })
    meta = frappe.get_meta(BANK_TRANSACTION)
    for key, fieldname in (("party_account_number", "bank_party_account_number"), ("transaction_type", "transaction_type")):
        if transaction.get(key) and meta.has_field(fieldname):
            doc.set(fieldname, transaction[key])
    if transaction.get("currency"):
        # Bank Transaction refuses a currency that is not the bank account's
        doc.currency = transaction["currency"]
    doc.insert()
    if submit:
        doc.submit()
    return doc.name


def make_journal_entry(transaction: dict, bank: frappe._dict, company: str, account: str, submit: bool) -> str:
    """Book one statement row: the bank's GL account against the counter account."""
    if transaction.get("currency") and transaction["currency"] != bank.account_currency:
        frappe.throw(_("The row is in {0}, the bank account in {1}.").format(transaction["currency"], bank.account_currency))

    date = getdate(transaction["date"])
    amount = abs(flt(transaction["amount"]))
    deposit = transaction["amount"] > 0
    remark = ": ".join(t for t in (transaction["party_name"], transaction["description"]) if t)

    doc = frappe.new_doc(JOURNAL_ENTRY)
    doc.update({
        "voucher_type": "Bank Entry",
        "company": company,
        "posting_date": date,
        # A Bank Entry needs a reference; a row without one has at least its transaction id
        "cheque_no": (transaction.get("reference_number") or transaction["transaction_id"])[:140],
        "cheque_date": date,
        JE_ID_FIELD: transaction["transaction_id"],
        JE_BANK_ACCOUNT_FIELD: bank.name,
    })
    if doc.meta.has_field("custom_remark"):
        # ERPNext 16 hides user_remark and builds remark itself unless custom_remark is set
        doc.custom_remark = 1
        doc.remark = remark
    else:
        doc.user_remark = remark

    bank_row = {
        "account": bank.account,
        "bank_account": bank.name,
        "debit_in_account_currency": amount if deposit else 0,
        "credit_in_account_currency": 0 if deposit else amount,
    }
    counter_row = {
        "account": account,
        "debit_in_account_currency": 0 if deposit else amount,
        "credit_in_account_currency": amount if deposit else 0,
    }
    if frappe.get_cached_value("Account", account, "report_type") == "Profit and Loss":
        counter_row["cost_center"] = frappe.get_cached_value("Company", company, "cost_center")
    doc.append("accounts", bank_row)
    doc.append("accounts", counter_row)
    doc.insert()
    if submit:
        doc.submit()
        # No Bank Transaction will ever clear this entry: it comes from the statement itself
        doc.db_set("clearance_date", date, update_modified=False)
    return doc.name
