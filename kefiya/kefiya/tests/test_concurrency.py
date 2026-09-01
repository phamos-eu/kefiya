# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import inspect
import unittest

from kefiya.utils import auto_reconcile, client


class TestSendLockIsAtomic(unittest.TestCase):
    """The send lock decides whether real money leaves the account twice.

    It used to be a read followed by a write, with a window in between: a
    double click, or the outbox and the single send firing together, could both
    find the key free and both go on to pay the same transfer.
    """

    def test_the_claim_is_a_single_atomic_operation(self):
        source = inspect.getsource(client._claim_send_lock)
        self.assertIn(
            "nx=True", source,
            "SET NX decides and claims in one operation; a get followed by a "
            "set does not.",
        )
        self.assertIn(
            "ex=seconds", source,
            "The lock must expire on its own, or a crashed request blocks the "
            "transfer forever.",
        )

    def test_the_key_matches_the_release(self):
        source = inspect.getsource(client._claim_send_lock)
        self.assertIn(
            "cache.make_key(lock_key)", source,
            "delete_value() and get_value() prefix the key with the site name; "
            "claiming a raw key would create a second, unreleasable one.",
        )

    def test_no_send_path_reads_the_lock_before_claiming(self):
        for fn in (client.submit_payment_request_via_fints,
                   client.submit_kefiya_transfer,
                   client.send_transfer_outbox):
            source = inspect.getsource(fn)
            self.assertNotIn(
                "cache().get_value(lock", source,
                "{0} must claim the lock, not check it -- checking first is "
                "the race.".format(fn.__name__),
            )
            self.assertIn("_claim_send_lock(", source)

    def test_the_claim_happens_before_the_bank_is_contacted(self):
        for fn in (client.submit_payment_request_via_fints,
                   client.submit_kefiya_transfer,
                   client.send_transfer_outbox):
            source = inspect.getsource(fn)
            self.assertLess(
                source.index("_claim_send_lock("),
                source.index("FinTSController("),
                "Claiming after the transfer went out would not prevent "
                "anything in {0}.".format(fn.__name__),
            )

    def test_a_partial_batch_claim_is_rolled_back(self):
        """Locks left behind on documents this send never covers would block
        them for ten minutes for no reason."""
        source = inspect.getsource(client.send_transfer_outbox)
        self.assertIn("claimed = []", source)
        self.assertIn(
            "for done in claimed:", source,
            "All or nothing: release what was already claimed before refusing.",
        )


class TestParallelReconcileIsSerialised(unittest.TestCase):
    """The collective fetch now runs one chain per bank side by side.

    Matching reads an invoice's outstanding amount and then allocates against
    it. Sequentially that is safe -- the first allocation lowers the
    outstanding, so the second no longer matches. In parallel two chains can
    read the same outstanding before either writes, and an invoice payable from
    two accounts would be allocated twice.
    """

    def _source(self):
        return inspect.getsource(auto_reconcile.run_after_import)

    def test_matching_runs_under_a_lock(self):
        source = self._source()
        self.assertIn("filelock", source)
        self.assertIn('"kefiya_auto_reconcile"', source)

    def test_the_lock_covers_both_stages(self):
        source = self._source()
        lock_at = source.index("with filelock(")
        self.assertLess(lock_at, source.index("_alyf_auto_reconcile("))
        self.assertLess(lock_at, source.index("_create_advance_payments("))

    def test_the_lock_does_not_cover_the_bank_dialog(self):
        """Holding it across the fetch would serialise the whole run and undo
        the parallelism it exists to make safe. It is only ever entered from
        run_after_import, which the controller calls once the import is
        submitted and committed -- with no FinTS command left to send."""
        from kefiya.utils.fints_controller import FinTSController

        caller = inspect.getsource(FinTSController.import_fints_transactions)
        self.assertLess(
            caller.index("curr_doc.submit()"),
            caller.index("run_after_import("),
            "Reconciliation must run after the import is booked, not while "
            "the dialog is still open.",
        )

    def test_a_timeout_never_fails_the_import(self):
        source = self._source()
        self.assertIn("except Exception:", source)
        self.assertIn(
            "frappe.log_error", source,
            "The transactions are booked either way; a lock timeout must be "
            "visible but harmless.",
        )
