# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import os
from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import import (
    PAYPAL,
    KefiyaBankStatementImport,
    detect_format,
    parse_statement,
)

# Anonymized PayPal activity export: same format as the real one, fake values.
# Opening balance 500,00, closing balance 131,82.
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "paypal_auszug.csv")

COMPANY = "_Test GmbH"
BANK = "_Test PayPal"

# Line 2 of the fixture in US dollars
USD_ROW = (
    '"08.08.2026","10:00:00","PDT","Jane Roe","PayPal Express-Zahlung","Abgeschlossen","USD","25,00","-1,20",'
    '"23,80","jane.roe@test.example","shop@muster-gmbh.example","0UV12345WX678901Y"' + ',""' * 27 + ',"Haben"\n'
)


def read_fixture() -> bytes:
    with open(FIXTURE, "rb") as f:
        return f.read()


def by_id(rows) -> dict:
    return {row["transaction_id"]: row for row in rows}


class TestPaypalParsing(FrappeTestCase):
    def test_detects_paypal_by_header(self):
        self.assertEqual(detect_format(read_fixture()), PAYPAL)

    def test_detects_export_with_other_columns(self):
        # Which columns an export has depends on the download settings in PayPal
        rawdata = (
            '"Datum","Uhrzeit","Name","Typ","Status","Währung","Brutto","Gebühr","Netto","Transaktionscode",'
            '"Auswirkung auf Guthaben"\n'
            '"01.08.2026","01:12:45","Max Mustermann","PayPal Express-Zahlung","Abgeschlossen","EUR","10,00",'
            '"0,00","10,00","1AB23456CD789012E","Haben"\n'
        ).encode("utf-8")
        self.assertEqual(detect_format(rawdata), PAYPAL)
        row = parse_statement(rawdata)[0]
        self.assertEqual((row["amount"], row["description"], row["reference_number"]),
                         (10.0, "PayPal Express-Zahlung", ""))

    def test_row_count(self):
        # 8 rows change the balance, 4 of them have a fee
        self.assertEqual(len(parse_statement(read_fixture())), 12)

    def test_memo_rows_are_skipped(self):
        # The authorisation moves no money; its code is on two rows of the export
        self.assertNotIn("4GH56789JK012345L", by_id(parse_statement(read_fixture())))

    def test_amounts_add_up_to_the_balance_change(self):
        total = sum(row["amount"] for row in parse_statement(read_fixture()))
        self.assertAlmostEqual(total, 131.82 - 500.00, places=2)

    def test_payment_is_gross_and_fee(self):
        rows = by_id(parse_statement(read_fixture()))
        payment = rows["2CD34567EF890123G"]
        self.assertEqual(payment["date"], date(2026, 8, 2))
        self.assertEqual(payment["amount"], 1234.56)
        self.assertEqual(payment["currency"], "EUR")
        self.assertEqual(payment["party_name"], "Erika Musterfrau")
        self.assertEqual(payment["description"], "PayPal Express-Zahlung, Bestellung AB10002")
        self.assertEqual(payment["reference_number"], "Bestellung AB10002")

        fee = rows["2CD34567EF890123G-fee"]
        self.assertEqual(fee["date"], date(2026, 8, 2))
        self.assertEqual(fee["amount"], -32.45)
        self.assertEqual(fee["party_name"], "PayPal")
        self.assertEqual(fee["description"], "PayPal Gebühr zu 2CD34567EF890123G")

    def test_bank_withdrawal_has_no_fee_row(self):
        rows = by_id(parse_statement(read_fixture()))
        withdrawal = rows["3EF45678GH901234J"]
        self.assertEqual(withdrawal["amount"], -1500.0)
        self.assertEqual(withdrawal["party_name"], "")
        self.assertEqual(withdrawal["description"], "Allgemeine Abbuchung")
        self.assertNotIn("3EF45678GH901234J-fee", rows)

    def test_refund_gives_the_fee_back(self):
        rows = by_id(parse_statement(read_fixture()))
        self.assertEqual(rows["8QR90123ST456789U"]["amount"], -59.9)
        self.assertEqual(rows["8QR90123ST456789U"]["description"],
                         "Rückzahlung, Bestellung AB10001, zu 1AB23456CD789012E")
        self.assertEqual(rows["8QR90123ST456789U-fee"]["amount"], 1.49)

    def test_pending_hold_and_its_reversal_are_both_imported(self):
        rows = by_id(parse_statement(read_fixture()))
        self.assertEqual(rows["5JK67890LM123456N"]["amount"], -89.0)  # status 'Ausstehend'
        self.assertEqual(rows["6LM78901NP234567Q"]["amount"], 89.0)

    def test_line_break_in_a_cell_does_not_split_the_row(self):
        rows = by_id(parse_statement(read_fixture()))
        self.assertEqual(rows["9ST01234UV567890W"]["amount"], 19.99)

    def test_buyer_contact_data_is_not_taken_over(self):
        # E-mail, delivery address and phone number are in the export, but not needed for the books
        values = " ".join(str(value) for row in parse_statement(read_fixture()) for value in row.values())
        for contact_data in ("@", "Musterstraße", "Beispielweg", "Musterstadt", "030000000", "Bitte beim Nachbarn"):
            self.assertNotIn(contact_data, values)

    def test_broken_row_raises_without_errors_list(self):
        broken = USD_ROW.replace('"25,00"', '"25.00"')
        with self.assertRaises(ValueError):
            parse_statement(read_fixture() + broken.encode("utf-8"))

    def test_broken_row_is_skipped_and_reported(self):
        broken = USD_ROW.replace('"25,00"', '"25.00"')
        errors = []
        rows = parse_statement(read_fixture() + broken.encode("utf-8"), errors)
        self.assertEqual(len(rows), 12)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 13)  # file line; one cell of the fixture spans two lines


