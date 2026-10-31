# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One table of the bank codes this app explains.

The original (phamos-eu/kefiya) brought a second one, bank_error_hints(), for
the refusals on loading accounts and fetching transactions. Two tables for the
same codes drift: 9010 read one way on a transfer and another on a fetch. Now
fints_response.KNOWN_CODES is the table, and a request that means something
else by a code says so as an override.
"""

import os
import unittest

from kefiya.utils import fints_response

UTILS = os.path.join(os.path.dirname(__file__), "..", "..", "utils")


def _lines(*codes):
    return {"lines": [{"code": code} for code in codes]}


class TestOneTable(unittest.TestCase):

    def test_the_chiptan_resync_is_in_it(self):
        hints = fints_response.advice(_lines("9800", "9040"))
        self.assertEqual(len(hints), 1)
        self.assertIn("resynchronised", hints[0])

    def test_an_override_replaces_the_general_hint(self):
        fetch = {"9010": "If the From Date is more than 90 days ago,"
                         " choose a later one."}
        self.assertEqual(fints_response.advice(_lines("9010"), fetch),
                         [fetch["9010"]])

    def test_without_an_override_the_general_hint_stands(self):
        self.assertEqual(fints_response.advice(_lines("9010")),
                         [fints_response.KNOWN_CODES["9010"]])

    def test_each_hint_once(self):
        self.assertEqual(len(fints_response.advice(_lines("9040", "9040"))), 1)

    def test_no_second_table(self):
        with open(os.path.join(UTILS, "fints_bank_errors.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("def bank_error_hints", source)
        self.assertIn("fints_response.advice(", source)


if __name__ == "__main__":
    unittest.main()
