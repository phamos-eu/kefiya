# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A Payment Entry made from a booking moves money at the bank's rate.

Example throughout: a company that books in EUR, a bank account in CHF, and
the bank's statement saying 1 EUR = 0.94 CHF for the conversion it did.
"""

import os
import unittest

from kefiya.utils.bank_exchange_rate import (
    FIELDS, invoice_allocation, payment_entry_values)

EUR_CHF = {"from": "EUR", "to": "CHF", "rate": 0.94}


class TestTwoCurrenciesOneRate(unittest.TestCase):

    def test_a_euro_customer_paying_into_the_franc_account(self):
        values = payment_entry_values("Receive", 940.0, "CHF", "EUR", "EUR",
                                      EUR_CHF)
        self.assertAlmostEqual(values["paid_amount"], 1000.0)
        self.assertEqual(values["received_amount"], 940.0)
        self.assertEqual(values["source_exchange_rate"], 1.0)
        self.assertAlmostEqual(values["target_exchange_rate"], 1 / 0.94)

    def test_paying_a_euro_supplier_from_the_franc_account(self):
        values = payment_entry_values("Pay", 470.0, "CHF", "EUR", "EUR",
                                      EUR_CHF)
        self.assertEqual(values["paid_amount"], 470.0)
        self.assertAlmostEqual(values["received_amount"], 500.0)
        self.assertAlmostEqual(values["source_exchange_rate"], 1 / 0.94)
        self.assertEqual(values["target_exchange_rate"], 1.0)

    def test_a_franc_customer_paying_into_the_franc_account(self):
        values = payment_entry_values("Receive", 940.0, "CHF", "CHF", "EUR",
                                      EUR_CHF)
        self.assertEqual(values["paid_amount"], 940.0)
        self.assertAlmostEqual(values["source_exchange_rate"], 1 / 0.94)
        self.assertAlmostEqual(values["target_exchange_rate"], 1 / 0.94)

    def test_both_sides_agree_in_the_company_currency(self):
        """What makes the entry balance: no gain or loss nobody had."""
        for payment_type in ("Receive", "Pay"):
            for party in ("EUR", "CHF"):
                v = payment_entry_values(payment_type, 1234.56, "CHF", party,
                                         "EUR", EUR_CHF)
                self.assertAlmostEqual(
                    v["paid_amount"] * v["source_exchange_rate"],
                    v["received_amount"] * v["target_exchange_rate"],
                    places=6, msg=(payment_type, party))

    def test_the_sign_of_the_booking_does_not_matter(self):
        self.assertEqual(
            payment_entry_values("Pay", -470.0, "CHF", "EUR", "EUR", EUR_CHF),
            payment_entry_values("Pay", 470.0, "CHF", "EUR", "EUR", EUR_CHF))

    def test_every_field_is_decided(self):
        values = payment_entry_values("Receive", 1.0, "CHF", "EUR", "EUR",
                                      EUR_CHF)
        self.assertEqual(sorted(values), sorted(FIELDS))


class TestWhereTheBanksRateDoesNotDecide(unittest.TestCase):

    def test_three_currencies_are_left_to_erpnext(self):
        """CHF bank, USD party, EUR company: the bank's EUR/CHF rate next to
        two system rates would not balance."""
        self.assertIsNone(payment_entry_values(
            "Receive", 940.0, "CHF", "USD", "EUR", EUR_CHF))

    def test_a_rate_about_other_currencies_is_not_used(self):
        self.assertIsNone(payment_entry_values(
            "Receive", 940.0, "CHF", "EUR", "EUR",
            {"from": "USD", "to": "CHF", "rate": 0.8}))

    def test_one_currency_has_nothing_to_convert(self):
        self.assertIsNone(payment_entry_values(
            "Receive", 100.0, "EUR", "EUR", "EUR", EUR_CHF))

    def test_without_a_rate_nothing_changes(self):
        self.assertIsNone(payment_entry_values(
            "Receive", 940.0, "CHF", "EUR", "EUR", None))

    def test_an_unknown_currency_or_type_changes_nothing(self):
        self.assertIsNone(payment_entry_values(
            "Receive", 940.0, "CHF", None, "EUR", EUR_CHF))
        self.assertIsNone(payment_entry_values(
            "Internal Transfer", 940.0, "CHF", "EUR", "EUR", EUR_CHF))


def _source(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return handle.read()


class TestThePaymentEntryWrapper(unittest.TestCase):
    """Read as text: the override needs ERPNext to load."""

    def setUp(self):
        text = _source("overrides", "bank_reconciliation_tool",
                       "bank_reconciliation_tool.py")
        self.body = text[text.index("def custom_create_payment_entry_bts"):]

    def test_it_is_installed_over_erpnexts_endpoint(self):
        hooks = _source("hooks.py")
        self.assertIn(
            '"erpnext.accounts.doctype.bank_reconciliation_tool.'
            'bank_reconciliation_tool.create_payment_entry_bts": '
            '"kefiya.overrides.bank_reconciliation_tool.'
            'bank_reconciliation_tool.custom_create_payment_entry_bts"',
            hooks)

    def test_erpnext_builds_the_entry_and_nothing_is_saved_first(self):
        """The permission checks stay ERPNext's; the bank's rate is set on
        the unsaved entry, before insert."""
        built = self.body.index("allow_edit=1")
        rated = self.body.index("pe.set(field, values[field])")
        saved = self.body.index("pe.insert()")
        self.assertLess(built, rated)
        self.assertLess(rated, saved)

    def test_without_a_bank_rate_it_is_erpnexts_function(self):
        early = self.body[:self.body.index("allow_edit=1")]
        self.assertIn("if not bank_fx:", early)
        self.assertIn("return original(bank_transaction_name, "
                      "allow_edit=allow_edit,", early)

    def test_no_request_argument_reaches_erpnext_unnamed(self):
        signature = self.body[:self.body.index("):")]
        self.assertNotIn("**", signature)


class TestABookingPaysAnInvoiceInItsCurrency(unittest.TestCase):
    """940 CHF against a 1.000 EUR invoice, at 1 CHF = 1/0.94 EUR."""

    RATE = 1 / 0.94

    def test_the_booking_settles_the_invoice_exactly(self):
        plan = invoice_allocation(940.0, 1000.0, self.RATE)
        self.assertEqual(plan["bank_amount"], 940.0)
        self.assertEqual(plan["party_amount"], 1000.0)
        self.assertTrue(plan["settles_invoice"])
        self.assertTrue(plan["settles_booking"])

    def test_compared_without_converting_it_would_not(self):
        """What the wizard did: 940 < 1000, invoice left open by 60."""
        plan = invoice_allocation(940.0, 1000.0, 1.0)
        self.assertFalse(plan["settles_invoice"])
        self.assertEqual(plan["remaining_party"], 60.0)

    def test_a_larger_booking_keeps_its_remainder_in_its_currency(self):
        plan = invoice_allocation(1034.0, 1000.0, self.RATE)
        self.assertEqual(plan["party_amount"], 1000.0)
        self.assertEqual(plan["bank_amount"], 940.0)
        self.assertEqual(plan["remaining_bank"], 94.0)
        self.assertTrue(plan["settles_invoice"])
        self.assertFalse(plan["settles_booking"])

    def test_a_smaller_booking_pays_part_of_the_invoice(self):
        plan = invoice_allocation(470.0, 1000.0, self.RATE)
        self.assertEqual(plan["bank_amount"], 470.0)
        self.assertEqual(plan["party_amount"], 500.0)
        self.assertEqual(plan["remaining_party"], 500.0)
        self.assertFalse(plan["settles_invoice"])
        self.assertTrue(plan["settles_booking"])

    def test_equal_to_the_cent_is_equal(self):
        """A nine-decimal rate never lands on the cent by itself."""
        plan = invoice_allocation(940.0, 1000.0, 1.0638297)
        self.assertTrue(plan["settles_invoice"] and plan["settles_booking"])
        self.assertEqual(plan["bank_amount"], 940.0)

    def test_rounding_never_takes_more_than_the_booking_has(self):
        plan = invoice_allocation(940.0, 1000.004, self.RATE)
        self.assertLessEqual(plan["bank_amount"], 940.0)

    def test_one_currency_is_the_old_rule(self):
        plan = invoice_allocation(300.0, 250.0, 1.0)
        self.assertEqual((plan["bank_amount"], plan["party_amount"],
                          plan["remaining_bank"]), (250.0, 250.0, 50.0))

    def test_a_rate_must_be_positive(self):
        with self.assertRaises(ValueError):
            invoice_allocation(1.0, 1.0, 0)


class TestTheWizardReconcilesInBothCurrencies(unittest.TestCase):
    """Read as text: client.py needs a bench to load."""

    def setUp(self):
        text = _source("utils", "client.py")
        self.client = text
        self.create = text[text.index("def create_payment_entry("):
                           text.index("def _same_currency_for_the_card")]
        self.wizard = _source("kefiya", "page", "bank_transaction_wiz",
                              "bank_transaction_wiz.js")

    def test_erpnext_is_told_the_bank_account_and_both_amounts(self):
        """Without the bank account ERPNext took the company's default one,
        in that one's currency."""
        for argument in ("party_amount=plan.party_amount",
                         "bank_account=plan.gl_account",
                         "bank_amount=(plan.bank_amount"):
            self.assertIn(argument, self.create)

    def test_the_account_is_no_longer_swapped_afterwards(self):
        self.assertNotIn("payment_entry.paid_to = gl_account", self.create)
        self.assertNotIn("payment_entry.paid_from = gl_account", self.create)

    def test_confirmation_and_booking_share_one_plan(self):
        preview = self.client[self.client.index(
            "def preview_invoice_payment"):self.client.index(
            "def _allocate_references")]
        self.assertIn("invoice_payment_plan(bank_transaction, invoice_doc)",
                      preview)
        self.assertIn("invoice_payment_plan(bank_transaction, invoice_doc)",
                      self.create)

    def test_the_bank_rate_is_set_before_the_entry_is_saved(self):
        self.assertLess(self.create.index("payment_entry_values("),
                        self.create.index("payment_entry.insert()"))

    def test_a_partial_payment_is_not_allocated_per_term_twice(self):
        self.assertIn("_allocate_references(payment_entry.references, "
                      "plan.party_amount)", self.create)
        self.assertNotIn("reference.allocated_amount = paid_amount",
                         self.create)

    def test_the_card_reconciliation_refuses_a_foreign_currency(self):
        card = self.create[:self.create.index("_wizard_documents(")]
        self.assertLess(
            card.index("_same_currency_for_the_card(invoice_doc"),
            card.index("create_mastercard_journal_entry_from_purchase_"
                       "invoice(invoice_doc"))

    def test_the_wizard_shows_a_booking_in_its_own_currency(self):
        self.assertIn('"currency"\n\t\t\t];', self.wizard)
        self.assertIn("format_currency(payment.unallocated_amount, "
                      "payment.currency || this.data.currency)", self.wizard)

    def test_the_wizard_no_longer_compares_across_currencies(self):
        self.assertNotIn("unallocated_amount > outstanding_amount",
                         self.wizard)
        self.assertIn("result.settles_invoice", self.wizard)

    def test_a_foreign_booking_is_always_confirmed_with_its_rate(self):
        self.assertIn("kefiya.utils.client.preview_invoice_payment",
                      self.wizard)
        self.assertIn("if (!(show_dialog || foreign))", self.wizard)


if __name__ == "__main__":
    unittest.main()
