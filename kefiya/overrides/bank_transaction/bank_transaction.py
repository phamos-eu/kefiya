import frappe
from erpnext.accounts.doctype.bank_transaction.bank_transaction import BankTransaction

class CustomBankTransaction(BankTransaction):
    @frappe.whitelist()
    def remove_payment_entries(self):
        payment_entries = []
        for payment_entry in self.payment_entries[:]:
            self.remove_payment_entry(payment_entry)
            payment_entries.append(payment_entry)

        # runs on_update_after_submit
        self.save()
        
        should_cancel = self.cancel_entries_on_unreconciling()
        for payment_entry in payment_entries:
            if should_cancel and payment_entry.payment_document == "Payment Entry":
                self.cancel_payment_entry(payment_entry)

    def cancel_entries_on_unreconciling(self):
        settings = frappe.get_single("Kefiya Settings")
        return settings.cancel_payment_entries_on_unreconciliation
    
    def cancel_payment_entry(self, payment_entry):
        payment_entry = frappe.get_doc("Payment Entry", payment_entry.payment_entry)
        payment_entry.cancel()