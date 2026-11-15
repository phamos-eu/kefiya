# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import os

import frappe
from frappe.tests.utils import FrappeTestCase

# Anonymized DKB VISA Business Card export: same format as the real one, fake values
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "dkb_visa_umsatzliste.csv")

COMPANY = "_Test GmbH"
BANK = "_Test DKB Bank"


class TestStartImportSecurity(FrappeTestCase):
    def setUp(self):
        # Also when setUp fails half way, as tearDown then does not run
        self.addCleanup(frappe.db.rollback)
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
            "account_name": "_Test Import Security",
            "bank": BANK,
            "company": COMPANY,
            "is_company_account": 1,
            "account": gl_account,
        }).insert().name
        # A GL account belongs to one bank account only, so the other one is a plain account;
        # it needs an IBAN of its own, as Kefiya keeps IBANs unique
        self.other_bank_account = frappe.get_doc({
            "doctype": "Bank Account",
            "account_name": "_Test Import Security Other",
            "bank": BANK,
            "company": COMPANY,
            "iban": "DE02120300000000202051",
        }).insert().name

        with open(FIXTURE, "rb") as f:
            self.file = frappe.get_doc({
                "doctype": "File",
                "file_name": "kefiya_test_import_security.csv",
                "content": f.read(),
                "is_private": 1,
            }).insert()

    def tearDown(self):
        frappe.set_user("Administrator")
        # Removes the file from disk; the rollback only covers the database
        self.file.delete()

    def make_import(self, import_file=None, bank_account=None):
        return frappe.get_doc({
            "doctype": "Kefiya Bank Statement Import",
            "company": COMPANY,
            "bank_account": bank_account or self.bank_account,
            "import_file": import_file or self.file.file_url,
            "submit_after_success": 1,
        }).insert()

    def transaction_count(self, bank_account):
        return frappe.db.count("Bank Transaction", {"bank_account": bank_account})

    def test_import_reads_the_uploaded_file(self):
        doc = self.make_import()
        doc.start_import(doc.import_file, doc.bank_account, doc.company)
        doc.reload()
        self.assertEqual(doc.status, "Success")
        self.assertEqual(self.transaction_count(self.bank_account), 14)

    def test_import_without_arguments_uses_the_doc(self):
        doc = self.make_import()
        doc.start_import()
        self.assertEqual(self.transaction_count(self.bank_account), 14)

    def test_mismatching_bank_account_is_rejected(self):
        doc = self.make_import()
        with self.assertRaises(frappe.ValidationError):
            doc.start_import(doc.import_file, self.other_bank_account, COMPANY)
        self.assertEqual(self.transaction_count(self.other_bank_account), 0)
        self.assertEqual(self.transaction_count(self.bank_account), 0)

    def test_mismatching_file_url_is_rejected(self):
        doc = self.make_import()
        with self.assertRaises(frappe.ValidationError):
            doc.start_import("/files/../../site_config.json", doc.bank_account, COMPANY)

    def test_bank_account_of_another_company_is_rejected(self):
        doc = self.make_import()
        frappe.db.set_value("Bank Account", self.bank_account, "company", "_Test Company Elsewhere")
        with self.assertRaises(frappe.ValidationError):
            doc.start_import()
        self.assertEqual(self.transaction_count(self.bank_account), 0)

    def test_path_traversal_is_rejected(self):
        # A File record with a traversing url, as File.validate would refuse to create it
        traversal_url = "/private/files/../../site_config.json"
        frappe.get_doc({
            "doctype": "File",
            "file_name": "site_config.json",
            "file_url": traversal_url,
            "is_private": 1,
        }).db_insert()
        doc = self.make_import()
        doc.db_set("import_file", traversal_url)

        with self.assertRaises(frappe.ValidationError) as context:
            doc.start_import()
        self.assertNotIn("db_name", str(context.exception))

    def test_url_without_file_record_is_rejected(self):
        doc = self.make_import()
        doc.db_set("import_file", "/files/../../site_config.json")
        with self.assertRaises(frappe.ValidationError):
            doc.start_import()

    def test_user_without_write_permission_cannot_start_import(self):
        doc = self.make_import()
        # Creating a user commits, so a user without roles on this doctype stands in
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            doc.start_import()
        frappe.set_user("Administrator")
        self.assertEqual(self.transaction_count(self.bank_account), 0)
