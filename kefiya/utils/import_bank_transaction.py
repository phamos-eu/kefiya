# coding=utf-8
from __future__ import unicode_literals

import re
import frappe
from frappe import _
from frappe.utils import now_datetime, getdate, add_days, cint

from kefiya.utils import booking_fingerprint
from kefiya.utils.auszug_pruefung import VORZEICHEN

#: Welche Richtung ein Buchungskennzeichen bedeutet, kleingeschrieben wie
#: der Parser sie liefert. c/d sind Haben und Soll, rc/rd ihre Stornos --
#: und ein Storno geht in die Gegenrichtung. Bis hierher galt
#: ``status not in ['c', 'd']``, und jedes Storno wurde damit verworfen:
#: in den Auszuegen dieser Instanz 32 Buchungen ueber 866.612,58 EUR, die
#: im System schlicht fehlten. Siehe auszug_pruefung.VORZEICHEN.
RICHTUNG = {kennzeichen.lower(): richtung
            for kennzeichen, richtung in VORZEICHEN.items()}

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
        # Hier UND in _start_batch: ein Objekt darf nicht in einem Zustand
        # existieren, in dem _identify auf einen AttributeError laeuft.
        # _start_batch setzt sie je Lauf zurueck, es legt sie nicht an.
        self._start_batch()

    def _start_batch(self):
        """Reset the fingerprint tallies for one run.

        Two tallies, and they must not be the same one:

            _held    how many of each kind the database held BEFORE this run.
                     Read once per kind and then remembered, so the rows this
                     run writes do not inflate it.
            _seen    how many of each kind the BANK has delivered so far in
                     this run.
        """
        self._held = {}
        self._seen = {}

    def _identify(self, date, amount, iban, name, posting_text, purpose):
        """This booking's fingerprint, and whether it still has to be written.

        One reader for both formats, which is the point: MT940 and CAMT
        describe the same booking with different words, and the old hash took
        three of its five fields from exactly the words that differ. A booking
        fetched by both routes was written twice.

        Looked up under every form it may already be filed under, written
        under the new one. Without the lookup on the old forms this would
        re-import the entire history on the next fetch -- a far larger version
        of the problem it exists to solve.

        AND IT COUNTS. That is the other half, and it is the half that loses
        money rather than duplicating it.

        A fingerprint identifies a KIND of booking, not one row, and a bank
        really does send the same kind several times in one day. Measured
        against the bank's own statement, on one account, on one day::

            02.04.2025    Bank: 13 x -200,00 EUR    System: 4

        Nine standing-order payments simply gone -- because the old check
        asked "does one like this exist?" and every answer after the first was
        yes. On that account that is roughly 1.800 EUR a month over eleven
        months, and to an accountant it looks exactly like tenants who did not
        pay. It is the same fingerprint that produces duplicates, read from
        the other side.

        So the question is HOW MANY, not whether. The bank has sent the k-th
        copy of this kind; the database held m before this run began; the k-th
        copy is written when k > m. Re-fetching a period stays idempotent
        (every k <= m, nothing is written) and genuine repeats survive.

        :return: (fingerprint to write, True if this copy is already covered)
        """
        forms = booking_fingerprint.known_forms(
            bank_account=self.kefiya_login.bank_account,
            date=date, amount=amount, iban=iban, name=name,
            posting_text=posting_text, purpose=purpose)
        key = forms[0]

        if key not in self._held:
            self._held[key] = frappe.db.count(
                "Bank Transaction", {"reference_number": ["in", forms]})

        self._seen[key] = self._seen.get(key, 0) + 1
        return key, self._seen[key] <= self._held[key]

    def kefiya_import(self, fints_transaction):
        self.interactive.progress = 0
        self._start_batch()

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
                # CAMT nennt die Richtung in CreditDebitIndicator und ein
                # Storno getrennt davon in ReversalIndicator (RvslInd).
                # Der zweite wird hier noch nicht gelesen; ein CAMT-Storno
                # kaeme deshalb mit der Richtung seiner Originalbuchung an.
                # Das ist eine bekannte Luecke und kein Versehen -- fuer
                # eine Korrektur fehlt ein CAMT-Beispiel mit Storno.
                raw_status = t.get("CreditDebitIndicator")  # DBIT / CRDT
                status = 'd' if raw_status == 'DBIT' else 'c'

                # Amount extraction
                amount = abs(float(t["amount"][0]))

                if amount == 0:
                    continue

                if status not in RICHTUNG:
                    frappe.log_error(
                        title='Kefiya Import: payment type not handled',
                        message=_('Payment type not handled: {0}').format(
                            status),
                    )
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

                # Which booking this is, and whether it is already here.
                # Looked up under the new fingerprint AND the two the old
                # code wrote, so nothing already imported comes back. See
                # booking_fingerprint for what changed and why.
                transaction_id, already_here = self._identify(
                    date=date, amount=amount, iban=applicant_iban,
                    name=applicant_name, posting_text=posting_text,
                    purpose=purpose)
                if already_here:
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
                frappe.log_error(
                    title="Kefiya Import: error importing bank transaction",
                    message="{}\n\n{}".format(t, frappe.get_traceback()),
                )
                frappe.msgprint("There were some transactions with error. Please, have a look on Error Log.")

    def old_kefiya_import(self, fints_transaction):
        # F841 total_items = len(fints_transaction)
        self.interactive.progress = 0
        self._start_batch()
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
                        title='Kefiya Import: transaction incomplete',
                        message=_('Transaction missing amount or status'),
                    )
                    continue

                amount = abs(float(raw_amount))

                if amount == 0:
                    continue

                if status not in RICHTUNG:
                    frappe.log_error(
                        title='Kefiya Import: payment type not handled',
                        message=_('Payment type not handled: {0}').format(
                            status),
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

                transaction_id, already_here = self._identify(
                    date=date, amount=amount, iban=applicant_iban,
                    name=original_applicant_name, posting_text=posting_text,
                    purpose=purpose)
                if already_here:
                    continue

                # Ein Storno geht in die Gegenrichtung seiner Buchung:
                # rd storniert eine Belastung, das Geld kommt also zurueck.
                # Die Richtung steht in RICHTUNG und damit an genau einer
                # Stelle -- derselben, die die Kontoauszugspruefung benutzt.
                if RICHTUNG[status] > 0:
                    payment_type = 'Receive'
                    party_type = 'Customer'
                    paid_to = self.kefiya_login.erpnext_account  # noqa: E501
                    remarkType = 'Sender'
                    deposit = amount
                    withdrawal = 0
                else:
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
                frappe.log_error(
                    title="Kefiya Import: error importing bank transaction",
                    message="{}\n\n{}".format(t, frappe.get_traceback()),
                )
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