class TestPaypalImport(FrappeTestCase):
    def setUp(self):
        if not frappe.db.exists("Company", COMPANY):
            self.skipTest(f"{COMPANY} does not exist on this site")
        if not frappe.db.exists("Bank", BANK):
            frappe.get_doc({"doctype": "Bank", "bank_name": BANK}).insert()
        gl_account = frappe.get_all(
            "Account",
            filters={"company": COMPANY, "account_type": "Bank", "is_group": 0, "account_currency": "EUR"},
            pluck="name",
            limit=1,
        )[0]
        self.bank_account = frappe.get_doc({
            "doctype": "Bank Account",
            "account_name": "_Test PayPal Konto",
            "bank": BANK,
            "company": COMPANY,
            "is_company_account": 1,
            "account": gl_account,
        }).insert().name

    def tearDown(self):
        frappe.db.rollback()

    def run_import(self, rawdata=None):
        doc = frappe.get_doc({
            "doctype": "Kefiya Bank Statement Import",
            "company": COMPANY,
            "bank_account": self.bank_account,
            "import_file": "/private/files/paypal_auszug.csv",
            "submit_after_success": 1,
        }).insert()
        path = FIXTURE
        if rawdata:
            path = os.path.join(frappe.get_site_path("private", "files"), "kefiya_test_paypal.csv")
            with open(path, "wb") as f:
                f.write(rawdata)
            self.addCleanup(os.remove, path)
        with patch.object(KefiyaBankStatementImport, "get_file_from_url", lambda self, file_url: path):
            doc.start_import(doc.import_file, self.bank_account, COMPANY)
        doc.reload()
        return doc

    def transactions(self):
        return frappe.get_all(
            "Bank Transaction",
            filters={"bank_account": self.bank_account, "docstatus": 1},
            fields=["transaction_id", "deposit", "withdrawal", "bank_party_name", "reference_number", "currency"],
        )

    def test_import_creates_transactions(self):
        doc = self.run_import()
        self.assertEqual(doc.status, "Success")
        self.assertEqual(doc.imported_records, 12)

        transactions = {t.transaction_id: t for t in self.transactions()}
        self.assertEqual(len(transactions), 12)
        payment = transactions["1AB23456CD789012E"]
        self.assertEqual((payment.deposit, payment.withdrawal), (59.9, 0))
        self.assertEqual(payment.bank_party_name, "Max Mustermann")
        self.assertEqual(payment.reference_number, "Bestellung AB10001")
        fee = transactions["1AB23456CD789012E-fee"]
        self.assertEqual((fee.deposit, fee.withdrawal), (0, 1.84))
        self.assertEqual(fee.bank_party_name, "PayPal")

        balance_change = sum(t.deposit - t.withdrawal for t in transactions.values())
        self.assertAlmostEqual(balance_change, 131.82 - 500.00, places=2)

    def test_reimport_creates_no_duplicates(self):
        self.run_import()
        doc = self.run_import()
        self.assertEqual(doc.status, "Success")
        self.assertEqual(doc.imported_records, 0)
        self.assertEqual(len(self.transactions()), 12)

    def test_row_in_another_currency_is_not_imported(self):
        doc = self.run_import(read_fixture() + USD_ROW.encode("utf-8"))
        self.assertEqual(doc.status, "Partial Success")
        self.assertEqual(doc.imported_records, 12)
        self.assertEqual({t.currency for t in self.transactions()}, {"EUR"})
