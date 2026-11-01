# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Holdings of a depot reach the system.

Two defects kept every depot empty, and neither left a trace:

1. The fetch asked the login's own account for holdings. A login points at a
   payment account; a depot has no IBAN at most banks and can never be that
   account. The bank refused, the refusal was filed as "not supported", and
   nothing was logged.
2. Had a depot answered, the rows would still have been dropped: python-fints
   names the field ``ISIN`` and the controllers read ``isin``, so every row
   arrived without an identity and ``refresh_holdings`` skipped it.
"""

import json
import os
import unittest
from collections import namedtuple

from kefiya.utils.securities_rows import holding_rows, same_account_number

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.normpath(os.path.join(HERE, "..", ".."))


def _read(*parts):
    with open(os.path.join(APP, *parts), encoding="utf-8") as handle:
        return handle.read()


# Same field names as fints.models.Holding, without importing fints.
Holding = namedtuple(
    "Holding",
    "ISIN name market_value value_symbol valuation_date pieces total_value "
    "acquisitionprice")


class TestHoldingRows(unittest.TestCase):

    def test_the_isin_survives_the_mapping(self):
        rows = holding_rows([Holding(
            "xx0000000002", "Example Fund", 12.5, "EUR", "2026-10-30",
            4.0, 50.0, 11.0)], "1234567")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["isin"], "XX0000000002")
        self.assertEqual(row["security_name"], "Example Fund")
        self.assertEqual(row["quantity"], 4.0)
        self.assertEqual(row["price"], 12.5)
        self.assertEqual(row["market_value"], 50.0)
        self.assertEqual(row["acquisition_price"], 11.0)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["valuation_date"], "2026-10-30")
        self.assertEqual(row["securities_account"], "1234567")

    def test_no_holdings_is_no_rows(self):
        self.assertEqual(holding_rows(None, "1"), [])
        self.assertEqual(holding_rows([], "1"), [])

    def test_dict_input_is_read_as_well(self):
        rows = holding_rows([{"isin": "xx0000000001", "pieces": 1}], "9")
        self.assertEqual(rows[0]["isin"], "XX0000000001")
        self.assertEqual(rows[0]["quantity"], 1)


class TestAccountNumbers(unittest.TestCase):

    def test_leading_zeros_do_not_matter(self):
        self.assertTrue(same_account_number("0001234", "1234"))
        self.assertTrue(same_account_number(1234, "1234"))

    def test_different_or_empty_numbers_do_not_match(self):
        self.assertFalse(same_account_number("1234", "1235"))
        self.assertFalse(same_account_number("", ""))
        self.assertFalse(same_account_number(None, "0"))


class TestControllersShareTheMapping(unittest.TestCase):

    def test_neither_controller_reads_a_lowercase_isin(self):
        for name in ("fints_controller.py", "fints_controller_legacy.py"):
            source = _read("utils", name)
            self.assertNotIn('getattr(h, "isin"', source, name)
            self.assertIn("holding_rows(", source, name)


class TestDepotsAreFetchedByAccountNumber(unittest.TestCase):

    def test_fetch_all_reaches_the_linked_depots(self):
        source = _read("utils", "client.py")
        self.assertIn("refresh_linked_depots(kefiya_login, FetchCtl)", source)

    def test_the_depot_is_addressed_without_an_iban(self):
        source = _read("utils", "fints_controller.py")
        start = source.index("def get_fints_depot_holdings(")
        body = source[start:source.index("\n    def ", start + 1)]
        self.assertIn("iban=None", body)
        self.assertIn("accountnumber=number", body)
        self.assertIn("NeedTANResponse", body)

    def test_a_failed_depot_is_logged_not_swallowed(self):
        source = _read("utils", "securities.py")
        start = source.index("def refresh_one_depot(")
        body = source[start:source.index("\ndef ", start + 1)]
        self.assertIn("frappe.log_error(", body)
        self.assertIn('"Failed"', body)


class TestDoctypes(unittest.TestCase):

    def _fields(self, doctype_dir):
        meta = json.loads(_read(
            "kefiya", "doctype", doctype_dir, doctype_dir + ".json"))
        names = {f["fieldname"]: f for f in meta["fields"]}
        for fieldname in meta["field_order"]:
            self.assertIn(fieldname, names, fieldname)
        return names

    def test_a_depot_links_to_its_bank_and_records_its_fetch(self):
        fields = self._fields("kefiya_securities_account")
        for name in ("online_banking_url", "subaccount_number",
                     "fetch_holdings", "last_holdings_fetch",
                     "last_holdings_status", "last_holdings_message"):
            self.assertIn(name, fields)
        self.assertEqual(fields["online_banking_url"].get("options"), "URL")

    def test_a_holding_can_be_entered_by_hand(self):
        fields = self._fields("kefiya_security_holding")
        self.assertFalse(fields["kefiya_login"].get("reqd"))
        self.assertIn("Manual", fields["source"]["options"])
        self.assertIn("acquisition_price", fields)


if __name__ == "__main__":
    unittest.main()
