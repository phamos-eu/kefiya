# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Two business transactions that were confused, and one that was never asked.

  HKDBS  scheduled direct debits -- money we COLLECT, once, on a date
  HKCDB  standing orders -- money we PAY, again and again, on a cycle

The log called the first one "Daueraufträge" and never asked for the second.
Worse, the first could not answer: the library filters the bank's response by
the name of the *request*, which no response ever contains.
"""

import inspect
import unittest

from kefiya.utils import standing_orders
from kefiya.utils.fints_segments import HKCDB1


class TestTheQueryIsBuiltFromWhatTheLibraryShips(unittest.TestCase):

    def test_it_is_hkcdb_version_one(self):
        self.assertEqual((HKCDB1.TYPE, HKCDB1.VERSION), ("HKCDB", 1))

    def test_it_is_hkdbs_without_the_date_range(self):
        """A standing order has no date window to ask for -- it has a cycle."""
        self.assertEqual(
            [f for f in HKCDB1._fields if f != "header"],
            ["account", "supported_sepa_pain_messages",
             "max_number_responses", "touchdown_point"])

    def test_the_answer_is_not_declared(self):
        """The schedule's field order could not be checked against anything
        the library ships. A guess that parses is worse than one that fails:
        it would put the day of execution where the rhythm belongs and look
        entirely plausible doing it."""
        from fints.segments.base import FinTS3Segment

        self.assertEqual(
            [c.__name__ for c in FinTS3Segment._all_subclasses()
             if c.__name__.startswith("HICDB")], [])


class TestTheLibraryQueryCannotAnswer(unittest.TestCase):
    """get_scheduled_debits() asks response_segments() to filter the bank's
    answer by "HKDBS" -- the name of the request. An answer contains HIDBS.
    The filter matches nothing, at every bank, every time."""

    def test_the_defect_is_still_there(self):
        from fints.client import FinTS3Client

        source = inspect.getsource(FinTS3Client.get_scheduled_debits)
        self.assertIn('response_type = "HKDBS"', source,
                      "If this fails the library fixed it and this module's "
                      "single-order path can go back to using it.")

    def test_we_ask_with_what_the_bank_sends(self):
        source = inspect.getsource(standing_orders.fetch_scheduled_debits)
        self.assertIn('"HIDBS"', source)

    def test_nothing_is_patched(self):
        """The library keeps its behaviour; this module simply does not use
        the broken path."""
        source = inspect.getsource(standing_orders)
        for word in ("monkey", "__wrapped__", "setattr(FinTS3Client"):
            self.assertNotIn(word, source)


class TestReadingAnAnswerWithoutGuessing(unittest.TestCase):

    def test_only_the_certain_part_is_read_by_position(self):
        source = inspect.getsource(standing_orders.parse_standing_order)
        self.assertIn("_pain_summary(data[2])", source)
        self.assertIn("tail = data[3:]", source)

    def test_the_schedule_is_marked_unconfirmed(self):
        source = inspect.getsource(standing_orders._schedule_from)
        self.assertIn('"confirmed": False', source)

    def test_the_raw_group_is_kept(self):
        """Where the reading turns out wrong against a real bank, the raw
        values are what says so."""
        source = inspect.getsource(standing_orders._schedule_from)
        self.assertIn('out = {"raw": parts', source)

    def test_the_pain_message_is_read_leniently(self):
        """It arrives as raw bytes from a bank and its shape varies by pain
        version. A strict parse that raises would cost the whole fetch."""
        summary = standing_orders._pain_summary(b"<Document/>")
        self.assertEqual(summary["amount"], 0.0)
        self.assertEqual(summary["recipient_iban"], "")

    def test_nothing_at_all_is_survivable(self):
        self.assertEqual(standing_orders._pain_summary(None), {})
        self.assertEqual(standing_orders._pain_summary(b""), {})


class TestTheLogTellsThemApart(unittest.TestCase):

    def test_the_fetch_is_optional(self):
        """A bank that does not offer HKCDB must not fail the whole run."""
        from kefiya.utils import client

        source = inspect.getsource(client.fetch_all)
        self.assertIn('"standing_orders", "standing_orders"', source)

    def test_the_old_wrong_label_is_gone(self):
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "public", "js", "controllers", "bank_refresh.js")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn('__("Standing orders"), "scheduled_debits"', source)
        self.assertIn('__("Scheduled debits"), "scheduled_debits"', source)
        self.assertIn('__("Standing orders"), "standing_orders"', source)
