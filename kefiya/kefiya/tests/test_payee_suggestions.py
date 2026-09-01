# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The recipient field suggests known payees -- which it never did.

The form has advertised it the whole time: "Known payees are suggested while
you type". The field called

    input.autocomplete({ source: names, minLength: 2 })

which is the jQuery UI widget. Frappe does not ship jQuery UI, so the call
landed on undefined, threw nothing, and did nothing. A comment beside it even
recorded the reason it had to be that way -- that a Frappe Autocomplete would
refuse an unknown name -- which is a reason for not using a control, not a
reason for calling one that is not there.

Two things had to be true for the fix, and both are checked here: the
suggestions must come from the app rather than from a Server Script stored on
one site, and a name that is not in the list must stay typeable, because a
transfer with no invoice behind it is what this document exists for.
"""

import os
import re
import unittest


def _app_path(*parts):
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)


def _python(*parts):
    """Read the module as text.

    frappe is not installed where these run, and every claim below is about
    the source anyway -- that the two sides of one rule say the same thing.
    """
    with open(_app_path(*parts), encoding="utf-8") as handle:
        return handle.read()


def _controller(name):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, "public", "js", "controllers", name),
              encoding="utf-8") as handle:
        return handle.read()


class TestTheSuggestionsComeFromTheApp(unittest.TestCase):

    @staticmethod
    def _endpoint():
        body = _python("utils", "payee_check.py")
        return body.split("def known_payees(")[1]

    def test_there_is_an_endpoint_and_the_desk_may_call_it(self):
        body = _python("utils", "payee_check.py")
        self.assertIn("@frappe.whitelist()\ndef known_payees(", body)

    def test_it_is_gated_on_the_right_to_enter_a_transfer(self):
        """The answer enumerates payees and IBANs this company has paid.
        That is not something a reader without that right should be able to
        collect one guess at a time."""
        source = self._endpoint()
        self.assertIn("has_permission", source)
        self.assertIn("throw=True", source)

    def test_the_row_budget_cannot_be_raised_from_the_browser(self):
        """It is whitelisted, so `limit` arrives from outside: limit=1e8 was
        two unbounded ordered scans on demand, and limit=abc was a 500."""
        source = self._endpoint()
        self.assertIn("min(cint(limit) or PAYEE_SCAN, PAYEE_SCAN)", source)

    def test_it_reads_the_same_two_sources_as_the_check(self):
        """What is suggested and what is later judged must not disagree."""
        source = self._endpoint()
        self.assertIn("Kefiya Transfer Item", source)
        self.assertIn("Bank Transaction", source)

    def test_it_answers_with_the_ibans_as_well(self):
        self.assertIn('"ibans"', self._endpoint())

    def test_the_page_no_longer_asks_a_stored_script(self):
        body = _controller("transfer_form.js") + _controller("payee_check.js")
        self.assertNotIn("zk_payees", body)
        self.assertIn("kefiya.utils.payee_check.known_payees", body)


class TestAnUnknownNameStaysTypeable(unittest.TestCase):
    """The document exists for the transfer that has no invoice behind it. A
    control that refuses an unknown payee would refuse exactly that."""

    def test_the_recipient_is_still_a_free_field(self):
        body = _controller("transfer_form.js")
        recipient = body.split('fieldname: "recipient_name"')[0]
        self.assertTrue(recipient.rstrip().endswith('fieldtype: "Data",'),
                        "The recipient must stay a Data field.")

    def test_the_widget_that_does_not_exist_is_gone(self):
        body = _controller("transfer_form.js")
        self.assertNotIn(".autocomplete({", body)

    def test_the_suggestions_need_no_library_at_all(self):
        """The first fix reached for window.Awesomplete because Frappe uses
        awesomplete itself -- through `import Awesomplete from "awesomplete"`,
        which esbuild scopes to its own modules. Nothing puts it on window,
        so that would have been the same silent nothing with a new reason."""
        body = _controller("payee_check.js")
        code = "\n".join(line for line in body.splitlines()
                         if not line.lstrip().startswith("//"))
        self.assertNotIn("Awesomplete", code)
        self.assertIn("datalist", code)

    def test_a_failed_fetch_does_not_silence_the_list_for_the_session(self):
        """The promise was cached before it resolved, so one connection blip
        left every later dialog with an empty list until a page reload."""
        body = _controller("payee_check.js")
        rule = body.split("kefiya.known_payees = function")[1] \
            .split("\n};")[0]
        catch = rule.split(".catch(")[1]
        self.assertIn("kefiya._known_payees = null", catch)


class TestAPayeeIsMatchedByTheRuleTheCheckUses(unittest.TestCase):
    """A plain lowercased comparison was not that rule. The history holds
    "Sofienstraße GmbH & Co. KG", this invoice says "Sofienstrasse GmbH",
    and check_payee calls those the same payee -- so a suggestion list that
    disagreed offered no IBAN for a payee it had just called known."""

    def test_the_browser_has_the_same_noise_words_as_the_server(self):
        body = _controller("payee_check.js")
        listed = body.split("kefiya.PAYEE_NOISE = [")[1].split("];")[0]
        in_js = set(re.findall(r'"([^"]+)"', listed))
        body = _python("utils", "payee_check.py")
        in_python = set(re.findall(
            r'"([^"]+)"', body.split("NOISE = {")[1].split("}")[0]))
        self.assertEqual(in_js, in_python)

    def test_the_lookup_normalises_before_it_compares(self):
        body = _controller("payee_check.js")
        rule = body.split("kefiya.payee_named = function")[1] \
            .split("\n};")[0]
        self.assertIn("normalise_payee_name", rule)
        self.assertNotIn("toLowerCase", rule)

    def test_a_subset_counts_and_a_single_shared_word_does_not(self):
        """The same two cases names_match() calls "exact" and "close"."""
        body = _controller("payee_check.js")
        rule = body.split("kefiya.same_payee_name = function")[1] \
            .split("\n};")[0]
        self.assertIn("subset", rule)


class TestPickingAPayeeOffersTheirIban(unittest.TestCase):
    """The half that saves the typing -- and the half that catches the swap,
    because an IBAN offered out of our own history is one we have paid."""

    def test_the_ibans_of_the_picked_payee_are_offered(self):
        body = _controller("transfer_form.js")
        self.assertIn("offerTheirIbans", body)

    def test_a_typed_iban_is_never_overwritten(self):
        """It is the one taken off the invoice in front of the person."""
        body = _controller("transfer_form.js")
        rule = body.split("const offerTheirIbans = function")[1] \
            .split("\n\t};")[0]
        self.assertIn(
            'if (kefiya.iban_plain(dialog.get_value("recipient_iban")))'
            ' return false;', rule)

    def test_one_iban_is_filled_in_and_several_are_offered_as_buttons(self):
        """Opening a dropdown on an empty field shows nothing -- the browser
        has no prefix to filter by -- so "we know two IBANs for them" would
        have been said by opening an empty box."""
        body = _controller("transfer_form.js")
        rule = body.split("const offerTheirIbans = function")[1] \
            .split("\n\t};")[0]
        self.assertIn("ibans.length === 1", rule)
        self.assertIn("data-iban", rule)
        self.assertNotIn(".open()", rule)

    def test_the_check_runs_once_for_one_click(self):
        """set_value fires the IBAN field's own onchange, which IS the check.
        Asking again beside it was a second round trip for one action."""
        body = _controller("transfer_form.js")
        handler = body.split(
            "dialog.fields_dict.recipient_name.df.onchange = function")[1] \
            .split("\n\t};")[0]
        self.assertIn("if (!offerTheirIbans(", handler)

    def test_a_slower_answer_cannot_overwrite_a_newer_one(self):
        """Two checks are in flight when the name changes and the IBAN is
        then filled from it."""
        body = _controller("transfer_form.js")
        rule = body.split("const checkPayee = function")[1].split("\n\t};")[0]
        self.assertIn("if (mine !== asked) return;", rule)
