# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The import with a column mapping: preview, templates, Bank Transactions and Journal Entries."""

import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import import (
    KefiyaBankStatementImport,
    save_import_template,
)
from kefiya.tests.test_statement_mapping import ENGLISH_CSV, ENGLISH_MAPPING

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PAYPAL_FIXTURE = os.path.join(FIXTURES, "paypal_auszug.csv")

COMPANY = "_Test GmbH"
BANK = "_Test Wizard Bank"
COUNTER_ACCOUNT = "Office Rent - TGH"
FEE_ACCOUNT = "Bank Charges - TGH"
OTHER_ACCOUNT = "Telephone Expenses - TGH"


class ImportTestCase(FrappeTestCase):
    def setUp(self):
        self.addCleanup(frappe.db.rollback)
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
        self.bank_gl_account = gl_account
        self.bank_account = frappe.get_doc({
            "doctype": "Bank Account",
            "account_name": "_Test Wizard Konto",
            "bank": BANK,
            "company": COMPANY,
            "is_company_account": 1,
            "account": gl_account,
        }).insert().name

    def make_import(self, rawdata=None, submit=1):
        path = PAYPAL_FIXTURE
        if rawdata is not None:
            path = os.path.join(frappe.get_site_path("private", "files"), f"kefiya_test_wizard_{frappe.generate_hash(length=6)}.csv")
            with open(path, "wb") as f:
                f.write(rawdata)
            self.addCleanup(os.remove, path)
        doc = frappe.get_doc({
            "doctype": "Kefiya Bank Statement Import",
            "company": COMPANY,
            "bank_account": self.bank_account,
            "import_file": "/private/files/kefiya_test_wizard.csv",
            "submit_after_success": submit,
        }).insert()
        patcher = patch.object(KefiyaBankStatementImport, "get_file_from_url", lambda self, file_url: path)
        patcher.start()
        self.addCleanup(patcher.stop)
        return doc

    def start(self, doc, **kwargs):
        doc.start_import(**kwargs)
        doc.reload()
        return doc

    def log(self, doc):
        return json.loads(doc.import_log or "[]")


class TestPreview(ImportTestCase):
    def test_paypal_template_is_suggested(self):
        preview = self.make_import().get_preview()
        self.assertEqual(preview["template"], "builtin:PayPal")
        self.assertIsNone(preview["mapping_error"])
        self.assertEqual(preview["totals"]["New"], 12)
        self.assertAlmostEqual(preview["totals"]["deposits"] - preview["totals"]["withdrawals"], 131.82 - 500, places=2)
        self.assertIn("Transaktionscode", preview["file"]["header"])

    def test_rows_imported_before_are_duplicates(self):
        self.start(self.make_import())
        preview = self.make_import().get_preview()
        self.assertEqual((preview["totals"]["New"], preview["totals"]["Duplicate"]), (0, 12))

    def test_unknown_file_gets_a_guessed_mapping(self):
        preview = self.make_import(ENGLISH_CSV).get_preview()
        self.assertIsNone(preview["template"])
        mapping = preview["mapping"]
        self.assertEqual(mapping["columns"]["date"], "Booking Date")
        self.assertEqual((mapping["columns"]["deposit"], mapping["columns"]["withdrawal"]), ("Credit", "Debit"))
        self.assertEqual((mapping["number_format"], mapping["date_format"]), ("en", "%Y-%m-%d"))
        self.assertEqual(preview["totals"]["New"], 3)

    def test_invalid_mapping_is_reported_not_raised(self):
        preview = self.make_import(ENGLISH_CSV).get_preview(mapping={"columns": {"date": "Nope", "amount": "Credit"}})
        self.assertIn("Nope", preview["mapping_error"])
        self.assertEqual(preview["rows"], [])

    def test_preview_writes_nothing(self):
        doc = self.make_import()
        doc.get_preview()
        self.assertEqual(frappe.db.count("Bank Transaction", {"bank_account": self.bank_account}), 0)
        self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "status"), "Not Started")


class TestTemplates(ImportTestCase):
    def test_saved_template_is_suggested(self):
        header = ENGLISH_CSV.decode().splitlines()[3].split(",")
        save_import_template("_Test English Bank", ENGLISH_MAPPING, json.dumps(header))
        preview = self.make_import(ENGLISH_CSV).get_preview()
        self.assertEqual(preview["template"], "_Test English Bank")
        self.assertEqual(preview["totals"]["New"], 3)

    def test_saved_template_beats_builtin(self):
        from kefiya.utils.statement_import.presets import PAYPAL, PRESETS

        save_import_template("_Test My PayPal", dict(PRESETS[PAYPAL], number_format="de"))
        self.assertEqual(self.make_import().get_preview()["template"], "_Test My PayPal")

    def test_invalid_template_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            save_import_template("_Test Broken", {"columns": {"amount": "A"}})


