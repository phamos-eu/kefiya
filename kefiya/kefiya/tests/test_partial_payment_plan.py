# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One payment paid in parts, over days and accounts.

The planner decides what leaves which account on which day, so it runs here
without frappe, on real dates. The frappe side is read as text: it must plan
on the server, stay inside one company and touch only drafts.
"""

import datetime
import os
import unittest

from kefiya.utils import partial_payment_plan as p

D = datetime.date
HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

# Mon 02.11.2026 .. Fri 06.11.2026; 07./08. are a weekend.
MONDAY = D(2026, 11, 2)
FRIDAY = D(2026, 11, 6)


def _read(*parts):
    with open(os.path.join(WURZEL, *parts), encoding="utf-8") as handle:
        return handle.read()


def daily(limits, used=None):
    """room_on for daily limits per account, with optional prior use."""
    used = used or {}

    def room_on(account, day, planned):
        taken = sum(a for d, acc, a in planned
                    if acc == account and d == day)
        return limits.get(account, 0) - used.get((account, day), 0) - taken
    return room_on


class TestBackwardsToTheDueDate(unittest.TestCase):

    def test_fits_in_one_part_on_the_due_date(self):
        parts, rest = p.plan_parts(800, FRIDAY, MONDAY, ["A"],
                                   daily({"A": 1000}))
        self.assertEqual(parts, [(FRIDAY, "A", 800.0)])
        self.assertEqual(rest, 0.0)

    def test_the_last_part_is_on_the_due_date(self):
        parts, rest = p.plan_parts(2500, FRIDAY, MONDAY, ["A"],
                                   daily({"A": 1000}))
        self.assertEqual(rest, 0.0)
        self.assertEqual(parts, [(D(2026, 11, 4), "A", 500.0),
                                 (D(2026, 11, 5), "A", 1000.0),
                                 (FRIDAY, "A", 1000.0)])

    def test_never_before_today(self):
        parts, rest = p.plan_parts(5000, D(2026, 11, 3), MONDAY, ["A"],
                                   daily({"A": 1000}))
        self.assertEqual(rest, 3000.0)
        self.assertTrue(all(day >= MONDAY for day, _, _ in parts))

    def test_weekends_are_skipped(self):
        monday_after = D(2026, 11, 9)
        parts, _ = p.plan_parts(2000, monday_after, MONDAY, ["A"],
                                daily({"A": 1000}))
        self.assertEqual([d for d, _, _ in parts], [FRIDAY, monday_after])

    def test_a_due_date_in_the_past_plans_nothing(self):
        parts, rest = p.plan_parts(100, D(2026, 10, 30), MONDAY, ["A"],
                                   daily({"A": 1000}))
        self.assertEqual(parts, [])
        self.assertEqual(rest, 100.0)


class TestAccountsBeforeDays(unittest.TestCase):

    def test_the_other_account_is_used_on_the_same_day(self):
        parts, rest = p.plan_parts(1500, FRIDAY, MONDAY, ["A", "B"],
                                   daily({"A": 1000, "B": 1000}))
        self.assertEqual(rest, 0.0)
        self.assertEqual(parts, [(FRIDAY, "A", 1000.0),
                                 (FRIDAY, "B", 500.0)])

    def test_the_own_account_comes_first_each_day(self):
        parts, _ = p.plan_parts(3500, FRIDAY, MONDAY, ["A", "B"],
                                daily({"A": 1000, "B": 1000}))
        self.assertEqual(parts, [(D(2026, 11, 5), "A", 1000.0),
                                 (D(2026, 11, 5), "B", 500.0),
                                 (FRIDAY, "A", 1000.0),
                                 (FRIDAY, "B", 1000.0)])

    def test_an_account_without_room_is_passed_over(self):
        parts, _ = p.plan_parts(500, FRIDAY, MONDAY, ["A", "B"],
                                daily({"A": 0, "B": 1000}))
        self.assertEqual(parts, [(FRIDAY, "B", 500.0)])

    def test_what_is_already_planned_uses_the_limit_up(self):
        used = {("A", FRIDAY): 700}
        parts, _ = p.plan_parts(500, FRIDAY, MONDAY, ["A"],
                                daily({"A": 1000}, used))
        self.assertEqual(parts, [(D(2026, 11, 5), "A", 200.0),
                                 (FRIDAY, "A", 300.0)])

    def test_an_account_named_twice_counts_once(self):
        parts, _ = p.plan_parts(1500, FRIDAY, MONDAY, ["A", "A"],
                                daily({"A": 1000}))
        self.assertEqual(len([x for x in parts if x[0] == FRIDAY]), 1)


class TestAllOrNothing(unittest.TestCase):

    def test_the_rest_is_reported(self):
        _, rest = p.plan_parts(10000.5, FRIDAY, MONDAY, ["A"],
                               daily({"A": 1000}))
        self.assertEqual(rest, 5000.5)

    def test_cents_add_up(self):
        parts, rest = p.plan_parts(2000.01, FRIDAY, MONDAY, ["A"],
                                   daily({"A": 1000}))
        self.assertEqual(rest, 0.0)
        self.assertAlmostEqual(sum(a for _, _, a in parts), 2000.01, 2)


class TestPartTexts(unittest.TestCase):

    def test_a_single_part_keeps_its_purpose(self):
        self.assertEqual(p.part_purpose("Rent", 1, 1), "Rent")

    def test_the_suffix_survives_a_long_purpose(self):
        text = p.part_purpose("x" * 200, 2, 3)
        self.assertEqual(len(text), 140)
        self.assertTrue(text.endswith(" (Teil 2/3)"))

    def test_an_empty_id_stays_empty(self):
        self.assertEqual(p.part_end_to_end_id("", 1, 3), "")

    def test_a_given_id_is_numbered_and_short_enough(self):
        base = "KSO-KEF-SO-00001-2026-11-16"
        ids = [p.part_end_to_end_id(base, i, 3) for i in (1, 2, 3)]
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(all(i.startswith(base) for i in ids))
        long_id = p.part_end_to_end_id("y" * 40, 12, 12)
        self.assertEqual(len(long_id), 35)
        self.assertTrue(long_id.endswith("-12"))


class TestTheServerSide(unittest.TestCase):
    """Read as text: transfer_limit imports frappe."""

    @classmethod
    def setUpClass(cls):
        cls.source = _read("utils", "transfer_limit.py")
        start = cls.source.index("def _partial_payment_source")
        cls.part = cls.source[start:]

    def test_only_a_draft_with_one_payment(self):
        self.assertIn("if doc.docstatus != 0:", self.part)
        self.assertIn("if len(doc.items) != 1:", self.part)

    def test_only_accounts_of_the_same_company(self):
        self.assertIn("if company != doc.company:", self.part)

    def test_the_browser_choice_is_checked_again(self):
        self.assertIn("if login not in allowed:", self.part)

    def test_an_unknown_limit_is_never_guessed(self):
        self.assertIn("if not _limit_of(doc.kefiya_login):", self.part)

    def test_nothing_is_placed_when_it_does_not_fit(self):
        split = self.part[self.part.index(
            "def split_into_partial_payments"):]
        self.assertLess(split.index("if rest > 0.004 or not parts:"),
                        split.index('frappe.new_doc("Kefiya Transfer")'))

    def test_each_part_is_kept_here_until_its_date(self):
        self.assertIn("target.manage_due_date = 1", self.part)

    def test_a_rolling_period_counts_for_the_days_after(self):
        self.assertIn("if limit_type == LIMIT_PERIOD:", self.source)
        self.assertIn("for k in range(span)", self.source)

    def test_a_limit_from_the_bank_wins_over_one_entered(self):
        """Agreed 01.11.2026: a limit the bank reports takes precedence; one
        entered by hand only stands while the bank names none."""
        refresh = self.source[self.source.index(
            "def refresh_transfer_limit"):self.source.index(
            "def pick_binding_limit")]
        silent = refresh[refresh.index("if not chosen:"):refresh.index(
            'return {"stored": False')]
        self.assertNotIn("transfer_limit_amount", silent)
        self.assertIn(
            'login.transfer_limit_amount = flt(chosen.get("amount"))',
            refresh)

    def test_dated_orders_all_carry_the_due_date(self):
        """Agreed 01.11.2026: parts sent on several days, all executed by
        the bank on the due date."""
        bank = self.part[self.part.index("if mode == MODE_BANK:"):]
        self.assertIn("target.execution_date = str(due)", bank)
        self.assertIn("target.manage_due_date = 0", bank)
        self.assertIn("target.instant_payment = 0", bank)
        self.assertIn('target.add_comment("Comment"', self.part)

    def test_a_dated_order_counts_on_the_day_it_was_sent(self):
        day = self.source[self.source.index("def limit_day"):self.source.index(
            "def committed_amount")]
        self.assertLess(day.index('row.get("sent_on")'),
                        day.index('row.get("execution_date")'))
        committed = self.source[self.source.index(
            "def committed_amount"):self.source.index("def check_transfer")]
        self.assertIn("if start <= limit_day(r) <= end", committed)

    def test_sending_counts_today(self):
        client = _read("utils", "client.py")
        refuse = client[client.index("def _refuse_over_limit"):]
        self.assertIn("check_batch(docs, on=frappe.utils.nowdate())", refuse)
        marked = client[client.index("def _mark_sent"):client.index(
            "def _mark_sent") + 1500]
        self.assertIn('doc.db_set("sent_on", now_datetime())', marked)

    def test_the_sent_day_is_a_field(self):
        import json
        meta = json.loads(_read("kefiya", "doctype", "kefiya_transfer",
                                "kefiya_transfer.json"))
        self.assertIn("sent_on", meta["field_order"])

    def test_the_form_offers_it(self):
        js = _read("kefiya", "doctype", "kefiya_transfer",
                   "kefiya_transfer.js")
        self.assertIn("kefiya_partial_payment_dialog(frm)", js)
        self.assertIn("split_into_partial_payments", js)


if __name__ == "__main__":
    unittest.main()
