from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from kefiya.overrides.bank_reconciliation_tool.bank_reconciliation_tool import (
    custom_create_journal_entry_bts,
)

BANK_TRANSACTION = "_Test Bank Transaction"


def permission(allowed_doctypes):
    """A stand-in for frappe.has_permission that allows only these doctypes."""
    return lambda doctype, *args, **kwargs: doctype in allowed_doctypes


class TestCreateJournalEntryPermissions(FrappeTestCase):
    """custom_create_journal_entry_bts makes the checks ERPNext 16's original makes."""

    def test_needs_write_access_to_the_bank_transaction(self):
        with patch("frappe.has_permission", permission(set())):
            self.assertRaises(
                frappe.PermissionError, custom_create_journal_entry_bts, BANK_TRANSACTION
            )

    def test_needs_access_to_the_party(self):
        with patch("frappe.has_permission", permission({"Bank Transaction"})):
            self.assertRaises(
                frappe.PermissionError,
                custom_create_journal_entry_bts,
                BANK_TRANSACTION,
                party_type="Customer",
                party="_Test Customer",
            )

    def test_unknown_party_type_is_refused(self):
        with patch("frappe.has_permission", permission({"Bank Transaction", "Customer"})):
            self.assertRaises(
                frappe.PermissionError,
                custom_create_journal_entry_bts,
                BANK_TRANSACTION,
                party_type="No Such Party Type",
                party="_Test Customer",
            )