class TestBankTransactionImport(ImportTestCase):
    def test_import_with_a_mapping(self):
        doc = self.start(self.make_import(ENGLISH_CSV), mapping=ENGLISH_MAPPING)
        self.assertEqual((doc.status, doc.imported_records, doc.import_as), ("Success", 3, "Bank Transaction"))
        transactions = frappe.get_all(
            "Bank Transaction",
            filters={"bank_account": self.bank_account, "docstatus": 1},
            fields=["deposit", "withdrawal", "bank_party_name", "reference_number"],
            order_by="date",
        )
        self.assertEqual([(t.deposit, t.withdrawal) for t in transactions], [(1234.56, 0), (0, 12.5), (0, 0.35)])
        self.assertEqual(transactions[1].bank_party_name, "Office Supplies")
        self.assertEqual(json.loads(doc.mapping)["number_format"], "en")

    def test_changed_mapping_imports_changed_values(self):
        mapping = json.loads(json.dumps(ENGLISH_MAPPING))
        mapping["columns"]["party_name"] = mapping["columns"].pop("party_name_withdrawal")
        mapping["columns"].pop("party_name_deposit")
        mapping["columns"]["party_name"] = "Purpose"
        mapping["description"]["columns"] = ["Payer", "Payee"]
        self.start(self.make_import(ENGLISH_CSV), mapping=mapping)
        names = frappe.get_all("Bank Transaction", filters={"bank_account": self.bank_account},
                               pluck="bank_party_name", order_by="date")
        self.assertEqual(names[0], "Invoice 4711")

    def test_invalid_mapping_is_refused_before_anything_is_imported(self):
        doc = self.make_import(ENGLISH_CSV)
        with self.assertRaises(frappe.ValidationError):
            doc.start_import(mapping={"columns": {"date": "Nope", "amount": "Credit"}})
        self.assertEqual(frappe.db.count("Bank Transaction", {"bank_account": self.bank_account}), 0)

    def test_running_import_cannot_be_started_again(self):
        doc = self.make_import(ENGLISH_CSV)
        doc.db_set("status", "In Progress")
        with patch(
            "kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import.is_job_enqueued",
            return_value=True,
        ), self.assertRaises(frappe.ValidationError):
            doc.start_import(mapping=ENGLISH_MAPPING)

    def test_changed_file_is_not_imported(self):
        doc = self.make_import(ENGLISH_CSV)
        with patch.object(KefiyaBankStatementImport, "run", lambda self: None):
            doc.start_import(mapping=ENGLISH_MAPPING)
        doc.reload()
        doc.db_set("file_hash", "0" * 64)
        doc.run()
        doc.reload()
        self.assertEqual(doc.status, "Error")
        self.assertIn("changed", self.log(doc)[0]["message"])

    def test_log_lists_problems_without_file_contents(self):
        doc = self.start(self.make_import(), template="builtin:PayPal")
        self.assertEqual(doc.imported_records, 12)
        log = self.log(doc)
        self.assertEqual({row["status"] for row in log}, {"Imported"})
        self.assertNotIn("@", json.dumps(log))


