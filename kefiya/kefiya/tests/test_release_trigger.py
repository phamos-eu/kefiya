# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What made the bank ask for a release, and fetching at night what needs none.

18.11.2026: the nightly run fetched the bookings of fourteen Volksbank
accounts without a release; the fetch by hand later that day asked for one per
account. Nothing recorded which request the bank wanted released.
"""

import os
import unittest

from kefiya.utils import release_trigger


class Header:
    def __init__(self, kind):
        self.type = kind


class Segment:
    def __init__(self, kind):
        self.header = Header(kind)


class Answer:
    def __init__(self, kind=None, decoupled=True):
        self.command_seg = Segment(kind) if kind else None
        self.decoupled = decoupled


class TestWhichRequest(unittest.TestCase):

    def test_the_segment_is_named(self):
        self.assertEqual(release_trigger.segment_of(Answer("HKCAZ")), "HKCAZ")

    def test_the_dialog_opening_has_none(self):
        self.assertEqual(release_trigger.segment_of(Answer()), "")

    def test_anything_odd_reads_as_unknown(self):
        self.assertEqual(release_trigger.segment_of(object()), "")


class TestTheLine(unittest.TestCase):

    def test_everything_needed_to_tell_the_causes_apart(self):
        text = release_trigger.describe(
            Answer("HKKAZ"), login="L", attended=True, earlier_today=1,
            challenge_text="Bitte bestaetigen")
        self.assertIn("request=HKKAZ -- bookings (MT940)", text)
        self.assertIn("attended=1", text)
        self.assertIn("decoupled=1", text)
        self.assertIn("fetches of this login earlier today=1", text)
        self.assertIn("bank: Bitte bestaetigen", text)

    def test_the_opening_says_so(self):
        text = release_trigger.describe(
            Answer(), login="L", attended=False, earlier_today=0)
        self.assertIn("(dialog opening or unknown)", text)
        self.assertIn("attended=0", text)


def _read(*parts):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestItIsWrittenDownEverywhere(unittest.TestCase):

    def test_it_goes_to_the_timeline_not_the_error_log(self):
        """The Error Log is for errors (18.11.2026)."""
        session = _read("utils", "fints_tan_session.py")
        body = session.split("def _note_release_request(")[1].split(
            "\n    def ")[0]
        self.assertNotIn("log_error", body)
        self.assertIn("note_on_login(", body)
        note = session.split("def note_on_login(")[1].split("\nclass ")[0]
        self.assertIn('add_comment(\n            "Info"', note)

    def test_every_prompt_notes_it(self):
        session = _read("utils", "fints_tan_session.py")
        body = session.split("def _publish_tan_prompt(")[1].split(
            "\n    def ")[0]
        self.assertIn("self._note_release_request(", body)

    def test_an_unattended_request_survives_the_rollback(self):
        session = _read("utils", "fints_tan_session.py")
        self.assertIn("frappe.flags.kefiya_release_trigger = message", session)
        schedule = _read("kefiya", "doctype", "kefiya_schedule",
                         "kefiya_schedule.py")
        body = schedule.split("def _log_tan_wanted(")[1].split("\ndef ")[0]
        self.assertIn('frappe.flags.pop("kefiya_release_trigger"', body)
        self.assertIn("note_on_login(login_name, trigger)", body)

    def test_the_opening_never_asks_when_nobody_is_there(self):
        controller = _read("utils", "fints_controller.py")
        body = controller.split("def __init_tan_mode(")[1].split(
            "\n    def ")[0]
        guard = body.index("if not self.interactive.enabled:")
        self.assertLess(guard, body.index("self.ask_for_tan("))


class TestTheNightFetchesWhatNeedsNoTan(unittest.TestCase):

    def setUp(self):
        self.schedule = _read("kefiya", "doctype", "kefiya_schedule",
                              "kefiya_schedule.py")

    def test_the_extras_run_after_the_bookings_are_committed(self):
        loop = self.schedule.split("def scheduled_import_fints_payments(")[1]
        self.assertLess(loop.index("frappe.db.commit()"),
                        loop.index("_fetch_extras_unattended(login_name,"))

    def test_in_one_dialog_and_without_the_depots_twice(self):
        body = self.schedule.split("def _fetch_extras_unattended(")[1].split(
            "\ndef ")[0]
        self.assertIn("client._fetch_session([login_name])", body)
        self.assertIn("include_depots=False", body)
        self.assertIn("except TanInteractionRequired:", body)

    def test_the_fetch_by_hand_reads_the_same(self):
        client = _read("utils", "client.py")
        fetch_all = client.split("def fetch_all(")[1].split("\ndef ")[0]
        self.assertIn("return fetch_extras(kefiya_login, kefiya_import,"
                      " summary)", fetch_all)
