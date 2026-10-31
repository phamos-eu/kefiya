# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

from frappe.tests.utils import FrappeTestCase

from kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import import (
    KefiyaBankStatementImport,
)


def split(amount):
    return KefiyaBankStatementImport.format_amount_iso(None, amount)


class TestIsoAmount(FrappeTestCase):
    def test_amount_without_comma_is_whole_euros(self):
        # Used to be read as cents: "1234" as 12.34
        self.assertEqual(split("1234"), [1234.0, 0])
        self.assertEqual(split("-1234"), [0, 1234.0])

    def test_dot_is_a_thousands_separator(self):
        # Used to be read as 15.00
        self.assertEqual(split("1.500"), [1500.0, 0])

    def test_short_amount(self):
        # Used to fail
        self.assertEqual(split("-7"), [0, 7.0])

    def test_decimals_as_before(self):
        self.assertEqual(split("12,50"), [12.5, 0])
        self.assertEqual(split("12,5"), [12.5, 0])
        self.assertEqual(split('"-1.234,56"'), [0, 1234.56])
