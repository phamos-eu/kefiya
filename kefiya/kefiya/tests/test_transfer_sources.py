# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""From which accounts a transfer may start.

The payer dropdown offered five loans and a credit card. Three filters were
meant to prevent that -- the Bank Account's account_type, a link to a Property
Loan, and the bank's own HIUPD capability list -- and all three missed: the
account_type is not maintained on those records, the loan link is not either,
and HIUPD is never fetched for an account nobody fetches.

The kind was right on every one of them the whole time. Nothing asked.
"""

import inspect
import os
import unittest

from kefiya.utils import account_kind


class TestWhatMaySendMoney(unittest.TestCase):

    def test_a_loan_cannot_send(self):
        self.assertNotIn(account_kind.LOAN, account_kind.TRANSFER_SOURCE_KINDS)

    def test_a_guarantee_a_share_and_a_depot_cannot_send(self):
        for kind in (account_kind.GUARANTEE, account_kind.SHARES,
                     account_kind.SECURITIES):
            self.assertNotIn(kind, account_kind.TRANSFER_SOURCE_KINDS, kind)

    def test_a_credit_card_cannot_send_either(self):
        """It is paid, it does not pay. This is where the sending kinds are
        narrower than the payment kinds -- a card counts towards liquidity and
        keeps a running balance, but no SEPA transfer starts from it."""
        self.assertIn(account_kind.CREDIT_CARD, account_kind.PAYMENT_KINDS)
        self.assertNotIn(account_kind.CREDIT_CARD,
                         account_kind.TRANSFER_SOURCE_KINDS)

    def test_a_giro_and_a_savings_account_can(self):
        self.assertIn(account_kind.GIRO, account_kind.TRANSFER_SOURCE_KINDS)
        self.assertIn(account_kind.SAVINGS, account_kind.TRANSFER_SOURCE_KINDS)

    def test_every_sending_kind_is_a_real_kind(self):
        for kind in account_kind.TRANSFER_SOURCE_KINDS:
            self.assertIn(kind, account_kind.KINDS, kind)


class TestTheAnswerIsOfferedOnce(unittest.TestCase):
    """So a page does not have to reimplement the rule -- which is how the
    dropdown came to disagree with the app in the first place."""

    def test_there_is_a_helper_for_a_single_login(self):
        self.assertTrue(callable(account_kind.can_send_transfers))
        source = inspect.getsource(account_kind.can_send_transfers)
        self.assertIn("TRANSFER_SOURCE_KINDS", source)

    def test_there_is_an_endpoint_for_the_whole_list(self):
        source = inspect.getsource(account_kind.transfer_sources)
        self.assertIn("TRANSFER_SOURCE_KINDS", source)

    def test_the_list_is_read_with_permissions(self):
        """These are offered for selection; an access the caller may not see
        must not appear in a dropdown."""
        source = inspect.getsource(account_kind.transfer_sources)
        self.assertIn('frappe.get_list(\n        "Kefiya Login"', source)

    def test_a_disabled_account_is_not_offered(self):
        source = inspect.getsource(account_kind.transfer_sources)
        self.assertIn('"disabled": 1', source)

    def test_a_site_without_the_field_still_gets_a_list(self):
        """Before account_kind exists every login is a payment account, which
        is what kind_of() answers anyway. Returning nothing would empty the
        dropdown on an instance that never classified its accounts."""
        source = inspect.getsource(account_kind.transfer_sources)
        self.assertIn('has_field("account_kind")', source)


class TestTheAccountSaysWhatItCanCarry(unittest.TestCase):
    """A balance on another page is a balance nobody looks up.

    It is stated where the account is chosen, because that is the moment it
    decides anything -- an order entered against an account that cannot carry
    it comes back from the bank days later.
    """

    @staticmethod
    def _source(*parts):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), *parts)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_the_overdraft_line_is_part_of_the_answer(self):
        """The half people forget: 664.028,54 on an account with a line of
        250.000,00 means 914.028,54 can leave it."""
        body = self._source("utils", "account_kind.py").split(
            "def account_standing(")[1].split("\ndef ")[0]
        self.assertIn("custom_credit_line", body)
        self.assertIn('standing["available"] = float(balance)', body)

    def test_an_unfetched_account_has_no_balance_rather_than_zero(self):
        body = self._source("utils", "account_kind.py").split(
            "def account_standing(")[1].split("\ndef ")[0]
        self.assertIn("if balance is not None:", body,
                      "A confident zero on an account nobody fetched is worse "
                      "than saying nothing.")

    def test_the_age_of_the_figures_travels_with_them(self):
        """These come from a fetch; their age is the difference between a fact
        and a guess."""
        body = self._source("utils", "account_kind.py").split(
            "def account_standing(")[1].split("\ndef ")[0]
        self.assertIn('"as_of"', body)
        for parts in (("public", "js", "controllers", "transfer_form.js"),
                      ("kefiya", "doctype", "kefiya_transfer",
                       "kefiya_transfer.js")):
            self.assertIn("as at {0}", self._source(*parts), parts[-1])

    def test_the_fields_are_optional(self):
        """They are Custom Fields on one instance; an app that requires them
        fails on every other."""
        body = self._source("utils", "account_kind.py").split(
            "def account_standing(")[1].split("\ndef ")[0]
        self.assertIn("meta.has_field(f)", body)

    def test_the_outbox_shows_it_where_the_account_is_picked(self):
        form = self._source("public", "js", "controllers", "transfer_form.js")
        self.assertIn("kefiya.account_standing_html", form)
        self.assertIn("dialog.fields_dict.payer.df.onchange = showStanding",
                      form)
