# coding=utf-8
from __future__ import unicode_literals

import hashlib
import re
import frappe
from frappe import _
from frappe.utils import now_datetime, getdate, add_days, cint

# Fallback look-back window when a Kefiya Login has no explicit
# allowed_sync_days_in_past configured.
DEFAULT_SYNC_DAYS_IN_PAST = 90


def resolve_incremental_from_date(bank_account, max_days_in_past=DEFAULT_SYNC_DAYS_IN_PAST):
    """Start date for an incremental FinTS fetch.

    Returns the date of the most recently imported (submitted) Bank
    Transaction for the given bank account (so the next fetch continues where
    the last one ended), clamped to the login's allowed look-back window
    (``max_days_in_past``). When there is no history yet, falls back to that
    full window.

    :param bank_account: Bank Account name (kefiya_login.bank_account)
    :param max_days_in_past: Kefiya Login.allowed_sync_days_in_past
    :return: datetime.date
    """
    max_days_in_past = cint(max_days_in_past) or DEFAULT_SYNC_DAYS_IN_PAST
    today = now_datetime().date()
    earliest = getdate(add_days(today, -max_days_in_past))

    last_date = None
    if bank_account:
        rows = frappe.db.get_all(
            "Bank Transaction",
            # only submitted transactions; cancelled/draft rows must not
            # determine where the next fetch starts.
            filters={"bank_account": bank_account, "docstatus": 1},
            fields=["date"],
            order_by="date desc",
            limit=1,
        )
        if rows and rows[0].date:
            # never start in the future: value-dated / pre-booked entries can
            # carry a date ahead of today; cap at today so from_date <= to_date.
            last_date = min(getdate(rows[0].date), today)

    if last_date and last_date > earliest:
        return last_date
    return earliest

# IBAN total length per ISO 13616 for common SEPA countries (country code -> length)
IBAN_LENGTHS = {
    "DE": 22, "AT": 20, "CH": 21, "LI": 21, "LU": 20, "NL": 18, "BE": 16,
    "FR": 27, "IT": 27, "ES": 24, "PT": 25, "DK": 18, "FI": 18, "SE": 24,
    "NO": 15, "PL": 28, "CZ": 24, "SK": 24, "HU": 28, "GB": 22, "IE": 22,
}

IBAN_PATTERN = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}")


def iban_is_valid(iban):
    """Validate an IBAN via expected country length and ISO 7064 mod-97 checksum."""
    if not iban:
        return False
    iban = iban.replace(" ", "").upper()
    if len(iban) < 15 or len(iban) > 34:
        return False
    expected = IBAN_LENGTHS.get(iban[:2])
    if expected and len(iban) != expected:
        return False
    rearranged = iban[4:] + iban[:4]
    converted = ""
    for char in rearranged:
        if char.isdigit():
            converted += char
        elif "A" <= char <= "Z":
            converted += str(ord(char) - 55)
        else:
            return False
    return int(converted) % 97 == 1


def extract_iban_from_name(name):
    """Some banks concatenate the counterparty IBAN into the name field
    (e.g. 'DE21....409Max Mustermann'). Return a tuple (iban_or_None,
    cleaned_name). The name is changed only when a valid IBAN can be
    removed cleanly."""
    if not name:
        return None, name
    compact = name.replace(" ", "")
    for match in IBAN_PATTERN.finditer(compact):
        candidate = match.group(0)
        # the greedy match may swallow an uppercase initial of the name;
        # trim from the end until a structurally valid IBAN remains
        for end in range(len(candidate), 14, -1):
            trimmed = candidate[:end]
            if iban_is_valid(trimmed):
                # remove the IBAN and collapse leftover whitespace runs
                cleaned = re.sub(r"\s+", " ", name.replace(trimmed, "")).strip()
                return trimmed, (cleaned or None)
    return None, name


