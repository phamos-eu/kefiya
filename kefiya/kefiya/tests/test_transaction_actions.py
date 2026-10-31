# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Three things one does with a booking: print it, pass it on, mark it.

A Bank Transaction could be opened and read and that was the end of it. These
are the rules the two new endpoints keep -- above all that neither of them
lets a caller past the permissions of the document they name.
"""

import inspect
import unittest

from kefiya.utils import transaction_actions


class TestTheNamesTheBrowserSends(unittest.TestCase):
    """The list view sends a JSON array, the form sends one string."""

    def test_a_single_name(self):
        self.assertEqual(transaction_actions._names("BT-1"), ["BT-1"])

    def test_a_json_array(self):
        self.assertEqual(transaction_actions._names('["BT-1", "BT-2"]'),
                         ["BT-1", "BT-2"])

    def test_a_python_list(self):
        self.assertEqual(transaction_actions._names(["BT-1"]), ["BT-1"])

    def test_broken_json_is_taken_as_one_name(self):
        """Better a name that does not exist -- and says so -- than a silent
        no-op on something the user watched themselves select."""
        self.assertEqual(transaction_actions._names("[BT-1"), ["[BT-1"])

    def test_nothing_is_nothing(self):
        for empty in (None, "", [], {}):
            self.assertEqual(transaction_actions._names(empty), [], empty)

    def test_empty_entries_drop_out(self):
        self.assertEqual(transaction_actions._names(["BT-1", "", None]),
                         ["BT-1"])


class TestNeitherEndpointIsOpen(unittest.TestCase):

    def test_marking_needs_write_on_each_document(self):
        source = inspect.getsource(transaction_actions.set_followup)
        self.assertIn('frappe.has_permission("Bank Transaction", ptype="write"',
                      source)
        self.assertIn("doc=name", source,
                      "Checked per document: a User Permission can allow one "
                      "company's bookings and not another's.")

    def test_the_check_comes_before_the_write(self):
        source = inspect.getsource(transaction_actions.set_followup)
        self.assertLess(source.index("has_permission"), source.index("db_set"))

    def test_forwarding_needs_read(self):
        source = inspect.getsource(
            transaction_actions.transfer_from_transaction)
        self.assertIn('ptype="read"', source)
        self.assertIn("throw=True", source)

    def test_forwarding_creates_nothing(self):
        """It hands back values. What opens is an unsaved draft, so a
        mis-click costs nothing."""
        source = inspect.getsource(
            transaction_actions.transfer_from_transaction)
        for writing in (".insert(", ".save(", ".submit("):
            self.assertNotIn(writing, source)


class TestWhatIsCarriedOverFromTheBooking(unittest.TestCase):

    def test_an_incoming_amount_is_preferred(self):
        source = inspect.getsource(
            transaction_actions.transfer_from_transaction)
        self.assertIn('flt(txn.get("deposit")) or flt(txn.get("withdrawal"))',
                      source)

    def test_the_purpose_is_cut_to_what_sepa_takes(self):
        self.assertEqual(transaction_actions.PURPOSE_LIMIT, 140)
        source = inspect.getsource(
            transaction_actions.transfer_from_transaction)
        self.assertIn("[:PURPOSE_LIMIT]", source)

    def test_a_missing_login_is_reported_not_guessed(self):
        source = inspect.getsource(transaction_actions._login_for)
        self.assertIn("return rows[0][\"name\"] if rows else None", source)
