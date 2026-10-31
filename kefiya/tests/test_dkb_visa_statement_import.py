# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import os
import tempfile
from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import import (
    DKB_VISA,
    KefiyaBankStatementImport,
    detect_format,
    parse_statement,
    parse_german_amount,
)

# Anonymized DKB VISA Business Card export: same format as the real one, fake values
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "dkb_visa_umsatzliste.csv")

# A booked row without an amount, appended as line 20 of the fixture
BROKEN_ROW = '"02.10.26";"02.10.26";"Gebucht";"MUSTER KIOSK";"Im Geschäft";"";""\n'

COMPANY = "_Test GmbH"
BANK = "_Test DKB Bank"
ACCOUNT_NAME = "_Test DKB VISA"


def read_fixture() -> bytes:
    with open(FIXTURE, "rb") as f:
        return f.read()


class TestDkbVisaFormat(FrappeTestCase):
    def test_detects_dkb_visa_by_header(self):
        self.assertEqual(detect_format(read_fixture()), DKB_VISA)

    def test_detects_header_without_bom(self):
        self.assertEqual(detect_format(read_fixture()[3:]), DKB_VISA)

    def test_existing_utf8_format_still_detected(self):
        rawdata = (
            "Kontonummer:,DE00 0000 0000 0000 0000 00\n" * 7
            + "31.10.2026,31.10.2026,Gebucht,Müller GmbH,Überweisung,DE94640000000064001505,,\"-1.234,56\"\n"
        ).encode("utf-8")
        self.assertEqual(detect_format(rawdata), "utf-8")

    def test_existing_iso_format_still_detected(self):
        rawdata = (
            "\"Kontonummer:\";\"DE00 0000 0000 0000 0000 00 / Girokonto\";\n" * 7
            + "\"31.10.2026\";\"31.10.2026\";\"Lastschrift\";\"Müller GmbH\";\"Gebühr für Überweisung\";"
            "\"DE94640000000064001505\";\"\";\"-1.234,56\";\n"
        ).encode("ISO-8859-1")
        self.assertEqual(detect_format(rawdata), "ISO-8859-1")


class TestDkbVisaParsing(FrappeTestCase):
    def test_amounts(self):
        for text, amount in [
            ("-23,40", -23.40),
            ("-7", -7.0),
            ("-1.234,56", -1234.56),
            ("1.500", 1500.0),
            ("12,5", 12.5),
            ("-2.000", -2000.0),
        ]:
            with self.subTest(text=text):
                self.assertEqual(parse_german_amount(text), amount)

    def test_row_count(self):
        self.assertEqual(len(parse_statement(read_fixture())), 14)

    def test_pending_rows_are_skipped(self):
        pending = '"01.11.26";"";"Vorgemerkt";"BEISPIEL MARKT KOELN";"Im Geschäft";"-3,20";""\n'
        self.assertEqual(len(parse_statement(read_fixture() + pending.encode("utf-8"))), 14)

    def test_row_fields(self):
        rows = parse_statement(read_fixture())
        settlement = rows[0]
        self.assertEqual(settlement["date"], date(2026, 10, 30))
        self.assertEqual(settlement["amount"], 1500.0)
        self.assertEqual(settlement["description"], "Ausgleich Kreditkarte (Gutschrift)")

        foreign = rows[6]
        self.assertEqual(foreign["date"], date(2026, 10, 18))  # Belegdatum, not Wertstellung
        self.assertEqual(foreign["amount"], -49.99)
        self.assertEqual(foreign["party_name"], "CLOUD DIENST BEISPIEL")
        self.assertEqual(foreign["description"], "CLOUD DIENST BEISPIEL (Onlinezahlung, -52,30 $)")

    def test_transaction_ids_are_unique_and_stable(self):
        ids = [row["transaction_id"] for row in parse_statement(read_fixture())]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids, [row["transaction_id"] for row in parse_statement(read_fixture())])

    def test_transaction_id_matches_earlier_imports(self):
        # Id as created by the first DKB VISA import (!18); must not change, or re-imports duplicate
        self.assertEqual(parse_statement(read_fixture())[0]["transaction_id"], "3dc1cf0fe4cc819641d42d863c61e26b")

    def test_unreadable_row_raises_without_errors_list(self):
        with self.assertRaises(ValueError):
            parse_statement(read_fixture() + BROKEN_ROW.encode("utf-8"))

    def test_unreadable_row_is_skipped_and_reported(self):
        errors = []
        rows = parse_statement(read_fixture() + BROKEN_ROW.encode("utf-8"), errors)
        self.assertEqual(len(rows), 14)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 20)

    def test_identical_rows_get_different_ids(self):
        row = '"04.10.26";"04.10.26";"Gebucht";"MUSTER CAFE BERLIN";"Im Geschäft";"-4,8";""\n'
        rows = parse_statement(read_fixture() + row.encode("utf-8"))
        self.assertEqual(len(rows), 15)
        self.assertNotEqual(rows[-1]["transaction_id"], rows[-2]["transaction_id"])


