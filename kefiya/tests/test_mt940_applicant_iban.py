# -*- coding: utf-8 -*-
# Copyright (c) 2026, phamos GmbH and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock, patch

import frappe
from fints.utils import mt940_to_array
from frappe.tests.utils import FrappeTestCase

from kefiya.patches.v0_5_0 import split_iban_from_bank_party_name as iban_patch
from kefiya.utils.import_bank_transaction import ImportBankTransaction, make_transaction_id

IBAN = "DE27999999990003333333"
NAME = "MUSTERFIRMA GMBH"

# One debit with ?31 (IBAN) and ?32 (name) in the :86: details
MT940 = """:20:STARTUMS
:25:64040033/1234567
:28C:0
:60F:C261001EUR1000,00
:61:2610011001DR250,00NMSCNONREF
:86:105?00SEPA-BASIS-LASTSCHRIFT?20Steuer?30SOLADES1TUB?31{iban}?32{name}
:62F:C261001EUR750,00
""".format(iban=IBAN, name=NAME)


class TestMt940ApplicantIban(FrappeTestCase):
    def test_fints_parser_keeps_iban_separate(self):
        transaction = mt940_to_array(MT940)[0].data
        self.assertEqual(transaction["applicant_iban"], IBAN)
        self.assertEqual(transaction["applicant_name"], NAME)


class TestSplitIban(FrappeTestCase):
    def test_splits_leading_iban(self):
        self.assertEqual(iban_patch.split_iban(IBAN + NAME), (IBAN, NAME))

    def test_iban_only(self):
        self.assertEqual(iban_patch.split_iban(IBAN), (IBAN, None))

    def test_keeps_plain_account_number(self):
        self.assertEqual(iban_patch.split_iban("1234567890" + NAME), (None, "1234567890" + NAME))

    def test_keeps_wrong_checksum(self):
        wrong = "DE27999999990003333334" + NAME
        self.assertEqual(iban_patch.split_iban(wrong), (None, wrong))

    def test_keeps_plain_name(self):
        self.assertEqual(iban_patch.split_iban(NAME), (None, NAME))


def make_bank_transaction(**values):
    bank_account = frappe.db.get_value(
        "Bank Account", {"is_company_account": 1}, ["name", "company"], as_dict=True
    )
    if not bank_account:
        raise frappe.ValidationError("Test needs a company Bank Account")
    doc = frappe.get_doc({
        "doctype": "Bank Transaction",
        "date": "2026-10-01",
        "bank_account": bank_account.name,
        "company": bank_account.company,
        "withdrawal": 250,
        "description": "Steuer",
        **values,
    })
    doc.insert(ignore_permissions=True)
    return doc


class TestSplitIbanPatch(FrappeTestCase):
    def test_patch_splits_name_and_fills_party(self):
        doc = make_bank_transaction(bank_party_name=IBAN + NAME)
        match = ["Musterfirma", "Supplier", "0003333333"]
        with patch.object(ImportBankTransaction, "get_bank_account_data", return_value=match):
            iban_patch.execute()

        values = frappe.db.get_value(
            "Bank Transaction", doc.name,
            ["bank_party_iban", "bank_party_name", "party", "party_type", "bank_party_account_number"],
        )
        self.assertEqual(values, (IBAN, NAME, "Musterfirma", "Supplier", "0003333333"))

    def test_patch_leaves_plain_name_alone(self):
        doc = make_bank_transaction(bank_party_name=NAME)
        iban_patch.execute()
        self.assertEqual(
            frappe.db.get_value("Bank Transaction", doc.name, ["bank_party_iban", "bank_party_name"]),
            (None, NAME),
        )


class TestImportSkipsMt940v5Duplicates(FrappeTestCase):
    def test_transaction_imported_under_mt940_5_is_skipped(self):
        transaction = {
            "date": "2026-10-01",
            "amount": {"amount": "-250.00"},
            "status": "D",
            "applicant_iban": IBAN,
            "applicant_name": NAME,
            "posting_text": "SEPA-BASIS-LASTSCHRIFT",
            "purpose": "Steuer",
        }
        # The id it got when mt-940 5.x glued the IBAN to the name
        make_bank_transaction(
            bank_party_name=IBAN + NAME,
            reference_number=make_transaction_id(
                "2026-10-01", 250.0, IBAN + NAME, "SEPA-BASIS-LASTSCHRIFT", "Steuer"
            ),
        )
        importer = ImportBankTransaction(frappe._dict(), MagicMock())
        with patch("frappe.get_doc") as get_doc, patch("frappe.log_error") as log_error:
            importer.old_kefiya_import([transaction])
        # old_kefiya_import swallows errors, so check none was logged either
        log_error.assert_not_called()
        get_doc.assert_not_called()
        self.assertEqual(importer.bank_transactions, [])
