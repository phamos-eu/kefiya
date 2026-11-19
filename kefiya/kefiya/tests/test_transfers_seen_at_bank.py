# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A transfer the bank has booked is sent -- the statement decides.

16.11.2026: an order was booked by the bank and kefiya kept calling it
"Not sent". These tests pin down when a booking counts as that order.
"""

import os
import unittest

from kefiya.utils.transfers_seen_at_bank import (
    find_booking,
    find_booking_without_id,
)


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


IBAN = "DE89999999990001111111"


def plain(description, withdrawal=10000.0, date="2026-11-19", iban=IBAN,
          name="BT-9"):
    return {"name": name, "description": description,
            "withdrawal": withdrawal, "date": date,
            "bank_party_iban": iban, "reference_number": "a1b2"}


class TestWithoutTheId(unittest.TestCase):
    """18.11.2026: a bank writes no End-To-End ID into its statement. The
    order was released in the app, booked, and stayed "Due" here."""

    def find(self, txns, purpose="Geldtransit an Sparkasse",
             due="2026-11-19", amount=10000.0, iban=IBAN):
        return find_booking_without_id(iban, amount, purpose, due, txns)

    def test_iban_amount_purpose_and_date_find_it(self):
        booking = plain("Geldtransit an SparkasseDATUM 18.11.2026, 19.43 UHR")
        self.assertIs(self.find([booking]), booking)

    def test_another_iban_is_another_payment(self):
        booking = plain("Geldtransit an Sparkasse",
                        iban="DE58999999990002222222")
        self.assertIsNone(self.find([booking]))

    def test_another_amount_is_another_payment(self):
        self.assertIsNone(self.find([plain("Geldtransit an Sparkasse",
                                           withdrawal=9999.99)]))

    def test_the_purpose_has_to_be_there(self):
        self.assertIsNone(self.find([plain("Miete November")]))

    def test_a_short_purpose_is_no_evidence(self):
        self.assertIsNone(self.find([plain("Miete")], purpose="Miete"))

    def test_far_from_the_date_is_not_it(self):
        self.assertIsNone(self.find([plain("Geldtransit an Sparkasse",
                                           date="2026-10-01")]))

    def test_two_candidates_are_no_answer(self):
        self.assertIsNone(self.find([
            plain("Geldtransit an Sparkasse", name="a"),
            plain("Geldtransit an Sparkasse", name="b")]))


class TestOnlyWhereItCanBeToldApart(unittest.TestCase):

    def test_only_an_order_with_one_payment(self):
        source = _read("utils", "transfers_seen_at_bank.py")
        self.assertIn("payments.get(item.parent) == 1", source)

    def test_a_booking_is_used_once(self):
        source = _read("utils", "transfers_seen_at_bank.py")
        self.assertIn("used.add(txn.get(\"name\"))", source)