class ImportBankTransaction:
    def __init__(self, kefiya_login, interactive, allow_error=False):
        self.allow_error = allow_error
        self.bank_transactions = []
        self.kefiya_login = kefiya_login
        self.interactive = interactive

    def kefiya_import(self, fints_transaction):
        self.interactive.progress = 0

        # CAMT returns a list inside a list → flatten it
        flat_transactions = []
        for lvl1 in fints_transaction:
            if isinstance(lvl1, list):
                flat_transactions.extend(lvl1)
            else:
                flat_transactions.append(lvl1)

        total_transactions = len(flat_transactions)

        for idx, t in enumerate(flat_transactions):
            try:
                # Status conversion
                raw_status = t.get("CreditDebitIndicator")  # DBIT / CRDT
                status = 'd' if raw_status == 'DBIT' else 'c'

                # Amount extraction
                amount = abs(float(t["amount"][0]))

                if amount == 0:
                    continue

                if status not in ['c', 'd']:
                    frappe.log_error(_('Payment type not handled'), 'Kefiya Import Error')
                    continue

                # Progress bar
                progress = (idx + 1) / total_transactions * 100
                msg = _('Query transaction {0} of {1}').format(idx + 1, total_transactions)
                self.interactive.show_progress_realtime(msg, progress, reload=False)

                # Dates
                date = t.get("date") or t.get("ValueDate.Date")

                # Purpose (Unstructured)
                purpose = (
                    t.get("purpose")
                    or t.get("EntryDetails.TransactionDetails.RemittanceInformation.Unstructured")
                )

                posting_text = t.get("AdditionalEntryInformation")

                # Debtor / Creditor logic
                if status == 'd':   # outgoing: Creditor is receiver
                    applicant_name = t.get("EntryDetails.TransactionDetails.RelatedParties.Creditor.Party.Name")
                    applicant_iban = t.get("EntryDetails.TransactionDetails.RelatedParties.CreditorAccount.Identification.IBAN")
                else:               # incoming: Debtor is sender
                    applicant_name = t.get("EntryDetails.TransactionDetails.RelatedParties.Debtor.Party.Name")
                    applicant_iban = t.get("EntryDetails.TransactionDetails.RelatedParties.DebtorAccount.Identification.IBAN")

                applicant_bin = None  # CAMT does not include BIN

                # Unique hash
                uniquestr = "{0},{1},{2},{3},{4}".format(
                    date, amount, applicant_name, posting_text, purpose
                )
                transaction_id = hashlib.md5(uniquestr.encode()).hexdigest()

                if frappe.db.exists('Bank Transaction', {'reference_number': transaction_id}):
                    continue

                # Payment type
                if status == 'c':
                    payment_type = 'Receive'
                    party_type = 'Customer'
                    paid_to = self.kefiya_login.erpnext_account
                    deposit = amount
                    withdrawal = 0
                else:
                    payment_type = 'Pay'
                    party_type = 'Supplier'
                    paid_from = self.kefiya_login.erpnext_account
                    deposit = 0
                    withdrawal = amount

                party, party_type, bank_party_account_number = self.get_bank_account_data(applicant_iban)

                # Create Bank Transaction
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

    def old_kefiya_import(self, fints_transaction):
        # F841 total_items = len(fints_transaction)
        self.interactive.progress = 0
        total_transactions = len(fints_transaction)

        for idx, t in enumerate(fints_transaction):
            try:
                # Convert to positive value if required. Guard amount/status:
                # unlike the date/name fields below, these used hard subscripts,
                # so any key drift in the parser output raised and the whole
                # transaction was silently dropped by the except block.
                amount_field = t.get('amount')
                raw_amount = (
                    amount_field.get('amount')
                    if isinstance(amount_field, dict) else amount_field
                )
                status = (t.get('status') or '').lower()

                if raw_amount in (None, '') or not status:
                    frappe.log_error(
                        _('Transaction missing amount or status'),
                        'Kefiya Import Error'
                    )
                    continue

                amount = abs(float(raw_amount))

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
                date = t.get('date')
                applicant_name = t.get('applicant_name')
                # keep the raw name for the dedup hash so that display-only
                # cleaning (IBAN extraction below) never changes the hash and
                # re-imports transactions that were already imported.
                original_applicant_name = applicant_name
                posting_text = t.get('posting_text')
                purpose = t.get('purpose')
                # mt-940 key drift: 'applicant_iban' is absent in the current
                # parser output; fall back to 'gvc_applicant_iban'.
                applicant_iban = t.get('applicant_iban') or t.get('gvc_applicant_iban')
                applicant_bin = t.get('applicant_bin') or t.get('gvc_applicant_bin')

                # some banks concatenate the counterparty IBAN into the name
                # field (e.g. 'DE21...Max Mustermann'); pull it out when no
                # IBAN was provided and clean up the displayed name
                if not applicant_iban:
                    applicant_iban, applicant_name = extract_iban_from_name(applicant_name)


                remarkType = ''
                paid_to = None
                paid_from = None

                uniquestr = "{0},{1},{2},{3},{4}".format(
                    date,
                    amount,
                    original_applicant_name,
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
