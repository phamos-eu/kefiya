# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The branch that turns "3945" into a challenge somebody can answer.

The rule itself takes a response and the segment it was sent with, and says
whether the bank refused to release the order without the payee check being
confirmed. No bank, no library, no site -- which is the point: this decides
what happens to a payment, and a rule that can only be exercised against a
live bank is a rule nobody exercises.

The rest of fints_vop is a copy of a library method with one branch added.
What can be asserted about a copy is that it knows which release it was taken
from and refuses to run against another one, and that is asserted here from
the source text.

WHAT THIS CANNOT CHECK, so that nobody reads it as covered: the imports in
_pieces() only resolve where python-fints is installed, which is a bench and
not this suite. A wrong module path there fails silently in exactly the worst
way -- _pieces() answers None, the guard reports "shape has moved", and the
branch is dead code that looks installed. It happened once already: PSRD1 was
imported from fints.formals, where it is not; it lives in fints.segments.auth.
It was caught by listing the pieces against the installed library before
shipping, and that check is the only thing that catches it.
"""

import os
import unittest

from kefiya.utils.fints_vop import (CONFIRMATION_DEMANDED,
                                    DEFAULT_POLL_SECONDS, POLL_LIMIT_SECONDS,
                                    can_be_approved, demands_confirmation,
                                    poll_pause, readable_verdict)
from kefiya.utils.fints_vop_client import (_codes_in, _poll_note,
                                           touchdown_in)
from kefiya.utils.vop_rule import result_from


class Line:
    def __init__(self, code):
        self.code = code


class Answer:
    """The shape python-fints hands back: responses(segment) -> lines."""

    def __init__(self, lines, explode=False):
        self._lines = lines
        self._explode = explode

    def responses(self, segment):
        if self._explode:
            raise RuntimeError("a response this cannot read")
        return self._lines


class TestTheBankAsksForTheConfirmation(unittest.TestCase):

    def test_3945_is_the_ask(self):
        self.assertTrue(demands_confirmation(
            Answer([Line("3040"), Line("3945")]), object()))

    def test_the_ordinary_answers_are_not(self):
        """0030 and 3955 are TAN requests and the library already handles
        them; 3050 is the commonest remark a German bank makes about a
        transfer. Treating any of them as a payee question would park a
        payment that was going through."""
        for code in ("0010", "0030", "3050", "3955", "9010"):
            self.assertFalse(
                demands_confirmation(Answer([Line(code)]), object()), code)

    def test_an_empty_answer_is_not_the_ask(self):
        self.assertFalse(demands_confirmation(Answer([]), object()))

    def test_a_response_it_cannot_read_answers_no(self):
        """Then the order takes exactly the path it took before this module
        existed. Guessing "yes" would park a payment on a parse error."""
        self.assertFalse(
            demands_confirmation(Answer([], explode=True), object()))

    def test_the_code_is_the_one_the_bank_sent(self):
        self.assertEqual(CONFIRMATION_DEMANDED, "3945")


class TestACopyKnowsWhatItIsACopyOf(unittest.TestCase):

    def _source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "utils", "fints_vop_client.py")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_it_names_the_release_it_was_taken_from(self):
        self.assertIn('KNOWN_GOOD = ("5.0.0b1", "5.0.0")', self._source())

    def test_another_release_gets_the_library_method(self):
        """A stale copy of a payment path is worse than a missing feature."""
        source = self._source()
        self.assertIn("if installed_version() not in KNOWN_GOOD:", source)
        self.assertIn('return parts["base"]', source)

    def test_a_moved_library_shape_gets_the_library_method_too(self):
        source = self._source()
        self.assertIn("if not hasattr(FinTS3PinTanClient, name):", source)
        self.assertIn("return None", source)

    def test_building_the_client_never_raises(self):
        """A bank connection must not fail over an optional branch."""
        source = self._source()
        body = source[source.index("def client_class("):
                      source.index("def _note(")]
        self.assertIn("except Exception:", body)
        self.assertIn("frappe.log_error", body)

    def test_it_says_out_loud_why_it_cannot_send_twice(self):
        """The only question that matters about a change on the payment path.
        3945 means the bank did not release the order, so turning that into a
        parked challenge cannot send anything a second time -- and the
        approval that follows is a person's deliberate act."""
        self.assertIn("WHY THIS CANNOT SEND TWICE", self._source())


