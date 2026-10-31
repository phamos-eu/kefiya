# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Two fetches writing to Bank Accounts at the same time must not kill each
other.

On 15.08 the collective run reported

    <a cooperative bank access>
    Saldo  Fehler: (1213, 'Deadlock found when trying to get lock')

once for the balance and five times for the capability list. Neither write is
contentious in itself -- one stores a number, the other a timestamp. The lock
came from the save: Bank Account.validate() calls update_default_bank_account(),
which issues

    UPDATE `tabBank Account` SET is_default = 0
    WHERE company = ... AND is_default = 1 AND disabled = 0 ...

-- a range lock over every account of the company, taken on every save of a
default account. The collective fetch runs the bank accesses in parallel, so two
of those range updates met on the same rows in opposite order and MariaDB killed
one of them.

So neither write takes that lock any more, and the one save that genuinely
remains gives way instead of giving up.
"""

import inspect
import unittest

import frappe

from kefiya.utils import account_capabilities as caps
from kefiya.utils import fetch_persistence


class TestTheBalanceDoesNotLockTheCompany(unittest.TestCase):

    def test_it_writes_the_fields_not_the_document(self):
        source = inspect.getsource(fetch_persistence.store_balance)
        self.assertIn("db_set(", source)
        self.assertNotIn(
            ".save(", source,
            "save() runs validate(), and validate() locks every account of "
            "the company to store one balance.")

    def test_it_writes_nothing_when_there_is_nothing_to_write(self):
        """An instance without the custom fields used to save the account
        anyway -- a range lock for no change at all."""
        source = inspect.getsource(fetch_persistence.store_balance)
        self.assertIn("if values:", source)


class TestTheCapabilityListOnlyStampsTheDate(unittest.TestCase):

    def test_an_unchanged_list_does_not_go_through_a_save(self):
        source = inspect.getsource(caps.store_for_account)
        head, _, tail = source.partition("if not changed:")
        self.assertTrue(tail, "The unchanged case must be handled apart.")
        unchanged_branch = tail.split("return True")[0]
        self.assertIn("db_set(", unchanged_branch)
        self.assertNotIn(
            ".save(", unchanged_branch,
            "The bank repeats its capability list on nearly every fetch. "
            "Saving for the timestamp is what took the lock dozens of times "
            "per run.")

    def test_the_stamp_does_not_touch_modified(self):
        """Rewriting `modified` for a field nobody edited would show the
        account as changed in every list sorted by it."""
        source = inspect.getsource(caps.store_for_account)
        self.assertIn("update_modified=False", source)

    def test_a_changed_list_still_goes_through_the_document(self):
        """A child table cannot be written field by field, and this is the
        path where validation is actually wanted."""
        source = inspect.getsource(caps.store_for_account)
        self.assertIn("_write_rows_through_deadlock(", source)


class TestTheRemainingSaveGivesWay(unittest.TestCase):

    @staticmethod
    def _code():
        """The helper's body without its docstring -- which discusses the very
        calls these tests assert are absent."""
        source = inspect.getsource(caps._write_rows_through_deadlock)
        return source.replace(caps._write_rows_through_deadlock.__doc__ or "", "")

    def test_it_retries_on_a_deadlock(self):
        code = self._code()
        self.assertIn("frappe.QueryDeadlockError", code)
        self.assertIn("time.sleep(", code)

    def test_it_never_rolls_back_the_caller(self):
        """A rollback here would discard the transactions this fetch just
        imported, to fix one account's capability list."""
        self.assertNotIn("db.rollback(", self._code())

    def test_it_re_reads_the_document_on_every_attempt(self):
        """A save that was rolled back leaves the in-memory copy holding child
        rows the database no longer has."""
        loop = self._code().partition("for attempt in range(attempts):")[2]
        self.assertIn('frappe.get_doc("Bank Account", bank_account)', loop)

    def test_the_last_failure_is_still_reported(self):
        """Swallowing it would report a capability list that was never
        written."""
        self.assertIn("raise", self._code())

    def test_it_gives_up_after_a_bounded_number_of_attempts(self):
        """A bank dialog is open while this runs; an unbounded retry would
        hold it until the bank drops it."""
        signature = inspect.signature(caps._write_rows_through_deadlock)
        attempts = signature.parameters["attempts"].default
        self.assertTrue(1 < attempts <= 5, attempts)


class TestTheRetryActuallyRuns(unittest.TestCase):
    """Behaviour, not shape: a deadlock on the first attempt must end in a
    written document, and a deadlock on every attempt must end in an error."""

    def setUp(self):
        self.original_get_doc = frappe.get_doc
        self.addCleanup(setattr, frappe, "get_doc", self.original_get_doc)
        self.saves = 0

    def _install(self, failures):
        outer = self

        class FakeMeta:
            def has_field(self, fieldname):
                return True

        class FakeAccount:
            meta = FakeMeta()

            def set(self, *args, **kwargs):
                pass

            def append(self, *args, **kwargs):
                pass

            def save(self, **kwargs):
                outer.saves += 1
                if outer.saves <= failures:
                    raise frappe.QueryDeadlockError(
                        "(1213, 'Deadlock found when trying to get lock')")

        frappe.get_doc = lambda *args, **kwargs: FakeAccount()

    def test_a_single_deadlock_is_survived(self):
        self._install(failures=1)
        caps._write_rows_through_deadlock("Girokonto - Genobank eG", [])
        self.assertEqual(self.saves, 2, "It must try again, and only once.")

    def test_a_permanent_deadlock_is_reported(self):
        self._install(failures=99)
        with self.assertRaises(frappe.QueryDeadlockError):
            caps._write_rows_through_deadlock("Girokonto - Genobank eG", [])
        self.assertEqual(self.saves, 3)
