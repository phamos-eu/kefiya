# coding=utf-8
from __future__ import unicode_literals

import hashlib
import frappe
from frappe import _

class ImportBankTransaction:
    def __init__(self, kefiya_login, interactive, allow_error=False):
        self.allow_error = allow_error
        self.bank_transactions = []
        self.kefiya_login = kefiya_login
        self.interactive = interactive

    def kefiya_import(self, fints_transaction):
        # F841 total_items = len(fints_transaction)
        self.interactive.progress = 0
        total_transactions = len(fints_transaction)

        for idx, t in enumerate(fints_transaction):
            try:
                # Convert to positive value if required
                amount = abs(float(t['amount']['amount']))
                status = t['status'].lower()

                if amount == 0:
                    continue

                if status not in ['c', 'd']:
                    frappe.log_error(
                        _('Payment type not handled'),
                        'Kefiya Import Error'
                    )
                    continue

                txn_number = idx + 1
                progress = txn_number / total_transactions * 100
                message = _('Query transaction {0} of {1}').format(
                    txn_number,
                    total_transactions
                )
                self.interactive.show_progress_realtime(
                    message, progress, reload=False
                )

                # date is in YYYY.MM.DD (json)
                date = t['date']
                applicant_name = t['applicant_name']
                posting_text = t['posting_text']
                purpose = t['purpose']
                applicant_iban = t['applicant_iban']
                applicant_bin = t['applicant_bin']

                remarkType = ''
                paid_to = None
                paid_from = None

                uniquestr = "{0},{1},{2},{3},{4}".format(
                    date,
                    amount,
                    applicant_name,
                    posting_text,
                    purpose
                )

                transaction_id = hashlib.md5(uniquestr.encode()).hexdigest()
                if frappe.db.exists(
                    'Bank Transaction', {
                        'reference_number': transaction_id
                    }
                ):
                    continue

                if status == 'c':
                    payment_type = 'Receive'
                    party_type = 'Customer'
                    paid_to = self.kefiya_login.erpnext_account  # noqa: E501
                    remarkType = 'Sender'
                    deposit = amount
                    withdrawal = 0
                elif status == 'd':
                    payment_type = 'Pay'
                    party_type = 'Supplier'
                    paid_from = self.kefiya_login.erpnext_account  # noqa: E501
                    remarkType = 'Receiver'
                    deposit = 0
                    withdrawal = amount

      
                party, party_type, bank_party_account_number = self.get_bank_account_data(applicant_iban)         

                bank_transaction = frappe.get_doc({
                    'doctype': 'Bank Transaction',
                    'date': date,
                    'status': 'Unreconciled',
                    'bank_account': self.kefiya_login.bank_account,
                    'company': self.kefiya_login.company,
                    'deposit': deposit,
                    'withdrawal': withdrawal,
                    'description': purpose,
                    'reference_number': transaction_id,
                    'allocated_amount': 0,
                    'unallocated_amount': amount,
                    'party_type': party_type,
                    'party': party,
                    'bank_party_name': applicant_name,
                    'bank_party_account_number': bank_party_account_number,
                    'bank_party_iban': applicant_iban,
                    'docstatus': 1
                })
                bank_transaction.insert()
                self.bank_transactions.append(bank_transaction)
            except Exception as e:
                frappe.log_error("Error importing bank transaction", "{}\n\n{}".format(t, frappe.get_traceback()))
                frappe.msgprint("There were some transactions with error. Please, have a look on Error Log.")

    def get_bank_account_data(self, IBAN):
        party, party_type, bank_party_account_number = '', '', ''
        bank_account_exists = frappe.db.exists('Bank Account', {'iban': IBAN})
        
        if bank_account_exists:
            bank_account_doc = frappe.get_doc('Bank Account', {'iban': IBAN})
            party = bank_account_doc.party
            party_type = bank_account_doc.party_type
            bank_party_account_number = bank_account_doc.bank_account_no

        return [party, party_type, bank_party_account_number]