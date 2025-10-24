import frappe
from frappe.utils import flt
from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry

class CustomJournalEntry(JournalEntry):
    def set_total_debit_credit(self):
        self.total_debit, self.total_credit, self.difference = 0, 0, 0

        accounts = self.get("accounts")
        should_halve = len(accounts) == 4 and accounts[1].account == accounts[2].account

        for d in accounts:
            if d.debit and d.credit:
                frappe.throw(_("You cannot credit and debit the same account at the same time"))

            factor = 0.5 if should_halve else 1
            self.total_debit = flt(self.total_debit) + (flt(d.debit, d.precision("debit")) * factor)
            self.total_credit = flt(self.total_credit) + (flt(d.credit, d.precision("credit")) * factor)

        self.difference = flt(self.total_debit, self.precision("total_debit")) - flt(
            self.total_credit, self.precision("total_credit")
        )
