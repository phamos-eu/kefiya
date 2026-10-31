# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Dated and instant transfers, offered the same way in both forms.

Three things were wrong at once, and they had one root: the two places a
transfer can be entered did not share what they knew.

  * The outgoing-payments dialog never asked the bank what the account allows.
    It offered a dated order to an account whose bank takes none, and the
    refusal arrived from the bank -- after a TAN had been spent on it.
  * The document form did ask, and answered by HIDING the option. A box that
    vanishes teaches nobody anything: the reader cannot tell a missing feature
    from a lost right from a broken form.
  * Both described the same options in their own words, in their own file.
"""

import os
import unittest


def _read(*parts):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


FORM = ("kefiya", "doctype", "kefiya_transfer", "kefiya_transfer.js")
DIALOG = ("public", "js", "controllers", "transfer_form.js")
CAPS = ("public", "js", "controllers", "account_capabilities.js")


class TestBothFormsAskTheBank(unittest.TestCase):

    def test_the_capability_reader_is_available_to_every_page(self):
        """It used to be pulled into one doctype by an include."""
        bundle = _read("public", "js", "kefiya.bundle.js")
        self.assertIn('import "./controllers/account_capabilities";', bundle)
        self.assertNotIn("account_capabilities.js\" %}", _read(*FORM),
                         "Bundled and included is the same file twice.")

    def test_the_outbox_dialog_asks_too(self):
        dialog = _read(*DIALOG)
        self.assertIn("kefiya.capabilities.load(payer.login)", dialog)
        self.assertIn("const applyCapabilities = function (payer)", dialog)

    def test_only_the_bank_held_date_can_be_refused(self):
        """"As soon as possible" is an ordinary transfer and holding the date
        here is our own doing -- neither is the bank's to refuse."""
        body = _read(*DIALOG).split("const applyCapabilities")[1][:1200]
        self.assertIn('if (m.value !== "bank") return;', body)

    def test_a_refused_option_is_refused_before_the_tan(self):
        """Not after the bank has taken one for an order it then rejects."""
        body = _read(*DIALOG).split("kefiya.transfer_form_submit = ")[1]
        self.assertIn("dialog.refused_modes[mode.label]", body)
        self.assertIn("dialog.instant_refused", body)


class TestARefusedOptionSaysWhy(unittest.TestCase):
    """A greyed-out box with no reason is only marginally better than a hidden
    one; a vanished one is worse than both."""

    def test_the_document_form_no_longer_hides_it(self):
        form = _read(*FORM)
        self.assertNotIn('frm.toggle_display("instant_payment", instant_ok)',
                         form)
        self.assertIn('frm.set_df_property("instant_payment", "read_only"',
                      form)

    def test_the_reason_names_the_bank_and_the_transaction(self):
        body = _read(*CAPS).split("refusal_reason: function")[1][:900]
        self.assertIn("The bank does not allow", body)

    def test_a_collective_order_is_explained_as_its_own_transaction(self):
        """Several payments go out as one collective order, which the bank
        rules on separately -- that is why the option can disappear when a
        second row is added."""
        body = _read(*CAPS).split("refusal_reason: function")[1][:900]
        self.assertIn("count > 1", body)
        self.assertIn("collective order", body)

    def test_both_forms_use_that_one_reason(self):
        for parts in (FORM, DIALOG):
            self.assertIn("kefiya.capabilities.refusal_reason",
                          _read(*parts), parts[-1])


class TestOneWordingForBothForms(unittest.TestCase):
    """Describing the same option in two files is how they drift apart."""

    def test_the_hints_have_a_single_source(self):
        dialog = _read(*DIALOG)
        self.assertIn("kefiya.execution_hint = function (mode)", dialog)
        self.assertIn("kefiya.EXECUTION_MODES.instant_hint", dialog)

    def test_the_document_form_reads_from_it(self):
        form = _read(*FORM)
        self.assertIn('kefiya.execution_hint("instant")', form)
        self.assertIn('kefiya.execution_hint("bank")', form)
        self.assertIn('kefiya.execution_hint("here")', form)

    def test_no_form_carries_its_own_copy_of_the_text(self):
        form = _read(*FORM)
        self.assertNotIn("The bank does not accept dated orders on this "
                         "account", form)
        self.assertNotIn("Arrives within seconds", form)
