# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A log that reports the wrong thing is worse than no log.

The first collective run after the new protocol went live said "2 neu" for
every one of thirteen accounts, called thirteen accounts' securities query a
broken bank connection, and reported a library defect as a raw Python message.
These are the three corrections.
"""

import inspect
import unittest

from kefiya.utils import client, fetch_persistence, mt940_compat


class TestTheTransactionCountIsTheTransactionCount(unittest.TestCase):
    """import_fints_transactions answers with a DICT --
    {"transactions": [...], "payments": [...]} -- so len() of it counted its
    two keys. Every account reported "2 neu", whether the bank had sent nothing
    or eighteen bookings."""

    def test_the_created_bookings_are_counted(self):
        result = {"transactions": ["a", "b", "c"],
                  "payments": ["BT-1", "BT-2", "BT-3", "BT-4", "BT-5"]}
        self.assertEqual(len(client._created_transactions(result)), 5)

    def test_not_the_keys_of_the_dictionary(self):
        result = {"transactions": ["a"], "payments": []}
        self.assertEqual(
            len(client._created_transactions(result)), 0,
            "Raw material the bank sent is not a booking that was created.")

    def test_nothing_is_nothing(self):
        for empty in (None, {}, {"payments": None}, {"transactions": ["a"]}):
            self.assertEqual(client._created_transactions(empty), [], empty)

    def test_a_list_is_taken_as_it_is(self):
        """So a controller that returns one is counted, not miscounted."""
        self.assertEqual(client._created_transactions(["BT-1", "BT-2"]),
                         ["BT-1", "BT-2"])

    def test_something_unexpected_counts_as_nothing(self):
        self.assertEqual(client._created_transactions("nonsense"), [])

    def test_fetch_all_uses_it(self):
        source = inspect.getsource(client.fetch_all)
        self.assertIn("_created_transactions(new_txns)", source)
        self.assertNotIn(
            "len(new_txns)", source,
            "That is the defect: len() of the result dictionary.")


class TestABankRefusalIsNotABrokenConnection(unittest.TestCase):
    """python-fints turns response code 9010 -- the bank's generic "cannot
    process this order" -- into a message about the bank identifier and the
    BPD, whatever was actually refused. One collective run produced 24 Error
    Log entries from it and told the user every account had a defective
    connection, when the truth was that none of them is a securities account."""

    def _error(self, cls=None, message=None):
        from fints.exceptions import FinTSClientError

        cls = cls or FinTSClientError
        return cls(message or (
            "Error during dialog initialization, could not fetch BPD. Please "
            "check that you passed the correct bank identifier to the HBCI "
            "URL of the correct bank."))

    def test_the_refusal_is_recognised(self):
        self.assertTrue(client._is_refused_by_the_bank(self._error()))

    def test_a_wrong_pin_stays_loud(self):
        from fints.exceptions import FinTSClientPINError

        self.assertFalse(client._is_refused_by_the_bank(
            self._error(FinTSClientPINError)),
            "It is a FinTSClientError subclass and must not slip through as "
            "'the bank does not offer this'.")

    def test_strong_authentication_stays_loud(self):
        from fints.exceptions import FinTSSCARequiredError

        self.assertFalse(client._is_refused_by_the_bank(
            self._error(FinTSSCARequiredError)))

    def test_any_other_failure_stays_an_error(self):
        self.assertFalse(client._is_refused_by_the_bank(
            self._error(message="Account locked")))
        self.assertFalse(client._is_refused_by_the_bank(TypeError("boom")))
        self.assertFalse(client._is_refused_by_the_bank(None))

    def test_it_is_recorded_as_absent_with_a_reason(self):
        source = inspect.getsource(client._optional_fetch)
        self.assertIn("_is_refused_by_the_bank(exc)", source)
        self.assertIn('summary.setdefault("unsupported_details", {})', source)

    def test_a_refusal_writes_no_error_log_entry(self):
        """Twelve identical entries per run buried the failures that matter."""
        source = inspect.getsource(client._optional_fetch)
        refusal = source.index("_is_refused_by_the_bank(exc)")
        logging = source.index("frappe.log_error")
        self.assertLess(
            refusal, logging,
            "The refusal branch has to return before anything is logged.")

    def test_the_statement_download_treats_it_the_same(self):
        source = inspect.getsource(fetch_persistence.download_statements)
        self.assertIn("_is_refused_by_the_bank(exc)", source)
        self.assertIn('result["reason"]', source)


class TestPendingEntriesCanBeReadAtAll(unittest.TestCase):
    """The bank puts a date-and-time indication in the pending block whose
    timezone is optional. A regex group that does not participate still lands
    in the match dictionary as None, and the parser decides what to do with it
    by asking whether the KEY is there -- then calls int(None)."""

    def test_the_repair_installs(self):
        self.assertTrue(mt940_compat.ensure_optional_timezone_is_optional())

    def test_it_is_idempotent(self):
        mt940_compat.ensure_optional_timezone_is_optional()
        self.assertTrue(mt940_compat.ensure_optional_timezone_is_optional())

    def test_a_missing_timezone_parses(self):
        from mt940 import models

        mt940_compat.ensure_optional_timezone_is_optional()
        parsed = models.DateTime(year="26", month="07", day="31", hour="09",
                                 minute="00", offset=None)
        self.assertEqual((parsed.year, parsed.month, parsed.day),
                         (2026, 7, 31))
        self.assertIsNone(parsed.tzinfo,
                          "No timezone given means no timezone, not a made-up "
                          "one.")

    def test_a_given_timezone_survives(self):
        from mt940 import models

        mt940_compat.ensure_optional_timezone_is_optional()
        parsed = models.DateTime(year="26", month="07", day="31", hour="09",
                                 minute="00", offset="0100")
        self.assertIsNotNone(parsed.tzinfo)

    def test_a_real_pending_block_parses(self):
        from fints.utils import mt940_to_array

        mt940_compat.ensure_optional_timezone_is_optional()
        message = "\n".join([
            ":20:STARTUMSE", ":25:67250020/0009289240", ":28C:0",
            ":60F:C260731EUR22433,33",
            ":13D:2607310900",
            ":61:2607310731DR345,10NMSCNONREF",
            ":86:166?00SEPA-UEBERWEISUNG?20Stellplatzmiete",
            ":62F:C260731EUR22088,23", "-",
        ])
        rows = mt940_to_array(message)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0].data.get("amount")), "-345.10 EUR")

    def test_the_pending_fetch_installs_it_before_parsing(self):
        from kefiya.utils.fints_controller import FinTSController

        source = inspect.getsource(
            FinTSController.get_fints_pending_transactions)
        self.assertIn("ensure_optional_timezone_is_optional()", source)

    def test_only_the_broken_case_is_touched(self):
        """Nothing that works today may change; the only thing replaced is an
        exception."""
        source = inspect.getsource(
            mt940_compat.ensure_optional_timezone_is_optional)
        self.assertIn('kwargs.get("offset", "unset") is None', source)
        self.assertIn(
            "models.DateTime.__new__ = original", source,
            "A repair that does not take must put the original back.",
        )
