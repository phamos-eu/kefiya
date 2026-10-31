# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import inspect
import unittest

import frappe

from kefiya.utils import client, transfer_limit
from kefiya.utils.fints_controller import FinTSController


class TestTheLimitComesFromTheBank(unittest.TestCase):
    """Guessing it is worse than not knowing it: a bank refuses the WHOLE
    order when it exceeds the limit, not the part above it."""

    def test_it_is_read_off_the_account_information(self):
        source = inspect.getsource(FinTSController.get_fints_limits)
        self.assertIn('upd.find_segments("HIUPD")', source)
        self.assertIn("account_limit", source)
        self.assertIn(
            "allowed_transactions", source,
            "The per-transaction limit is the one that binds a transfer; the "
            "account-wide one is the fallback.",
        )

    def test_it_costs_no_extra_command(self):
        """The limit rides on the logon information, so reading it inside the
        shared session is free -- that is why it can run on every fetch."""
        source = inspect.getsource(FinTSController.get_fints_limits)
        self.assertIn("with self.client_session() as conn:", source)
        for command in ("HKSAL", "HKKAZ", "HKCAZ", "_send_with_possible_retry"):
            self.assertNotIn(command, source)

    def test_only_this_login_s_account_counts(self):
        """A login often carries several accounts; another account's limit
        says nothing about what this one may send."""
        source = inspect.getsource(FinTSController.get_fints_limits)
        self.assertIn("seg_iban != iban", source)

    def test_a_transfer_limit_beats_the_account_limit(self):
        source = inspect.getsource(transfer_limit.pick_binding_limit)
        self.assertIn("TRANSFER_SEGMENTS", source)
        self.assertIn('r.get("scope") == "transaction"', source)
        self.assertIn(
            'min(pool, key=lambda r: flt(r["amount"]))', source,
            "Among equals the smaller one wins -- it is the one that gets hit.",
        )

    def test_the_transfer_segments_are_the_ones_that_move_money(self):
        for segment in ("HKCCS", "HKCCM", "HKCSE", "HKCME", "HKIPZ", "HKIPM"):
            self.assertIn(segment, transfer_limit.TRANSFER_SEGMENTS)
        self.assertNotIn(
            "HKKAZ", transfer_limit.TRANSFER_SEGMENTS,
            "A limit on a statement retrieval does not bind a transfer.",
        )

    def test_it_is_refreshed_on_every_fetch(self):
        source = inspect.getsource(client.fetch_all)
        self.assertIn("refresh_transfer_limit", source)
        self.assertIn('"transfer_limit", "transfer_limit"', source)

    def test_the_fields_exist_on_the_login(self):
        meta = frappe.get_meta("Kefiya Login")
        for field in ("transfer_limit_amount", "transfer_limit_type",
                      "transfer_limit_days", "transfer_limit_checked_on"):
            self.assertTrue(meta.has_field(field), field)

    def test_a_failed_read_is_told_apart_from_no_limit(self):
        """"The bank named no limit" and "we never asked" look identical on
        the document otherwise, and only one of them is a reason to let a
        large order through."""
        source = inspect.getsource(transfer_limit.refresh_transfer_limit)
        self.assertIn("login.transfer_limit_checked_on = now_datetime()",
                      source)


class TestNothingLeavesOverTheLimit(unittest.TestCase):
    def test_both_send_paths_check(self):
        for func in (client.submit_kefiya_transfer, client.send_transfer_outbox):
            self.assertIn("_refuse_over_limit(", inspect.getsource(func))

    def test_a_batch_is_measured_as_a_whole(self):
        """Judging each document on its own would wave through ten orders
        that each fit and together do not."""
        source = inspect.getsource(client.send_transfer_outbox)
        self.assertIn("_refuse_over_limit(docs)", source)
        self.assertIn("check_batch", inspect.getsource(client._refuse_over_limit))

    def test_what_already_went_out_counts(self):
        source = inspect.getsource(transfer_limit.check_batch)
        self.assertIn("committed_amount(", source)
        self.assertIn("available = limit - used", source)

    def test_a_held_order_is_not_counted(self):
        """It is not going out, so its money is not spoken for."""
        self.assertNotIn("On Hold", transfer_limit.COMMITTED_STATUSES)
        for status in ("Approved", "Due", "Scheduled at Bank", "Sent"):
            self.assertIn(status, transfer_limit.COMMITTED_STATUSES)

    def test_the_documents_being_judged_do_not_count_against_themselves(self):
        source = inspect.getsource(transfer_limit.check_batch)
        self.assertIn("exclude=[d.name for d in docs]", source)

    def test_an_unknown_limit_blocks_nothing_and_claims_nothing(self):
        source = inspect.getsource(transfer_limit.check_batch)
        self.assertIn('"reason": "no limit known"', source)

    def test_a_per_order_limit_is_judged_per_order(self):
        """The bank sees one message per order even when they go together."""
        source = inspect.getsource(transfer_limit.check_batch)
        self.assertIn("if limit_type == LIMIT_SINGLE:", source)
        self.assertIn("too_big = [d for d in docs", source)


class TestSplitting(unittest.TestCase):
    """Splitting money across days is a decision, so it happens on request and
    leaves documents you can see."""

    def test_only_a_draft_can_be_split(self):
        source = inspect.getsource(transfer_limit.split_over_limit)
        self.assertIn("if doc.docstatus != 0:", source)

    def test_it_is_permission_gated(self):
        source = inspect.getsource(transfer_limit.split_over_limit)
        self.assertIn('ptype="write"', source)

    def test_the_original_document_survives(self):
        """Its name, its history and anything referring to it stay valid."""
        source = inspect.getsource(transfer_limit.split_over_limit)
        self.assertIn("keep = plan[0]", source)
        self.assertIn("created.append(doc.name)", source)

    def test_a_single_payment_over_the_limit_is_refused_not_mangled(self):
        source = inspect.getsource(transfer_limit.split_over_limit)
        self.assertIn("if plan is None:", source)
        self.assertIn("raised with the", source)

    def test_the_end_to_end_id_is_not_copied(self):
        """It names the order it belongs to, and a bank uses it to recognise a
        payment it has already seen. A copy would reach the bank naming an
        order it is not in -- and twice."""
        source = inspect.getsource(transfer_limit._clean_row)
        self.assertIn('"end_to_end_id"', source)

    def test_the_days_skip_the_weekend(self):
        source = inspect.getsource(transfer_limit._next_banking_day)
        self.assertIn("date.weekday() < 5", source)

    def test_the_order_of_the_payments_is_kept(self):
        """Reordering somebody's payment run to save a day is not a trade they
        agreed to."""
        source = inspect.getsource(transfer_limit.plan_split)
        self.assertNotIn("sort", source)
        self.assertIn("for index, amount in enumerate(amounts):", source)
