# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import inspect
import unittest

import frappe

from kefiya.utils import client, fints_segments
from kefiya.utils.fints_controller import FinTSController


class TestTheLibraryGapIsReal(unittest.TestCase):
    """python-fints can send a transfer now and read the dated orders a bank
    already holds -- it cannot hand one over. These segments close that gap,
    and this test fails the day the library closes it itself."""

    def test_the_library_still_lacks_the_dated_transfer(self):
        from fints.segments import transfer as lib

        for name in ("HKCSE1", "HKCME1"):
            self.assertFalse(
                hasattr(lib, name),
                "python-fints now ships {0}; use the library's segment and "
                "delete ours rather than keeping two.".format(name))

    def test_ours_mirror_the_immediate_ones(self):
        """HKCSE differs from HKCCS in what the bank does with it, not in what
        goes on the wire -- the date lives inside the pain message."""
        from fints.segments.transfer import HKCCM1, HKCCS1

        self.assertEqual(list(fints_segments.HKCSE1._fields.keys()),
                         list(HKCCS1._fields.keys()))
        self.assertEqual(list(fints_segments.HKCME1._fields.keys()),
                         list(HKCCM1._fields.keys()))

    def test_they_are_registered_with_the_library(self):
        """Defining the class IS the registration; without it the bank's
        answer parses into a nameless segment and the order id is lost."""
        from fints.segments.base import FinTS3Segment

        registered = {c.__name__ for c in FinTS3Segment._all_subclasses()}
        for name in ("HKCSE1", "HICSE1", "HKCME1", "HICME1"):
            self.assertIn(name, registered)

    def test_the_parameter_segment_name_matches_the_standard(self):
        """_find_highest_supported_command derives it from the command type,
        so a wrong type name looks for a segment no bank ever sends."""
        for cls, expected in ((fints_segments.HKCSE1, "HICSES"),
                              (fints_segments.HKCME1, "HICMES")):
            derived = "{0}I{1}S".format(cls.TYPE[0], cls.TYPE[2:])
            self.assertEqual(derived, expected)


class TestTheDatedTransferIsSentProperly(unittest.TestCase):
    def _source(self):
        return inspect.getsource(FinTSController._send_scheduled_transfer)

    def test_it_reuses_the_library_send_path(self):
        """TAN and Verification of Payee must work exactly as they do for an
        immediate transfer -- this is money leaving the account."""
        source = self._source()
        self.assertIn("_send_pay_with_possible_retry", source)
        self.assertIn("_continue_sepa_transfer", source)

    def test_the_date_is_not_a_segment_field(self):
        source = self._source()
        self.assertIn("ReqdExctnDt", source,
                      "The comment has to say where the date actually is.")

    def test_the_order_id_is_read(self):
        source = self._source()
        self.assertIn("read_task_id", source)

    def test_a_collective_order_carries_its_control_sum(self):
        source = self._source()
        self.assertIn("seg.sum_amount.amount = control_sum", source)

    def test_single_booking_is_not_demanded(self):
        """Asking for it without being able to read single_booking_allowed is
        how a whole payment run gets rejected."""
        source = self._source()
        self.assertNotIn("seg.request_single_booking = True", source)

    def test_instant_and_scheduled_are_refused_together(self):
        source = inspect.getsource(FinTSController.submit_sepa_transfer)
        self.assertIn("scheduled and instant_payment", source)


class TestWhoHoldsTheDueDate(unittest.TestCase):
    """Ticked, the payment waits here; unticked, the bank holds it. Nothing
    may be paid before its day by either route."""

    def test_the_field_exists_with_the_safe_default(self):
        meta = frappe.get_meta("Kefiya Transfer")
        field = meta.get_field("manage_due_date")
        self.assertIsNotNone(field)
        self.assertEqual(
            field.default, "1",
            "Keeping it here is the reversible option and has to be the "
            "default: an order at the bank can only be changed at the bank.")

    def test_the_bank_reference_is_kept(self):
        meta = frappe.get_meta("Kefiya Transfer")
        self.assertIsNotNone(meta.get_field("bank_task_id"))

    def test_the_status_tells_the_two_apart(self):
        options = (frappe.get_meta("Kefiya Transfer")
                   .get_field("status").options or "").split("\n")
        self.assertIn("Scheduled at Bank", options)
        self.assertIn("Due", options)

    def test_nothing_is_paid_before_its_day(self):
        source = inspect.getsource(client.submit_kefiya_transfer)
        self.assertIn("_not_due_yet(doc)", source)

    def test_an_order_at_the_bank_is_not_sent_twice(self):
        source = inspect.getsource(client.submit_kefiya_transfer)
        self.assertIn('doc.status == "Scheduled at Bank"', source)

    def test_a_batch_may_not_mix_the_two(self):
        """One message carries one execution date."""
        source = inspect.getsource(client.send_transfer_outbox)
        self.assertIn("if len(scheduling) > 1:", source)
        self.assertIn("len({str(doc.execution_date) for doc in docs}) > 1",
                      source)

    def test_a_bank_without_dated_orders_does_not_pay_today_instead(self):
        for func in (client.submit_kefiya_transfer, client.send_transfer_outbox):
            source = inspect.getsource(func)
            self.assertIn("_is_unsupported_schedule(exc)", source)
            self.assertIn('db_set("manage_due_date", 1)', source)


class TestTheDailyRunPresentsAndDoesNotPay(unittest.TestCase):
    """A credit transfer needs a TAN. A scheduler that tried to send would
    leave a parked challenge behind at four in the morning and still not have
    paid anybody."""

    def _source(self):
        return inspect.getsource(client.present_due_transfers)

    def test_it_never_contacts_the_bank(self):
        self.assertNotIn("submit_sepa_transfer", self._source())

    def test_it_only_touches_what_it_may(self):
        source = self._source()
        self.assertIn('"status": "Approved"', source)
        self.assertIn('"manage_due_date": 1', source)
        self.assertIn('"on_hold": 0', source)

    def test_it_catches_up_on_overdue_orders(self):
        self.assertIn('("<=", nowdate())', self._source())

    def test_it_is_registered_with_the_scheduler(self):
        from kefiya import hooks

        daily = hooks.scheduler_events.get("daily") or []
        self.assertIn("kefiya.utils.client.present_due_transfers", daily)

    def test_announcing_twice_does_not_pile_up_todos(self):
        source = inspect.getsource(client._announce_due_transfer)
        self.assertIn('frappe.db.exists("ToDo"', source)
