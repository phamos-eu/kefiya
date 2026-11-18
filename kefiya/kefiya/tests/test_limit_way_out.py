# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Over the bank's limit: proposals, an editable table, and its check.

Measured on 18.11.2026 at a Sparkasse account with a daily limit of
3.000,00: 652,52 already sent that day, an order of 3.576,00 refused with
"2.347,48 free". The person had to work out the parts by hand.
"""

import datetime
import os
import unittest

from kefiya.utils import limit_split_plan as plan

WED = datetime.date(2026, 11, 18)


def daily(limits, used=None):
    """room_on for daily limits: limit - already used - planned that day."""
    used = used or {}

    def room_on(login, day, planned):
        taken = used.get((login, day), 0) + sum(
            p[2] for p in planned if p[1] == login and p[0] == day)
        return max(limits.get(login, 0) - taken, 0)
    return room_on


class TestBankingDays(unittest.TestCase):

    def test_today_first_then_weekdays(self):
        days = plan.banking_days(datetime.date(2026, 11, 20), 3)  # a Friday
        self.assertEqual(days[0], datetime.date(2026, 11, 20))
        self.assertEqual(days[1], datetime.date(2026, 11, 23))   # Monday


class TestProposals(unittest.TestCase):

    def test_own_account_day_by_day(self):
        room = daily({"own": 3000}, {("own", WED): 652.52})
        parts, rest = plan.fill(3576, ["own"], plan.banking_days(WED, 5),
                                room)
        self.assertEqual(rest, 0)
        self.assertEqual(parts, [(WED, "own", 2347.48),
                                 (datetime.date(2026, 11, 19), "own",
                                  1228.52)])

    def test_several_accounts_today_first(self):
        room = daily({"own": 3000, "other": 3000},
                     {("own", WED): 652.52})
        parts, rest = plan.fill(3576, ["own", "other"],
                                plan.banking_days(WED, 5), room)
        self.assertEqual(rest, 0)
        self.assertEqual(parts, [(WED, "own", 2347.48),
                                 (WED, "other", 1228.52)])

    def test_never_a_cent_over_the_room(self):
        room = daily({"own": 100.004})
        parts, _ = plan.fill(150, ["own"], plan.banking_days(WED, 3), room)
        self.assertEqual(parts[0][2], 100.0)

    def test_what_does_not_fit_is_said(self):
        room = daily({"own": 1000})
        _, rest = plan.fill(5000, ["own"], plan.banking_days(WED, 2), room)
        self.assertEqual(rest, 3000)


class TestTheEditedTable(unittest.TestCase):

    def setUp(self):
        self.room = daily({"own": 3000, "other": 3000},
                          {("own", WED): 652.52})

    def check(self, rows, total=3576):
        return plan.check_table(rows, total, WED, ["own", "other"],
                                self.room)

    def test_a_valid_table_passes(self):
        problem, _, clean = self.check([
            {"date": "2026-11-18", "kefiya_login": "own", "amount": 2000},
            {"date": "2026-11-19", "kefiya_login": "own", "amount": 1576}])
        self.assertIsNone(problem)
        self.assertEqual(len(clean), 2)

    def test_over_the_room_of_a_day(self):
        problem, detail, _ = self.check([
            {"date": "2026-11-18", "kefiya_login": "own", "amount": 2400},
            {"date": "2026-11-19", "kefiya_login": "own", "amount": 1176}])
        self.assertEqual(problem, "limit")
        self.assertEqual(detail[2], 2347.48)

    def test_two_rows_on_one_day_count_together(self):
        problem, _, _ = self.check([
            {"date": "2026-11-18", "kefiya_login": "own", "amount": 2000},
            {"date": "2026-11-18", "kefiya_login": "own", "amount": 1576}])
        self.assertEqual(problem, "limit")

    def test_the_parts_must_add_up(self):
        problem, _, _ = self.check([
            {"date": "2026-11-18", "kefiya_login": "own", "amount": 2000},
            {"date": "2026-11-19", "kefiya_login": "own", "amount": 1500}])
        self.assertEqual(problem, "sum")

    def test_no_day_in_the_past(self):
        problem, _, _ = self.check([
            {"date": "2026-11-17", "kefiya_login": "own", "amount": 2000},
            {"date": "2026-11-19", "kefiya_login": "own", "amount": 1576}])
        self.assertEqual(problem, "date")

    def test_an_account_not_offered_is_refused(self):
        problem, detail, _ = self.check([
            {"date": "2026-11-18", "kefiya_login": "own", "amount": 2000},
            {"date": "2026-11-18", "kefiya_login": "elsewhere",
             "amount": 1576}])
        self.assertEqual((problem, detail), ("account", "elsewhere"))

    def test_one_part_is_no_split(self):
        problem, _, _ = self.check([
            {"date": "2026-11-18", "kefiya_login": "other", "amount": 3576}])
        self.assertEqual(problem, "count")


def _read(*parts):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestTheWiring(unittest.TestCase):

    def test_the_refusal_names_the_order(self):
        body = _read("utils", "client.py").split(
            "def _refuse_over_limit(")[1].split("\ndef ")[0]
        self.assertIn('"names": [d.name for d in docs]', body)
        self.assertIn('"can_split"', body)

    def test_the_page_opens_the_table(self):
        page = _read("public", "js", "controllers", "payment_outbox.js")
        self.assertIn("kefiya.limit_way_out(m, { changed: load })", page)
        bundle = _read("public", "js", "kefiya.bundle.js")
        self.assertIn('import "./controllers/limit_way_out";', bundle)

    def test_an_approved_order_needs_the_confirmation(self):
        source = _read("utils", "limit_way_out.py")
        body = source.split("def apply(")[1].split("\ndef ")[0]
        self.assertIn("doc.docstatus == 1 and not cint(reopen)", body)
        self.assertLess(body.index("not cint(reopen)"),
                        body.index("doc.cancel()"))
        self.assertLess(body.index("plan.check_table("),
                        body.index("doc.cancel()"))
        self.assertIn("first.docstatus = 0", body)
        dialog = _read("public", "js", "controllers", "limit_way_out.js")
        self.assertIn("frappe.confirm(", dialog)

    def test_every_part_is_kept_here_until_its_day(self):
        """A dated order at the bank counts on the day it is SENT."""
        body = _read("utils", "limit_way_out.py").split("def apply(")[1]
        self.assertIn("target.manage_due_date = 1", body)

    def test_proposals_never_start_before_the_execution_date(self):
        body = _read("utils", "limit_way_out.py").split("def options(")[1]
        self.assertIn("start = max(today, plan.as_date(doc.execution_date)",
                      body)
        self.assertIn("plan.banking_days(start,", body)


class TestDatedTransfersAndTheLimit(unittest.TestCase):
    """Rules given on 18.11.2026."""

    def setUp(self):
        self.client = _read("utils", "client.py")

    def _body(self, name):
        return self.client.split("def " + name + "(")[1].split("\ndef ")[0]

    def test_a_date_today_or_gone_by_goes_out_today(self):
        body = self._body("_due_dated_goes_today")
        self.assertIn("getdate(doc.execution_date) <= today", body)
        self.assertIn('doc.db_set("manage_due_date", 1)', body)
        for path in ("submit_kefiya_transfer", "send_transfer_outbox"):
            send = self._body(path)
            self.assertLess(send.index("_due_dated_goes_today("),
                            send.index("_bank_holds_the_date(doc)"))

    def test_the_daily_run_does_the_same(self):
        body = self._body("present_due_transfers")
        self.assertLess(body.index('"manage_due_date": 0'),
                        body.index('"manage_due_date": 1'))

    def test_a_dated_order_over_the_limit_is_kept_here(self):
        for path in ("submit_kefiya_transfer", "send_transfer_outbox"):
            send = self._body(path)
            over = send[send.index("_refuse_over_limit("):]
            self.assertLess(over.index("if scheduled:"),
                            over.index("return over"))
            self.assertIn("return _keep_here_instead(", over)
        keep = self._body("_keep_here_instead")
        self.assertIn('doc.db_set("manage_due_date", 1)', keep)
        self.assertIn('"status": "kept_here"', keep)

    def test_kept_here_is_never_reported_as_sent(self):
        page = _read("public", "js", "controllers", "payment_outbox.js")
        self.assertIn('if (m.status === "kept_here") return "kept";', page)
        form = _read("public", "js", "controllers", "fints_transfer_flow.js")
        self.assertIn('msg.status === "kept_here"', form)
