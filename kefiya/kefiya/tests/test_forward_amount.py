# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Passing money on: four directions, and each one says which it is.

They are not variations of one thing. Who may receive the money differs, and
so does what has to be checked before it is offered -- moving money inside one
company is an internal transfer, moving it to another company is a payment
between two legal entities and should not look like housekeeping.
"""

import inspect
import unittest

from kefiya.utils import transaction_actions as ta


class TestFourDirections(unittest.TestCase):

    def test_there_are_four(self):
        self.assertEqual(len(ta.FORWARD_VARIANTS), 4)

    def test_each_one_has_a_name_a_person_reads(self):
        self.assertEqual(sorted(ta.FORWARD_LABELS), sorted(ta.FORWARD_VARIANTS))

    def test_two_of_them_pick_an_account(self):
        self.assertEqual(set(ta.FORWARD_PICKS_ACCOUNT),
                         {ta.FORWARD_OWN_SAME, ta.FORWARD_OWN_OTHER})

    def test_the_other_two_do_not(self):
        for variant in (ta.FORWARD_OTHER, ta.FORWARD_BACK):
            self.assertNotIn(variant, ta.FORWARD_PICKS_ACCOUNT)


class TestAmountAndPurposeTravelUnchanged(unittest.TestCase):
    """That is what forwarding means: the same money, recognisable on the
    other side."""

    def test_the_recipient_is_the_only_thing_the_variant_changes(self):
        source = inspect.getsource(ta.forward_amount)
        # Every branch starts from the same values.
        self.assertIn("values = transfer_from_transaction(name)", source)
        self.assertNotIn('values["item"]["amount"] =', source)
        self.assertNotIn('values["item"]["purpose"] =', source)


class TestTheVariantIsCheckedNotTrusted(unittest.TestCase):
    """A picker filtered in the browser is a convenience. If the same rule
    does not hold here, "same company" is a label and not a fact."""

    def test_a_foreign_account_is_refused_under_same_company(self):
        source = inspect.getsource(ta.forward_amount)
        self.assertIn("if variant == ta.FORWARD_OWN_SAME and not same:"
                      .replace("ta.", ""), source)

    def test_an_own_account_is_refused_under_other_company(self):
        source = inspect.getsource(ta.forward_amount)
        self.assertIn("if variant == ta.FORWARD_OWN_OTHER and same:"
                      .replace("ta.", ""), source)

    def test_an_unknown_variant_is_refused(self):
        source = inspect.getsource(ta.forward_amount)
        self.assertIn("if variant not in FORWARD_VARIANTS:", source)

    def test_the_target_is_read_checked(self):
        source = inspect.getsource(ta.forward_amount)
        self.assertIn('frappe.has_permission("Bank Account"', source)


class TestNobodyForwardsToTheAccountItIsAlreadyOn(unittest.TestCase):
    """The bank refuses it too -- after the order was sent and a TAN spent."""

    def test_the_picker_leaves_it_out(self):
        source = inspect.getsource(ta.forward_targets)
        self.assertIn('if row["name"] == txn.get("bank_account"):', source)

    def test_and_the_call_refuses_it(self):
        source = inspect.getsource(ta.forward_amount)
        self.assertIn('if target.name == txn.get("bank_account"):', source)


class TestAnotherRecipientStartsEmpty(unittest.TestCase):
    """Leaving the previous counterparty in would be the likeliest wrong
    payment of the four: it looks filled in and it is not the one meant."""

    def test_the_recipient_fields_are_cleared(self):
        source = inspect.getsource(ta.forward_amount)
        block = source.split("if variant == FORWARD_OTHER:")[1]
        self.assertIn('values["item"]["recipient_name"] = ""', block)
        self.assertIn('values["item"]["recipient_iban"] = ""', block)


class TestOnlyAccountsThatCanBePaid(unittest.TestCase):

    def test_disabled_accounts_are_not_offered(self):
        source = inspect.getsource(ta.forward_targets)
        self.assertIn('"disabled": 0', source)

    def test_only_the_companys_own_accounts(self):
        source = inspect.getsource(ta.forward_targets)
        self.assertIn('"is_company_account": 1', source)

    def test_the_list_goes_through_get_all_so_permissions_apply(self):
        source = inspect.getsource(ta.forward_targets)
        self.assertIn("frappe.get_all(", source)
