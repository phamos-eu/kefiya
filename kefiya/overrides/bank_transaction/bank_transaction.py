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