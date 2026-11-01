# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The way out of a refusal for want of funds: a dated transfer, or the amount
split into parts somebody chose.

The rules are checked here without a bench; the endpoints are read as text,
because what matters about them is what they refuse.
"""

import datetime
import importlib
import os
import sys
import types
import unittest

TODAY = datetime.date(2026, 11, 1)   # a Sunday


def _load():
    fake = types.ModuleType("frappe")
    fake._ = lambda s: s
    fake.whitelist = lambda *a, **k: (lambda f: f)
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    try:
        import kefiya.utils.funds_way_out as mod
        return importlib.reload(mod)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


fw = _load()

SOURCE = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "utils", "funds_way_out.py"),
    encoding="utf-8").read()


class TestTheParts(unittest.TestCase):

    def test_parts_that_add_up(self):
        problem, parts = fw.check_parts(
            [{"amount": "3709.55", "date": "2026-11-01"},
             {"amount": 8190.45, "date": "2026-11-03"}], 11900, TODAY)
        self.assertIsNone(problem)
        self.assertEqual([p["amount"] for p in parts], [3709.55, 8190.45])
        self.assertEqual(parts[1]["date"], datetime.date(2026, 11, 3))

    def test_freely_chosen_amounts_in_any_number(self):
        problem, parts = fw.check_parts(
            [{"amount": 1000}, {"amount": 2500.5}, {"amount": 8399.5,
                                                    "date": "2026-12-01"}],
            11900, TODAY)
        self.assertIsNone(problem)
        self.assertEqual(parts[0]["date"], TODAY)

    def test_must_add_up_to_the_cent(self):
        self.assertEqual(fw.check_parts(
            [{"amount": 100}, {"amount": 99.98}], 200, TODAY)[0], "sum")

    def test_no_empty_or_negative_part(self):
        self.assertEqual(fw.check_parts(
            [{"amount": 0}, {"amount": 200}], 200, TODAY)[0], "amount")
        self.assertEqual(fw.check_parts(
            [{"amount": -5}, {"amount": 205}], 200, TODAY)[0], "amount")
        self.assertEqual(fw.check_parts(
            [{"amount": "x"}, {"amount": 200}], 200, TODAY)[0], "amount")

    def test_not_in_the_past(self):
        self.assertEqual(fw.check_parts(
            [{"amount": 100, "date": "2026-10-31"}, {"amount": 100}], 200,
            TODAY)[0], "date")

    def test_one_part_is_no_split(self):
        self.assertEqual(fw.check_parts([{"amount": 200}], 200, TODAY)[0],
                         "count")


class TestThePurpose(unittest.TestCase):

    def test_says_which_part(self):
        self.assertEqual(fw.part_purpose("Rechnung 4711", 1, 2, "Teilzahlung"),
                         "Rechnung 4711 (Teilzahlung 1/2)")

    def test_stays_within_a_sepa_line(self):
        got = fw.part_purpose("x" * 140, 2, 3, "Teilzahlung")
        self.assertEqual(len(got), 140)
        self.assertTrue(got.endswith("(Teilzahlung 2/3)"))

    def test_without_a_purpose(self):
        self.assertEqual(fw.part_purpose(None, 1, 2, "Teilzahlung"),
                         "(Teilzahlung 1/2)")


class TestTheNextBankingDay(unittest.TestCase):

    def test_over_the_weekend(self):
        self.assertEqual(fw.next_banking_day(datetime.date(2026, 10, 30)),
                         datetime.date(2026, 11, 2))
        self.assertEqual(fw.next_banking_day(TODAY),
                         datetime.date(2026, 11, 2))
        self.assertEqual(fw.next_banking_day(datetime.date(2026, 11, 2)),
                         datetime.date(2026, 11, 3))


class TestTheEndpoints(unittest.TestCase):

    def test_only_a_draft_is_split(self):
        self.assertIn("if doc.docstatus != 0:", SOURCE)

    def test_permissions(self):
        self.assertIn('ptype="submit" if doc.docstatus == 1', SOURCE)
        self.assertIn('frappe.has_permission("Kefiya Transfer", ptype="write",'
                      "\n                          doc=transfer_name", SOURCE)

    def test_a_bank_without_dated_transfers_is_asked_first(self):
        asked = SOURCE.index("_refuse_unsupported(docs, True)")
        changed = SOURCE.index("doc.manage_due_date = 0")
        self.assertLess(asked, changed)

    def test_a_later_part_is_held_by_the_bank(self):
        self.assertIn(
            'target.manage_due_date = 0 if part["date"] > today else 1',
            SOURCE)

    def test_the_copy_carries_no_end_to_end_id(self):
        self.assertIn("_clean_row(doc.items[0].as_dict())", SOURCE)


if __name__ == "__main__":
    unittest.main()
