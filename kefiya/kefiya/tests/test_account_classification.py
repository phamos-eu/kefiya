# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The rule that decides whether the books and the bank disagree.

Runs without a site: is_misclassified() takes the two values and nothing else,
which is the whole reason it is a separate function from the query around it.
"""

import unittest

from kefiya.utils.ledger_rule import (
    LIQUID, NOT_LIQUID, is_misclassified, is_stated)


class TestWhatCountsAsCash(unittest.TestCase):

    def test_a_giro_account_as_bank_is_exactly_right(self):
        self.assertFalse(is_misclassified("Current Account", LIQUID))

    def test_savings_as_bank_is_right_too(self):
        self.assertFalse(is_misclassified("Savings", LIQUID))

    def test_a_share_deposit_counted_as_cash_is_the_complaint(self):
        # The case that started this: 500 EUR that exist and cannot be spent,
        # sitting under "Bankkonten" and adding to the liquidity.
        self.assertTrue(is_misclassified("Cooperative Shares", LIQUID))

    def test_a_guarantee_counted_as_cash_is_worse(self):
        # A granted line was never money at all.
        self.assertTrue(is_misclassified("Guarantee / Credit Line", LIQUID))

    def test_a_loan_counted_as_cash(self):
        self.assertTrue(is_misclassified("Loan", LIQUID))

    def test_a_securities_account_counted_as_cash(self):
        self.assertTrue(is_misclassified("Securities Account", LIQUID))

    def test_the_same_kinds_booked_correctly_are_not_flagged(self):
        # Four of the five Darlehen on this instance already sit as a
        # liability. The check must be silent about those, or the one that
        # does not disappears in the noise.
        for kind in NOT_LIQUID:
            self.assertFalse(is_misclassified(kind, "Current Liability"), kind)
            self.assertFalse(is_misclassified(kind, None), kind)

    def test_a_credit_card_is_never_flagged(self):
        # Whether a card sits in the books as a bank account or as a liability
        # is a convention, not a fact. Flagging it would be an opinion dressed
        # up as a finding.
        self.assertNotIn("Credit Card", NOT_LIQUID)
        self.assertFalse(is_misclassified("Credit Card", LIQUID))

    def test_an_unknown_kind_is_not_flagged(self):
        self.assertFalse(is_misclassified("Sparbuch", LIQUID))

    def test_the_list_still_matches_the_kinds_the_app_knows(self):
        """NOT_LIQUID is spelled out, so it can go stale. This is the guard.

        Read out of account_kind.py as text rather than imported: that module
        pulls in frappe, and the point of the rule living apart is that it can
        be checked without a site.
        """
        import os
        import re

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "utils", "account_kind.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        def literal(name):
            hit = re.search(r"^%s = (.+)$" % name, source, re.M)
            self.assertIsNotNone(hit, name)
            return hit.group(1)

        kinds = re.search(r"^KINDS = \((.+?)\)$", source, re.M | re.S).group(1)
        names = [n.strip() for n in kinds.split(",") if n.strip()]
        known = {n: literal(n).strip().strip('"') for n in names}

        payment = re.search(r"^PAYMENT_KINDS = \((.+?)\)$",
                            source, re.M | re.S).group(1)
        pays = {known[n.strip()] for n in payment.split(",") if n.strip()}

        self.assertEqual(sorted(NOT_LIQUID),
                         sorted(set(known.values()) - pays),
                         "account_kind.py grew or lost a kind. Decide whether "
                         "the books may count it as cash, then update "
                         "NOT_LIQUID.")


class TestTheDefaultIsNotAnAnswer(unittest.TestCase):
    """account_kind defaults to "Current Account" and kind_of() returns it for
    every login nobody configured. Reading that as "this is a giro account"
    moved eight Volksbank Darlehen into the payment accounts -- and into the
    liquidity -- when a first version of the Finanzübersicht grouping trusted
    the field in both directions."""

    def test_a_non_payment_kind_was_chosen_by_somebody(self):
        for kind in NOT_LIQUID:
            self.assertTrue(is_stated(kind), kind)

    def test_a_payment_kind_may_just_be_the_default(self):
        for kind in ("Current Account", "Savings", "Credit Card"):
            self.assertFalse(is_stated(kind), kind)

    def test_nothing_at_all_is_not_an_answer_either(self):
        self.assertFalse(is_stated(None))
        self.assertFalse(is_stated(""))
