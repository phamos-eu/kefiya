# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Stage 4 of the auto reconciliation: booking transfers we sent ourselves.

The dangerous part is not the booking, it is the matching. A withdrawal booked
against the wrong voucher settles a debt that was never paid, and the error only
surfaces when someone chases a payment that the system believes has arrived.
These tests pin down when a marker counts as found -- and when it does not.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from kefiya.utils.auto_reconcile import _find_marker


def txn(description="", withdrawal=0.0, reference_number=""):
    return frappe._dict(
        {
            "description": description,
            "withdrawal": withdrawal,
            "reference_number": reference_number,
        }
    )


class TestBookOwnTransfers(FrappeTestCase):
    def test_marker_in_the_statement_text(self):
        markers = {"EXP-HR-EXP-2026-00009": 70.40}
        found = _find_marker(
            txn("SEPA UEBERWEISUNG EREF+EXP-HR-EXP-2026-00009 Reisekosten", 70.40),
            markers,
        )
        self.assertEqual(found, "EXP-HR-EXP-2026-00009")

    def test_marker_in_the_reference_number(self):
        markers = {"EXP-HR-EXP-2026-00009": 70.40}
        found = _find_marker(
            txn("SEPA UEBERWEISUNG", 70.40, reference_number="EXP-HR-EXP-2026-00009"),
            markers,
        )
        self.assertEqual(found, "EXP-HR-EXP-2026-00009")

    def test_no_marker_no_booking(self):
        markers = {"EXP-HR-EXP-2026-00009": 70.40}
        self.assertIsNone(_find_marker(txn("MIETE AUGUST", 70.40), markers))

    def test_wrong_amount_is_not_our_payment(self):
        """A marker on a differently sized withdrawal is a coincidence, not a payment."""
        markers = {"EXP-HR-EXP-2026-00009": 70.40}
        self.assertIsNone(
            _find_marker(txn("EREF+EXP-HR-EXP-2026-00009", 704.00), markers)
        )

    def test_cent_difference_is_tolerated(self):
        markers = {"EXP-HR-EXP-2026-00009": 70.40}
        self.assertEqual(
            _find_marker(txn("EREF+EXP-HR-EXP-2026-00009", 70.401), markers),
            "EXP-HR-EXP-2026-00009",
        )

    def test_two_markers_in_one_transaction_are_left_alone(self):
        """A collective transfer whose split the statement does not show."""
        markers = {"EXP-A": 10.00, "EXP-B": 10.00}
        self.assertIsNone(_find_marker(txn("SAMMLER EXP-A EXP-B", 20.00), markers))

    def test_empty_transaction_text(self):
        self.assertIsNone(_find_marker(txn(None, 70.40), {"EXP-X": 70.40}))
