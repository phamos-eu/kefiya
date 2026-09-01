# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Which copy of a doubled booking survives -- and when none of them does.

This is the rule that decides whether 14,492 documents are deleted from a
production database whose source files no longer exist, so it is tested for
what it decides rather than for what it queries. Everything below is a pure
function over dicts; no site, no bank.

The case it comes from: an import filed one payment under two or three
accounts. An employee noticed because a supplier appeared to have been paid
three times, once from an account that could not have paid him. The rows are
identical to the character, so the rule has to reach for the only trustworthy
thing in the database -- the transactions somebody really fetched over a live
bank connection, one account at a time.
"""

import os
import unittest

from kefiya.utils.duplicate_rule import (
    BY_ACCOUNT, BY_PAYEE, PURPOSE_CUT, UNDECIDED, fingerprint, pick_home,
)

BRILU = "Brilu KG Mietkonto Sparkasse"
SOFIE = "Sofienstr. Mietkonto Sparkasse"
BAU = "Sofienstr. Baukonto Sparkasse"


def _copy(account, **rest):
    row = {"name": account[:5] + "-1", "bank_account": account,
           "date": "2026-02-17", "withdrawal": 895.48, "deposit": 0,
           "description": "ONLINE-UEBERWEISUNG | RNr. 2026/10",
           "bank_party_name": "Milorad Vrban"}
    row.update(rest)
    return row


class TestTwoRowsAreOneBooking(unittest.TestCase):

    def test_the_same_payment_on_two_accounts_has_one_fingerprint(self):
        self.assertEqual(fingerprint(_copy(BRILU)), fingerprint(_copy(BAU)))

    def test_a_different_amount_is_a_different_booking(self):
        self.assertNotEqual(fingerprint(_copy(BRILU)),
                            fingerprint(_copy(BRILU, withdrawal=895.49)))

    def test_a_different_day_is_a_different_booking(self):
        self.assertNotEqual(fingerprint(_copy(BRILU)),
                            fingerprint(_copy(BRILU, date="2026-02-18")))

    def test_an_incoming_and_an_outgoing_amount_are_not_the_same(self):
        """A transfer between two own accounts appears on both -- once as a
        withdrawal, once as a deposit. That is two bookings, not a duplicate,
        and deleting one of them would lose a real payment."""
        self.assertNotEqual(
            fingerprint(_copy(BRILU, withdrawal=100, deposit=0)),
            fingerprint(_copy(SOFIE, withdrawal=0, deposit=100)))

    def test_the_purpose_is_compared_but_not_endlessly(self):
        long_one = "X" * (PURPOSE_CUT + 40)
        other = "X" * PURPOSE_CUT + "Y" * 40
        self.assertEqual(fingerprint(_copy(BRILU, description=long_one)),
                         fingerprint(_copy(BAU, description=other)))


class TestThePayeeDecides(unittest.TestCase):
    """The strong case: one of the accounts has really paid this
    counterparty and the other has not."""

    def test_the_account_that_really_pays_him_keeps_the_booking(self):
        copies = [_copy(BRILU), _copy(SOFIE), _copy(BAU)]
        keeper, why = pick_home(
            copies, ("MILORAD", "VRBAN"),
            {(("MILORAD", "VRBAN"), BRILU): 74,
             (("MILORAD", "VRBAN"), SOFIE): 2},
            {BRILU: 2054, SOFIE: 227, BAU: 0})
        self.assertEqual(why, BY_PAYEE)
        self.assertEqual(keeper["bank_account"], BRILU)

    def test_a_busy_account_does_not_beat_a_relevant_one(self):
        """An account that is merely large is not evidence about THIS
        payment. The payee is."""
        copies = [_copy(BRILU), _copy(SOFIE)]
        keeper, why = pick_home(
            copies, ("EINE", "FIRMA"),
            {(("EINE", "FIRMA"), SOFIE): 12},
            {BRILU: 2054, SOFIE: 227})
        self.assertEqual(why, BY_PAYEE)
        self.assertEqual(keeper["bank_account"], SOFIE)

    def test_an_equal_number_of_real_payments_decides_nothing_by_payee(self):
        copies = [_copy(BRILU), _copy(SOFIE)]
        keeper, why = pick_home(
            copies, ("MILORAD", "VRBAN"),
            {(("MILORAD", "VRBAN"), BRILU): 5,
             (("MILORAD", "VRBAN"), SOFIE): 5},
            {BRILU: 2054, SOFIE: 227})
        # It falls through to the weaker rule rather than tossing a coin.
        self.assertEqual(why, BY_ACCOUNT)
        self.assertEqual(keeper["bank_account"], BRILU)


class TestAnAccountWithNoHistoryAtAll(unittest.TestCase):
    """The Baukonto. Not one submitted transaction has ever been fetched for
    it, and it held 29,693 copies of payments that live elsewhere."""

    def test_it_never_keeps_a_booking(self):
        copies = [_copy(BRILU), _copy(BAU)]
        keeper, why = pick_home(copies, ("UNBEKANNT",), {},
                                {BRILU: 2054, BAU: 0})
        self.assertEqual(why, BY_ACCOUNT)
        self.assertEqual(keeper["bank_account"], BRILU)

    def test_two_silent_accounts_decide_nothing(self):
        """Zero is the absence of evidence, not evidence. Letting an
        unfetched account win because the other is equally silent would put
        money on an account for no reason at all."""
        keeper, why = pick_home([_copy(BAU), _copy(SOFIE)], ("UNBEKANNT",),
                                {}, {BAU: 0, SOFIE: 0})
        self.assertEqual(why, UNDECIDED)
        self.assertIsNone(keeper)


class TestNothingIsGuessed(unittest.TestCase):

    def test_a_tie_leaves_the_booking_alone(self):
        """A duplicate that is visibly a duplicate is better than a payment
        filed on the wrong account with nothing to show it was a guess."""
        keeper, why = pick_home([_copy(BRILU), _copy(SOFIE)], ("UNBEKANNT",),
                                {}, {BRILU: 500, SOFIE: 500})
        self.assertEqual(why, UNDECIDED)
        self.assertIsNone(keeper)

    def test_a_single_copy_is_not_a_duplicate(self):
        keeper, why = pick_home([_copy(BRILU)], ("MILORAD", "VRBAN"),
                                {(("MILORAD", "VRBAN"), BRILU): 74},
                                {BRILU: 2054})
        self.assertEqual(why, UNDECIDED)
        self.assertIsNone(keeper)

    def test_a_payee_nobody_knows_falls_through_rather_than_failing(self):
        keeper, why = pick_home([_copy(BRILU), _copy(BAU)], (), {},
                                {BRILU: 2054, BAU: 0})
        self.assertEqual(why, BY_ACCOUNT)
        self.assertEqual(keeper["bank_account"], BRILU)


def _repair_source():
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, "utils", "statement_repair.py"),
              encoding="utf-8") as handle:
        return handle.read()


class TestTheDeletionCannotRunAway(unittest.TestCase):
    """Rails on the part that actually removes documents from a production
    database whose source files no longer exist."""

    def test_only_a_draft_is_ever_deleted(self):
        body = _repair_source().split("def _untouched(")[1].split("\ndef ")[0]
        self.assertIn('row.get("docstatus") == 0', body)

    def test_a_reconciled_row_is_left_alone(self):
        """Reconciliation is where a booking stops being an import artefact
        and becomes somebody's work."""
        body = _repair_source().split("def _untouched(")[1].split("\ndef ")[0]
        self.assertIn('allocated_amount', body)

    def test_the_last_copy_is_never_deleted(self):
        body = _repair_source().split("def plan(")[1].split("\ndef ")[0]
        self.assertIn("if keeper is None:", body)

    def test_a_booking_doubled_within_one_account_is_left_alone(self):
        """3,079 bookings in the real run sit twice on the SAME account, and
        nothing distinguishes the two rows -- an importer writing a line
        twice and a card charged twice in one day look identical. Deleting
        by document would have taken 18 of them as a side effect of a
        decision that was never about them."""
        body = _repair_source().split("def plan(")[1].split("\ndef ")[0]
        self.assertIn('if row["bank_account"] != keeper["bank_account"]',
                      body)
        self.assertNotIn('if row["name"] != keeper["name"]', body)

    def test_an_undecided_group_loses_nothing(self):
        body = _repair_source().split("def plan(")[1].split("\ndef ")[0]
        undecided = body.split("if keeper is None:")[1].split("\n\n")[0]
        self.assertIn("continue", undecided)

    def test_deleting_needs_the_count_the_plan_reported(self):
        """Not a checkbox. A count that no longer matches means the data
        moved under the plan.

        Read from _carry_out, which is where the machinery lives now: both
        repairs used to carry their own thirty-line copy of it, differing in
        three lines, in a path whose job is to destroy documents.
        """
        body = _repair_source().split("def _carry_out(")[1].split(
            "\n@frappe.whitelist()")[0]
        self.assertIn("cint(confirm) != len(doomed)", body)
        self.assertIn("frappe.throw", body)

    def test_the_plan_itself_writes_nothing(self):
        body = _repair_source().split("def plan(")[1].split("\ndef ")[0]
        for write in ("delete_doc", "db_set", ".save(", ".submit(",
                      "db.commit"):
            self.assertNotIn(write, body, write)

    def test_it_takes_more_than_an_ordinary_write_right(self):
        body = _repair_source().split("def _may_repair(")[1]
        self.assertIn('frappe.only_for("System Manager")', body)
        self.assertIn('ptype="delete"', body)

    def test_what_is_deleted_can_be_got_back(self):
        """The source files of this run are gone. A permanent delete would
        make a wrong rule unrecoverable, so Frappe's Deleted Document archive
        is deliberately left to do its job."""
        body = _repair_source().split("def _carry_out(")[1]
        code = "\n".join(line for line in body.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertIn("frappe.delete_doc(", code)
        self.assertNotIn("delete_permanently", code)

    def test_the_run_is_written_to_the_log_before_it_starts(self):
        body = _repair_source().split("def _carry_out(")[1]
        self.assertIn('frappe.logger("kefiya").info', body)
        self.assertLess(body.index('frappe.logger("kefiya").info'),
                        body.index("frappe.delete_doc"))