class TestDkbVisaImport(FrappeTestCase):
    def setUp(self):
        if not frappe.db.exists("Company", COMPANY):
            self.skipTest(f"{COMPANY} does not exist on this site")
        if not frappe.db.exists("Bank", BANK):
            frappe.get_doc({"doctype": "Bank", "bank_name": BANK}).insert()
        gl_account = frappe.get_all(
            "Account",
            filters={"company": COMPANY, "account_type": "Bank", "is_group": 0},
            pluck="name",
            limit=1,
        )[0]
        self.bank_account = frappe.get_doc({
            "doctype": "Bank Account",
            "account_name": ACCOUNT_NAME,
            "bank": BANK,
            "company": COMPANY,
            "is_company_account": 1,
            "account": gl_account,
        }).insert().name

    def tearDown(self):
        frappe.db.rollback()

    def run_import(self, file_path=FIXTURE):
        doc = frappe.get_doc({
            "doctype": "Kefiya Bank Statement Import",
            "company": COMPANY,
            "bank_account": self.bank_account,
            "import_file": "/files/dkb_visa_umsatzliste.csv",
            "submit_after_success": 1,
        }).insert()
        with patch.object(KefiyaBankStatementImport, "get_file_from_url", lambda self, file_url: file_path):
            doc.start_import(doc.import_file, self.bank_account, COMPANY)
        doc.reload()
        return doc

    def transactions(self):
        return frappe.get_all(
            "Bank Transaction",
            filters={"bank_account": self.bank_account, "docstatus": 1},
            fields=["date", "deposit", "withdrawal", "transaction_id", "description"],
            order_by="date desc",
        )

    def test_import_creates_transactions(self):
        doc = self.run_import()
        self.assertEqual(doc.status, "Success")
        self.assertEqual(doc.imported_records, 14)

        transactions = self.transactions()
        self.assertEqual(len(transactions), 14)
        self.assertEqual(transactions[0].deposit, 1500)
        self.assertEqual(transactions[0].withdrawal, 0)
        purchase = next(t for t in transactions if t.description.startswith("MUSTERSHOP"))
        self.assertEqual(purchase.withdrawal, 2000)
        self.assertEqual(purchase.deposit, 0)

    def test_reimport_creates_no_duplicates(self):
        self.run_import()
        doc = self.run_import()
        self.assertEqual(doc.status, "Success")
        self.assertEqual(doc.imported_records, 0)
        self.assertEqual(len(self.transactions()), 14)


    def test_unreadable_row_gives_partial_success(self):
        with tempfile.NamedTemporaryFile(suffix=".csv") as broken:
            broken.write(read_fixture() + BROKEN_ROW.encode("utf-8"))
            broken.flush()
            doc = self.run_import(broken.name)
        self.assertEqual(doc.status, "Partial Success")
        self.assertEqual(doc.payload_count, 15)
        self.assertEqual(doc.imported_records, 14)
        self.assertEqual(len(self.transactions()), 14)