class Evpe:
    def __init__(self, result=None):
        self.result = result


class Hivpp:
    """What the bank hands back. Three shapes turn up in practice."""

    def __init__(self, polling_id=None, wait_for_seconds=None, inner=None,
                 report=None, vop_id=None):
        self.polling_id = polling_id
        self.wait_for_seconds = wait_for_seconds
        self.vop_single_result = inner
        self.payment_status_report = report
        self.vop_id = vop_id


class TestWhatMakesAnAnswerReleasable(unittest.TestCase):
    """One thing, and it is not the verdict: the VoP-ID.

    The approval segment is HKVPA(vop_id=...) and nothing else. This was asked
    the other way round -- "is the bank still checking", reading the verdict
    fields -- and at this bank that question has a permanent answer. Its BPD
    says::

        ParameterVoP(report_complete='V',
                     supported_report_formats='...pain.002.001.10')

    'V' means the complete payment status report: a pain.002 document in
    HIVPP.payment_status_report, which is mutually exclusive with
    vop_single_result -- and vop_single_result is where every verdict reader
    here and in python-fints looks. The library's client.py mentions
    payment_status_report zero times. So the verdict was never readable, the
    old predicate was permanently true, and no transfer could go out.
    """

    def test_a_vop_id_is_what_releases_an_order(self):
        self.assertTrue(can_be_approved(Hivpp(vop_id=b"587d03b8")))

    def test_without_one_there_is_nothing_to_release_with(self):
        self.assertFalse(can_be_approved(Hivpp(polling_id=b"x",
                                               wait_for_seconds=2)))
        self.assertFalse(can_be_approved(None))
        self.assertFalse(can_be_approved(object()))

    def test_a_verdict_alone_does_not_make_it_releasable(self):
        """It reads well and cannot be sent: HKVPA carries the VoP-ID."""
        self.assertFalse(can_be_approved(Hivpp(inner=Evpe("RVMC"))))

    def test_a_report_this_app_cannot_read_is_still_releasable(self):
        """The whole point. The reviewer becomes the check, and the dialog
        says so -- but the order can go out."""
        answer = Hivpp(vop_id=b"587d03b8", report=b"<Document/>")
        self.assertTrue(can_be_approved(answer))
        self.assertEqual(readable_verdict(answer), "")

    def test_the_verdict_is_read_where_the_bank_puts_one(self):
        self.assertEqual(readable_verdict(Hivpp(inner=Evpe("RVMC"))), "RVMC")
        self.assertEqual(readable_verdict(Hivpp()), "")
        self.assertEqual(readable_verdict(None), "")

    def test_the_bank_names_the_pause(self):
        self.assertEqual(poll_pause(Hivpp(wait_for_seconds=2)), 2.0)

    def test_an_absurd_pause_is_brought_back_into_range(self):
        """Neither a busy loop nor a request nobody waits out."""
        self.assertEqual(poll_pause(Hivpp(wait_for_seconds=0)),
                         float(DEFAULT_POLL_SECONDS))
        self.assertEqual(poll_pause(Hivpp(wait_for_seconds=900)), 10.0)
        self.assertEqual(poll_pause(Hivpp(wait_for_seconds=-5)),
                         float(DEFAULT_POLL_SECONDS))
        self.assertEqual(poll_pause(Hivpp(wait_for_seconds="zwei")),
                         float(DEFAULT_POLL_SECONDS))

    def test_the_copy_waits_for_a_vop_id_before_it_decides(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "utils", "fints_vop_client.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("hivpp = self._await_vop_result(", source)
        self.assertIn("while not can_be_approved(hivpp)", source)
        self.assertIn("if can_be_approved(hivpp) and (", source)

    def test_an_unreadable_verdict_no_longer_blocks_the_order(self):
        """The regression this replaces: requiring a readable verdict meant
        nothing could ever be parked at a bank that sends a report."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "utils", "fints_vop_client.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("still_checking", source)


class Response:
    """The shape python-fints answers with: responses(ref, code=None)."""

    def __init__(self, lines, explode=False, by_code=False):
        self._lines = lines
        self._explode = explode
        self._by_code = by_code

    def responses(self, ref, code=None):
        if self._explode:
            raise RuntimeError("a response this cannot read")
        if code is None:
            return self._lines
        return [line for line in self._lines if line.code == code]


class Segment:
    """An HIVPP1 as the poll loop sees it."""

    def __init__(self, vop_id=None, result=None, polling_id=None,
                 report=b"", wait=None):
        self.vop_id = vop_id
        self.polling_id = polling_id
        self.payment_status_report = report
        self.wait_for_seconds = wait
        self.vop_single_result = type("EVPE", (), {"result": result})()


class TestTheLogSaysWhatActuallyHappened(unittest.TestCase):
    """"Asked for 30 seconds and got nothing" was said whether the bank had
    been asked fifteen times or once.

    Those are different faults with different fixes, and the log could not
    tell them apart -- the loop breaks out silently when an answer carries no
    HIVPP segment, and then reported the ceiling as if it had been reached.
    The trail is what makes the next attempt diagnosable.
    """

    def test_a_note_names_the_step_and_what_came_back(self):
        note = _poll_note(3, 6.0, Segment(vop_id=None, result=None,
                                          polling_id=b"pid", wait=2))
        self.assertIn("ask 3", note)
        self.assertIn("after 6s", note)
        self.assertIn("vop_id=no", note)
        self.assertIn("pid", note)

    def test_a_note_says_when_the_id_finally_arrived(self):
        self.assertIn("vop_id=yes", _poll_note(1, 2.0, Segment(vop_id=b"x")))

    def test_a_note_reports_the_report_size(self):
        """The verdict lives in the payment status report at this bank, and
        its size is the only readable thing about it."""
        self.assertIn("report=9 bytes",
                      _poll_note(0, 0.0, Segment(report=b"123456789")))

    def test_a_note_never_raises_on_a_segment_it_cannot_read(self):
        self.assertIsInstance(_poll_note(0, 0.0, object()), str)

    def test_response_codes_are_readable(self):
        """responses() takes the segment the codes answer. Calling it
        without one raises, which would have made this line read
        "unreadable" every single time."""
        said = _codes_in(Response([Line("3010"), Line("9999")]), object())
        self.assertIn("3010", said)
        self.assertIn("9999", said)

    def test_an_unreadable_response_says_so_rather_than_raising(self):
        self.assertIn("unreadable",
                      _codes_in(Response([], explode=True), object()))

    def test_the_note_carries_the_scroll_reference(self):
        self.assertIn("aufsetzpunkt='staticscrollref'",
                      _poll_note(1, 2.0, Segment(), "staticscrollref"))


class TestTheBankSaidThereIsMore(unittest.TestCase):
    """3040 carries an Aufsetzpunkt and HKVPP has a field for it.

    The Volksbank answers the order with

        3040 Es liegen weitere Informationen vor. (['staticscrollref'])
        3945 Freigabe ohne VOP-Bestaetigung nicht moeglich.

    and the follow-up was sending the polling id alone. The bank said "ask
    again with this reference" and was asked again without it.
    """

    def test_the_reference_is_read_off_the_answer(self):
        said = Line("3040")
        said.parameters = ["staticscrollref"]
        self.assertEqual(
            touchdown_in(Response([said], by_code=True), object()),
            "staticscrollref")

    def test_a_3040_without_a_parameter_is_not_a_reference(self):
        said = Line("3040")
        said.parameters = []
        self.assertIsNone(touchdown_in(Response([said], by_code=True),
                                       object()))

    def test_no_3040_at_all(self):
        self.assertIsNone(touchdown_in(Response([], by_code=True), object()))

    def test_it_never_raises(self):
        self.assertIsNone(
            touchdown_in(Response([], explode=True), object()))
        self.assertIsNone(touchdown_in(None, object()))

    def test_the_follow_up_sends_it(self):
        """Asserted as the keyword with its value, which no comment has."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "utils", "fints_vop_client.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        loop = source.split("def _await_vop_result(")[1]
        self.assertIn("aufsetzpunkt=scroll)", loop)
        # And it is refreshed from each answer, or the second ask repeats
        # the first one's reference for as long as the loop runs.
        self.assertIn("scroll = touchdown_in(again, query) or scroll", loop)


class TestTheCeilingIsNotThirty(unittest.TestCase):
    """Thirty was a guess, and at two seconds a poll it gave the bank fifteen
    chances to finish a check that runs against a foreign institution."""

    def test_it_is_a_minute(self):
        self.assertEqual(POLL_LIMIT_SECONDS, 60)
