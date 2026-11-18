# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A transfer the bank has booked is sent -- the statement decides.

16.11.2026: an order was booked by the bank and kefiya kept calling it
"Not sent". These tests pin down when a booking counts as that order.
"""

import os
import unittest

from kefiya.utils.transfers_seen_at_bank import find_booking


def txn(description, withdrawal, reference_number="", name="BT-1"):
    return {"name": name, "description": description,
            "withdrawal": withdrawal, "reference_number": reference_number}


class TestFindBooking(unittest.TestCase):

    def test_the_id_and_the_amount_find_it(self):
        booking = txn("RG 02.07.2026 EREF: KEF-TRF-2099-00056-1 IBAN: X", 20.83)
        self.assertIs(find_booking("KEF-TRF-2099-00056-1", 20.83, [booking]),
                      booking)

    def test_the_reference_field_counts_as_well(self):
        booking = txn("SEPA", 20.83, reference_number="KEF-TRF-2099-00056-1")
        self.assertIs(find_booking("KEF-TRF-2099-00056-1", 20.83, [booking]),
                      booking)

    def test_a_longer_id_is_another_order(self):
        booking = txn("EREF: KEF-TRF-2099-00056-10", 20.83)
        self.assertIsNone(
            find_booking("KEF-TRF-2099-00056-1", 20.83, [booking]))

    def test_another_amount_is_another_payment(self):
        booking = txn("EREF: KEF-TRF-2099-00056-1", 20.84)
        self.assertIsNone(
            find_booking("KEF-TRF-2099-00056-1", 20.83, [booking]))

    def test_two_candidates_are_no_answer(self):
        bookings = [txn("EREF: KEF-TRF-2099-00056-1", 20.83, name="a"),
                    txn("EREF: KEF-TRF-2099-00056-1", 20.83, name="b")]
        self.assertIsNone(
            find_booking("KEF-TRF-2099-00056-1", 20.83, bookings))

    def test_no_id_finds_nothing(self):
        self.assertIsNone(find_booking("", 20.83, [txn("", 20.83)]))


def _read(*parts):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestItRunsAfterEveryImport(unittest.TestCase):

    def test_before_the_settings_gate(self):
        body = _read("utils", "auto_reconcile.py").split(
            "def run_after_import(")[1].split("\ndef ")[0]
        self.assertLess(body.index("transfers_seen_at_bank.mark_seen("),
                        body.index("auto_reconcile_after_import"))

    def test_a_held_order_is_left_alone(self):
        """The bank holds it until its date; "Sent" would be wrong."""
        from kefiya.utils import transfers_seen_at_bank as seen
        self.assertNotIn("Scheduled at Bank", seen.OPEN_STATES)
        self.assertIn("Due", seen.OPEN_STATES)

    def test_the_reminders_are_closed(self):
        source = _read("utils", "transfers_seen_at_bank.py")
        self.assertIn('"ToDo"', source)
        self.assertIn('"Closed"', source)