class TestJournalEntryImport(ImportTestCase):
    def import_je(self, rawdata=None, **kwargs):
        kwargs.setdefault("counter_account", COUNTER_ACCOUNT)
        doc = self.make_import(rawdata)
        if rawdata is None:
            kwargs.setdefault("template", "builtin:PayPal")
        else:
            kwargs.setdefault("mapping", ENGLISH_MAPPING)
        return self.start(doc, import_as="Journal Entry", **kwargs)

    def journal_entries(self):
        return frappe.get_all(
            "Journal Entry",
            filters={"kefiya_bank_account": self.bank_account, "docstatus": 1},
            fields=["name", "kefiya_transaction_id", "total_debit", "cheque_no", "clearance_date", "voucher_type",
                    "posting_date"],
        )

    def accounts(self, journal_entry):
        return {
            row.account: (row.debit, row.credit)
            for row in frappe.get_all("Journal Entry Account", filters={"parent": journal_entry},
                                      fields=["account", "debit", "credit"])
        }

    def test_one_journal_entry_per_row(self):
        doc = self.import_je(ENGLISH_CSV)
        self.assertEqual((doc.status, doc.imported_records), ("Success", 3))
        self.assertEqual(frappe.db.count("Bank Transaction", {"bank_account": self.bank_account}), 0)

        entries = {je.kefiya_transaction_id: je for je in self.journal_entries()}
        self.assertEqual(len(entries), 3)
        deposit = next(je for je in entries.values() if je.cheque_no == "R1")
        self.assertEqual(deposit.voucher_type, "Bank Entry")
        self.assertEqual(deposit.total_debit, 1234.56)
        self.assertEqual(str(deposit.clearance_date), str(deposit.posting_date))
        self.assertEqual(self.accounts(deposit.name),
                         {self.bank_gl_account: (1234.56, 0), COUNTER_ACCOUNT: (0, 1234.56)})
        withdrawal = next(je for je in entries.values() if je.cheque_no == "R2")
        self.assertEqual(self.accounts(withdrawal.name),
                         {self.bank_gl_account: (0, 12.5), COUNTER_ACCOUNT: (12.5, 0)})

    def test_reimport_and_other_target_find_duplicates(self):
        self.import_je(ENGLISH_CSV)
        again = self.import_je(ENGLISH_CSV)
        self.assertEqual((again.status, again.imported_records, again.skipped_records), ("Success", 0, 3))
        as_transactions = self.start(self.make_import(ENGLISH_CSV), mapping=ENGLISH_MAPPING)
        self.assertEqual(as_transactions.imported_records, 0)
        self.assertEqual(frappe.db.count("Bank Transaction", {"bank_account": self.bank_account}), 0)

    def test_fees_go_to_the_fee_account_and_rows_can_be_overridden(self):
        doc = self.import_je(fee_account=FEE_ACCOUNT, row_accounts={"3EF45678GH901234J": OTHER_ACCOUNT})
        self.assertEqual((doc.status, doc.imported_records), ("Success", 12))
        entries = {je.kefiya_transaction_id: je.name for je in self.journal_entries()}
        self.assertIn(FEE_ACCOUNT, self.accounts(entries["2CD34567EF890123G-fee"]))
        self.assertIn(COUNTER_ACCOUNT, self.accounts(entries["2CD34567EF890123G"]))
        self.assertIn(OTHER_ACCOUNT, self.accounts(entries["3EF45678GH901234J"]))

    def test_unknown_row_override_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            self.import_je(ENGLISH_CSV, row_accounts={"no such row": OTHER_ACCOUNT})

    def test_counter_account_is_required_and_checked(self):
        receivable = frappe.get_all("Account", filters={"company": COMPANY, "account_type": "Receivable",
                                                        "is_group": 0}, pluck="name", limit=1)
        for account in [None, self.bank_gl_account] + receivable:
            with self.subTest(account=account), self.assertRaises(frappe.ValidationError):
                self.import_je(ENGLISH_CSV, counter_account=account)
        self.assertEqual(self.journal_entries(), [])

    def test_account_of_another_company_is_refused(self):
        other = frappe.get_all("Account", filters={"company": ["!=", COMPANY], "is_group": 0,
                                                   "root_type": "Expense"}, pluck="name", limit=1)
        if not other:
            self.skipTest("no other company on this site")
        with self.assertRaises(frappe.ValidationError):
            self.import_je(ENGLISH_CSV, counter_account=other[0])

    def test_user_without_journal_entry_permission_is_stopped(self):
        with patch("frappe.has_permission", side_effect=lambda doctype, ptype="read", *a, **k: doctype != "Journal Entry"):
            with self.assertRaises(frappe.PermissionError):
                self.import_je(ENGLISH_CSV)

    def test_zero_amount_and_foreign_currency_rows(self):
        rawdata = ENGLISH_CSV.replace(b'"1,234.56",,EUR', b'0.00,,EUR').replace(b",0.35,EUR", b",0.35,USD")
        doc = self.import_je(rawdata)
        self.assertEqual((doc.status, doc.imported_records), ("Partial Success", 1))
        statuses = sorted(row["status"] for row in self.log(doc))
        self.assertEqual(statuses, ["Error", "Imported", "Skipped"])

    def test_draft_journal_entries(self):
        doc = self.make_import(ENGLISH_CSV, submit=0)
        self.start(doc, import_as="Journal Entry", mapping=ENGLISH_MAPPING, counter_account=COUNTER_ACCOUNT)
        drafts = frappe.get_all("Journal Entry", filters={"kefiya_bank_account": self.bank_account, "docstatus": 0},
                                fields=["clearance_date"])
        self.assertEqual(len(drafts), 3)
        self.assertIsNone(drafts[0].clearance_date)
