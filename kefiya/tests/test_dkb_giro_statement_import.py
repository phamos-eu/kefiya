# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import os
from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import import (
    DKB_GIRO,
    KefiyaBankStatementImport,
    detect_format,
    parse_statement,
)

# Anonymized DKB Girokonto export: same format as the real one, fake values
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "dkb_giro_umsatzliste.csv")

COMPANY = "_Test GmbH"
BANK = "_Test DKB Bank"
SUPPLIER = "_Test DKB Telekom"
SUPPLIER_IBAN = "DE02500105170137075030"


def read_fixture() -> bytes:
    with open(FIXTURE, "rb") as f:
        return f.read()


class TestDkbGiroParsing(FrappeTestCase):
    def test_detects_dkb_giro_by_header(self):
        self.assertEqual(detect_format(read_fixture()), DKB_GIRO)

    def test_row_count(self):
        self.assertEqual(len(parse_statement(read_fixture())), 12)

    def test_pending_rows_are_skipped(self):
        pending = (
            '"01.11.26";"";"Vorgemerkt";"Muster GmbH";"Beispiel Leasing GmbH";"LEASING RATE";'
            '"Ausgang";"DE89370400440532013000";"-450";"";"";""\n'
        )
        self.assertEqual(len(parse_statement(read_fixture() + pending.encode("utf-8"))), 12)

    def test_withdrawal_fields(self):
        row = parse_statement(read_fixture())[2]
        self.assertEqual(row["date"], date(2026, 10, 28))
        self.assertEqual(row["amount"], -49.99)
        self.assertEqual(row["party_name"], "Beispiel Telekom AG")  # payee
        self.assertEqual(row["party_iban"], SUPPLIER_IBAN)
        self.assertEqual(row["description"], "K1234567 R987654321 V123456789 Rechnung Oktober")
        self.assertEqual(row["reference_number"], "K1234567+R987654321")

    def test_deposit_fields(self):
        row = parse_statement(read_fixture())[5]
        self.assertEqual(row["date"], date(2026, 10, 24))
        self.assertEqual(row["amount"], 2380.5)
        self.assertEqual(row["party_name"], "BEISPIEL KUNDE AG")  # payer
        self.assertEqual(row["reference_number"], "20261024")

    def test_date_is_buchungsdatum(self):
        row = parse_statement(read_fixture())[3]
        self.assertEqual(row["date"], date(2026, 10, 27))  # Wertstellung is 28.10.26

    def test_transaction_ids_are_unique(self):
        ids = [row["transaction_id"] for row in parse_statement(read_fixture())]
        self.assertEqual(len(set(ids)), len(ids))


@patch.object(KefiyaBankStatementImport, "get_file_from_url", lambda self, file_url: FIXTURE)
class TestDkbGiroImport(FrappeTestCase):
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
            "account_name": "_Test DKB Girokonto",
            "bank": BANK,
            "company": COMPANY,
            "is_company_account": 1,
            "account": gl_account,
        }).insert().name

        if not frappe.db.exists("Supplier", SUPPLIER):
            frappe.get_doc({"doctype": "Supplier", "supplier_name": SUPPLIER}).insert()
        frappe.get_doc({
            "doctype": "Bank Account",
            "account_name": "_Test DKB Telekom",
            "bank": BANK,
            "party_type": "Supplier",
            "party": SUPPLIER,
            "iban": SUPPLIER_IBAN,
        }).insert()

    def tearDown(self):
        frappe.db.rollback()

    def run_import(self):
        doc = frappe.get_doc({
            "doctype": "Kefiya Bank Statement Import",
            "company": COMPANY,
            "bank_account": self.bank_account,
            "import_file": "/files/dkb_giro_umsatzliste.csv",
            "submit_after_success": 1,
        }).insert()
        doc.start_import(doc.import_file, self.bank_account, COMPANY)
        doc.reload()
        return doc

    def transactions(self):
        return frappe.get_all(
            "Bank Transaction",
            filters={"bank_account": self.bank_account, "docstatus": 1},
            fields=["deposit", "withdrawal", "bank_party_name", "bank_party_iban", "party_type", "party",
                    "reference_number"],
        )

    def test_import_creates_transactions_with_party(self):
        doc = self.run_import()
        self.assertEqual(doc.status, "Success")
        self.assertEqual(doc.imported_records, 12)

        transactions = self.transactions()
        self.assertEqual(len(transactions), 12)
        telekom = [t for t in transactions if t.bank_party_iban == SUPPLIER_IBAN]
        self.assertEqual(len(telekom), 2)
        for t in telekom:
            self.assertEqual((t.party_type, t.party), ("Supplier", SUPPLIER))
            self.assertEqual(t.bank_party_name, "Beispiel Telekom AG")
        tax = next(t for t in transactions if t.bank_party_name == "Finanzamt Beispielstadt")
        self.assertEqual((tax.withdrawal, tax.deposit), (1234.56, 0))

    def test_reimport_creates_no_duplicates(self):
        self.run_import()
        doc = self.run_import()
        self.assertEqual(doc.status, "Success")
        self.assertEqual(doc.imported_records, 0)
        self.assertEqual(len(self.transactions()), 12)
