# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What the entry form says an account holds -- and when it must say nothing.

Its own file rather than a class in test_transfer_sources, for a dull reason
that cost this fix its first run: that module imports account_kind at the top,
frappe is not installed where these tests run, and the whole file therefore
never loads. Two tests written into it were dead the moment they were added
and reported nothing. This one reads the source and runs.

What it guards: the balance shown beside the paying account. The module says,
in its own docstring, that "an account nobody has fetched has no balance at
all rather than a balance of zero" -- and then let a stored 0.00 through as a
fact, because it only checked for None. On this instance that is 788 of 839
accounts: custom_account_balance defaults to 0 and last_integration_date has
never been written by anything. So somebody entering a transfer was told the
account held 0,00 EUR in the same confident line used for the account that
really holds 664.028,54.
"""

import os
import unittest


def _source(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return handle.read()


def _standing():
    return _source("utils", "account_kind.py").split(
        "def account_standing(")[1].split("\ndef ")[0]


class TestAnUnfetchedAccountSaysNothing(unittest.TestCase):

    def test_a_zero_nobody_fetched_is_not_a_balance(self):
        body = _standing()
        self.assertIn(
            'if not row.get("last_integration_date") and not balance:', body)

    def test_it_answers_none_rather_than_zero(self):
        """None is what the caller renders as "no balance has been fetched
        for this account yet"; 0 is what it renders as money."""
        body = _standing()
        rule = body.split(
            'if not row.get("last_integration_date") and not balance:')[1]
        self.assertTrue(rule.lstrip().startswith("balance = None"))

    def test_a_fetched_zero_stays_a_zero(self):
        """Deliberately narrow: only the untouched pair -- no date AND
        exactly zero -- counts as unknown. An account a fetch really did
        report as empty keeps its zero, because hiding that would be the same
        mistake pointing the other way."""
        body = _standing()
        self.assertIn('not row.get("last_integration_date") and not balance',
                      body)
        self.assertNotIn("if not balance:\n", body)

    def test_the_caller_still_has_something_to_render(self):
        """The form checks for null/undefined, so the two sides have to agree
        on what "unknown" looks like."""
        js = _source("public", "js", "controllers", "transfer_form.js")
        rule = js.split("kefiya.account_standing_html = function")[1] \
            .split("\n};")[0]
        self.assertIn("payer.balance === null", rule)
        self.assertIn("No balance has been fetched", rule)


class TestTheDateIsPartOfTheAnswer(unittest.TestCase):
    """These figures are written by a fetch, so their age is the difference
    between a fact and a guess."""

    def test_the_standing_carries_when_it_was_read(self):
        self.assertIn('"as_of": row.get("last_integration_date")',
                      _standing())

    def test_a_missing_field_does_not_break_the_answer(self):
        """account_currency does not exist on every instance -- it does not
        on this one -- so the read is built from what the meta actually has."""
        body = _standing()
        self.assertIn("meta.has_field(f)", body)
