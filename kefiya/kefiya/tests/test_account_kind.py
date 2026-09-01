# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Not every entry a bank lists is an account you can pay from.

Two Avale and two share deposits were being treated like current accounts: the
balance was written onto the Bank Account and then counted backwards over
bookings that do not exist. The number the bank states for a guarantee is the
granted line; for a share deposit it is an amount held and not available. These
are the rules that keep each of them out of the wrong column.
"""

import inspect
import unittest

from kefiya.utils import account_kind, fetch_persistence


class TestWhatKindOfAccountThisIs(unittest.TestCase):

    def test_an_unset_login_is_a_payment_account(self):
        """The classification may take accounts out of the balance logic. It
        must never quietly pull a new one in."""
        self.assertEqual(account_kind.kind_of(None), account_kind.GIRO)

    def test_an_unknown_value_falls_back(self):
        doc = type("D", (), {"account_kind": "Bloedsinn"})()
        self.assertEqual(account_kind.kind_of(doc), account_kind.GIRO)

    def test_a_document_is_read_like_a_name(self):
        doc = type("D", (), {"account_kind": account_kind.GUARANTEE})()
        self.assertEqual(account_kind.kind_of(doc), account_kind.GUARANTEE)

    def test_the_kinds_are_distinct(self):
        self.assertEqual(len(set(account_kind.KINDS)), len(account_kind.KINDS))

    def test_only_payment_accounts_keep_a_running_balance(self):
        for kind in account_kind.KINDS:
            doc = type("D", (), {"account_kind": kind})()
            self.assertEqual(
                account_kind.keeps_a_running_balance(doc),
                kind in (account_kind.GIRO, account_kind.SAVINGS,
                         account_kind.CREDIT_CARD),
                kind)

    def test_only_a_guarantee_reports_a_line(self):
        reporting = [k for k in account_kind.KINDS
                     if account_kind.reports_a_credit_line(
                         type("D", (), {"account_kind": k})())]
        self.assertEqual(reporting, [account_kind.GUARANTEE])

    def test_a_loan_a_guarantee_and_a_share_are_not_liquidity(self):
        for kind in (account_kind.LOAN, account_kind.GUARANTEE,
                     account_kind.SHARES, account_kind.SECURITIES):
            doc = type("D", (), {"account_kind": kind})()
            self.assertFalse(account_kind.counts_towards_liquidity(doc), kind)


class TestTheBalanceGoesIntoTheRightField(unittest.TestCase):
    """A guarantee line in the balance field would land in every total that
    adds balances up."""

    def test_a_guarantee_is_stored_as_a_line(self):
        source = inspect.getsource(fetch_persistence.store_balance)
        self.assertIn("account_kind.reports_a_credit_line(login)", source)
        self.assertIn("if meta.has_field(\"custom_account_balance\") "
                      "and not is_a_line:", source)

    def test_the_bank_s_own_line_wins(self):
        """Where the bank states a line explicitly, that is the line -- the
        fallback only fills in where it said nothing."""
        source = inspect.getsource(fetch_persistence.store_balance)
        self.assertIn("line = row.get(\"line_of_credit\")", source)
        self.assertIn("if line is None and is_a_line:", source)

    def test_the_kind_is_reported_back(self):
        source = inspect.getsource(fetch_persistence.store_balance)
        self.assertIn('"account_kind": account_kind.kind_of(login)', source)


class TestNothingIsCountedBackOverBookingsThatDoNotExist(unittest.TestCase):

    def test_the_guard_is_there(self):
        source = inspect.getsource(fetch_persistence.apply_running_balance)
        self.assertIn(
            "if not account_kind.keeps_a_running_balance(kefiya_login):",
            source)

    def test_it_runs_before_anything_is_written(self):
        source = inspect.getsource(fetch_persistence.apply_running_balance)
        guard = source.index("keeps_a_running_balance")
        writing = source.index("db_set")
        self.assertLess(guard, writing,
                        "The guard has to return before the first db_set.")

    def test_the_reason_names_the_kind(self):
        source = inspect.getsource(fetch_persistence.apply_running_balance)
        self.assertIn('result["account_kind"]', source)
