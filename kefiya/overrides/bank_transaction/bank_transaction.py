import frappe

# When the ALYF `banking` app is installed, both apps override "Bank Transaction" via
# `override_doctype_class`. Kefiya's class wins as the controller, but banking still
# registers a `before_submit` doc-event that calls `doc.get_rounded(...)` -- a method
# defined on banking's BankTransaction subclass. If Kefiya extends the plain ERPNext
# BankTransaction instead, that method is missing and every bank-transaction import
# fails with `AttributeError: ... has no attribute 'get_rounded'`. Extending banking's
# class when present makes the two overrides compose; we fall back to the ERPNext base
# when `banking` is not installed (banking is not a Kefiya dependency).
try:
    from banking.overrides.bank_transaction import CustomBankTransaction as BankTransactionBase
except Exception:
    from erpnext.accounts.doctype.bank_transaction.bank_transaction import (
        BankTransaction as BankTransactionBase,
    )


class CustomBankTransaction(BankTransactionBase):
    def before_submit(self):
        super().before_submit()
        self.set_expense_account_and_party()

    def before_update_after_submit(self):
        super().before_update_after_submit()
        self.set_expense_account_and_party()

    def set_expense_account_and_party(self):
        """Derive the Expense Account (and, for Journal Entries, backfill the
        Party) from the voucher(s) this Bank Transaction is reconciled against."""
        if self.unallocated_amount > 0 or not self.payment_entries:
            self.expense_account = None
            return

        company_account = frappe.get_value("Bank Account", self.bank_account, "account")

        self.expense_account = next(
            filter(
                None,
                (
                    get_voucher_expense_account(pe.payment_document, pe.payment_entry, company_account)
                    for pe in self.payment_entries
                ),
            ),
            None,
        )

        if not self.party:
            for pe in self.payment_entries:
                if pe.payment_document != "Journal Entry":
                    continue
                party_type, party = get_journal_entry_party(pe.payment_entry)
                if party_type and party:
                    self.party_type, self.party = party_type, party
                    break

    @frappe.whitelist()
    def remove_payment_entries(self):
        # Permission gate: a whitelisted document method is callable by anyone
        # who may read the document, and this unreconciles -- and depending on
        # Kefiya Settings cancels and deletes -- the linked Payment Entries.
        frappe.has_permission(
            "Bank Transaction", ptype="write", doc=self, throw=True)

        payment_entries = []
        for payment_entry in self.payment_entries[:]:
            self.remove_payment_entry(payment_entry)
            payment_entries.append(payment_entry)
        # runs on_update_after_submit
        self.save()
        
        should_delete = self.delete_entries_on_unreconciling()
        if should_delete:
            for payment_entry in payment_entries:
                if payment_entry.payment_document == "Payment Entry":
                    self.delete_payment_entry(payment_entry)
                elif payment_entry.payment_document == "Journal Entry":
                    self.delete_journal_entry(payment_entry)

    def delete_entries_on_unreconciling(self):
        settings = frappe.get_single("Kefiya Settings")
        return settings.delete_payment_entries_on_unreconciliation
    
    def delete_payment_entry(self, payment_entry):
        payment_entry = frappe.get_doc("Payment Entry", payment_entry.payment_entry)
        payment_entry.cancel()
        payment_entry.delete()
    
    def delete_journal_entry(self, payment_entry):
        journal_entry = frappe.get_doc("Journal Entry", payment_entry.payment_entry)
        journal_entry.cancel()
        journal_entry.delete()


def get_voucher_expense_account(payment_document, payment_entry, company_account):
    """Best-effort guess of the Expense/Income Account booked against a
    reconciled voucher, excluding the company's own bank account."""
    if payment_document == "Payment Entry":
        # A Bank Transaction can be reconciled against a Payment Entry that was
        # itself created to settle one or more Purchase Invoices (e.g. the
        # Mastercard matching flow) - follow that reference to the real invoice.
        purchase_invoices = frappe.get_all(
            "Payment Entry Reference",
            filters={"parent": payment_entry, "reference_doctype": "Purchase Invoice"},
            pluck="reference_name",
        )
        return next(
            filter(
                None,
                (
                    get_voucher_expense_account("Purchase Invoice", pi, company_account)
                    for pi in purchase_invoices
                ),
            ),
            None,
        )

    if payment_document == "Purchase Invoice":
        accounts = frappe.get_all(
            "Purchase Invoice Item", filters={"parent": payment_entry}, pluck="expense_account"
        )
    elif payment_document == "Journal Entry":
        accounts = frappe.get_all(
            "Journal Entry Account",
            filters={"parent": payment_entry, "party_type": ["in", ["", None]]},
            pluck="account",
        )
    else:
        return None

    accounts = sorted({account for account in accounts if account and account != company_account})
    return accounts[0] if accounts else None


def get_journal_entry_party(journal_entry):
    row = frappe.get_all(
        "Journal Entry Account",
        filters={"parent": journal_entry, "party_type": ["is", "set"], "party": ["is", "set"]},
        fields=["party_type", "party"],
        limit=1,
    )
    return (row[0].party_type, row[0].party) if row else (None, None)