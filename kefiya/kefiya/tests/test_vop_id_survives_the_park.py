# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The VoP-ID has to reach the TAN, or the bank refuses to release the order.

This is the reason no Volksbank transfer ever went out. The bank checks the
payee, hands back a VoP-ID, and then will not sign anything until the check is
confirmed with it::

    3945  Freigabe ohne VOP-Bestaetigung nicht moeglich.

The confirmation is one segment, HKVPA(vop_id=...), sent alongside the TAN.
Two separate things dropped it on the way:

  1. python-fints attaches it only when it can READ the verdict::

         if challenge.vop_result and \\
                 challenge.vop_result.vop_single_result.result == 'RCVC':

     At a bank that answers with a complete payment status report --
     report_complete='V', which is what this one does -- vop_single_result is
     an empty EVPE() and the verdict is None. Never 'RCVC'. So the TAN went
     out bare.

  2. NeedTANResponse.get_data() does not serialise vop_result at all, and
     _from_data_v1 does not restore it. Every challenge kefiya parks -- which
     is every challenge a user answers -- came back out without one, so even
     a readable verdict was gone by the time the TAN was typed.

Both are fixed by asking the same question can_be_approved() asks: is there a
VoP-ID. This is that question asked of the two places the ID has to survive.

No bank and no library: what is asserted is the rule and the source text.
"""

import os
import unittest

from kefiya.utils.fints_vop import (CarriedVopId, can_be_approved,
                                    carry_vop_id, vop_id_of)


class Challenge:
    """The shape a NeedTANResponse presents to the code under test."""

    def __init__(self, vop_result=None):
        self.vop_result = vop_result


class Segment:
    def __init__(self, vop_id=None, result=None):
        self.vop_id = vop_id
        self.vop_single_result = type("EVPE", (), {"result": result})()


class TestTheIdIsWhatDecides(unittest.TestCase):

    def test_an_id_is_enough_without_a_verdict(self):
        """The measured case: a VoP-ID and no readable verdict at all."""
        self.assertTrue(can_be_approved(Segment(vop_id=b"abc", result=None)))

    def test_a_verdict_without_an_id_is_not_enough(self):
        """Nothing to put in HKVPA(vop_id=...), so nothing may be released."""
        self.assertFalse(can_be_approved(Segment(vop_id=None, result="RCVC")))

    def test_nothing_at_all(self):
        self.assertFalse(can_be_approved(None))


class TestCarryingItAcrossThePark(unittest.TestCase):

    def test_reads_the_id_off_a_live_challenge(self):
        self.assertEqual(
            vop_id_of(Challenge(Segment(vop_id=b"xyz"))), b"xyz")

    def test_a_challenge_without_a_payee_check_carries_nothing(self):
        self.assertIsNone(vop_id_of(Challenge()))
        self.assertIsNone(vop_id_of(None))

    def test_a_restored_challenge_gets_the_id_back(self):
        """What the library dropped: a parked challenge with no vop_result."""
        challenge = Challenge(vop_result=None)
        self.assertTrue(carry_vop_id(challenge, b"stored"))
        self.assertTrue(can_be_approved(challenge.vop_result))
        self.assertEqual(challenge.vop_result.vop_id, b"stored")

    def test_a_live_check_is_not_overwritten_by_a_stored_one(self):
        """The dialog knows better than the database does."""
        live = Segment(vop_id=b"live")
        challenge = Challenge(vop_result=live)
        self.assertTrue(carry_vop_id(challenge, b"stale"))
        self.assertIs(challenge.vop_result, live)

    def test_no_id_stored_changes_nothing(self):
        """A login that never met a payee check behaves as it always did."""
        challenge = Challenge()
        self.assertFalse(carry_vop_id(challenge, None))
        self.assertIsNone(challenge.vop_result)

    def test_it_never_raises(self):
        self.assertFalse(carry_vop_id(None, b"anything"))

    def test_the_carrier_answers_the_only_question_asked_of_it(self):
        self.assertTrue(can_be_approved(CarriedVopId(b"id")))
        self.assertFalse(can_be_approved(CarriedVopId(None)))


def _source(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return handle.read()


def _send_tan_code():
    """The body of the send_tan override, docstring stripped.

    The docstring quotes the library line this method exists to replace, so
    a search of the whole method finds the very text it was written to get
    rid of.
    """
    method = _source("utils", "fints_vop_client.py").split(
        "def send_tan(self, challenge, tan):")[1]
    return method.split('"""', 2)[2]


class TestBothEndsAreWired(unittest.TestCase):
    """Asserted as call syntax with arguments, not as prose.

    A test that greps for a name matches the comment that explains it, and
    this suite has been fooled that way before -- four times. Every assertion
    below names the argument it is passed, which a sentence about the code
    does not contain.
    """

    def test_parking_stores_the_id(self):
        body = _source("utils", "fints_controller.py")
        self.assertIn(
            "self.kefiya_login.stored_vop_id_blob = fints_vop.vop_id_of("
            "tan_state)", body)

    def test_every_place_that_forgets_a_challenge_forgets_the_id(self):
        """Or a stale approval rides onto an unrelated later challenge.

        Three places drop a parked challenge: _persist_fints_state with no
        challenge to park, __discard_parked_challenge, and
        _forget_client_state. Counted rather than sampled, because the one
        that gets missed is the one nobody wrote an assertion for.
        """
        body = _source("utils", "fints_controller.py")
        forgets = body.count(
            "stored_tan_state_decoupled = None")
        clears = body.count("stored_vop_id_blob = None")
        self.assertEqual(
            forgets, clears,
            "{0} places clear the parked TAN state, {1} clear the VoP-ID"
            " with it".format(forgets, clears))
        self.assertGreaterEqual(forgets, 3)

    def test_resuming_puts_the_id_back_before_the_tan_is_sent(self):
        # Die TAN-Strecke wohnt seit ihrer Herausloesung in fints_tan_session.
        body = _source("utils", "fints_tan_session.py")
        resume = body.split("def _resume_and_answer_the_parked_tan")[1][:1600]
        self.assertIn(
            "fints_vop.carry_vop_id(\n                tan_request,"
            " self.kefiya_login.stored_vop_id_blob)", resume)
        # And before, not after -- afterwards it would be decoration.
        self.assertLess(resume.index("carry_vop_id"),
                        resume.index("send_tan(tan_request, tan)"))

    def test_send_tan_attaches_the_approval_by_id(self):
        """Not by verdict, which is the library's condition and is unreadable
        at a bank that answers with a payment status report."""
        override = _send_tan_code()
        self.assertIn(
            'if can_be_approved(getattr(challenge, "vop_result", None)):',
            override)
        self.assertIn(
            "vop_seg = [HKVPA1(vop_id=challenge.vop_result.vop_id)]",
            override)
        # Past the docstring, which quotes the library's condition in order
        # to explain why it is wrong. Matching prose instead of code is how
        # this suite has passed with the code broken before.
        self.assertNotIn("vop_single_result.result == 'RCVC'", override)

    def test_the_decoupled_reissue_keeps_the_payee_check(self):
        """The library builds the next poll's challenge with five arguments
        where the constructor takes six, so the approval is sent with the
        first poll and with none of the ones after it."""
        reissue = _send_tan_code().split("if resp.code == '3956':")[1][:500]
        self.assertIn("challenge.vop_result,", reissue)

    def test_the_login_can_hold_the_id(self):
        body = _source("kefiya", "doctype", "kefiya_login", "kefiya_login.py")
        self.assertIn("def stored_vop_id_blob(self):", body)
        self.assertIn(
            "self.stored_vop_id_state = self.conv_blob_to_encrypted_string("
            "value)", body)

    def test_clearing_the_caches_clears_the_id(self):
        body = _source("kefiya", "doctype", "kefiya_login", "kefiya_login.py")
        clearing = body.split("def clear_fints_caches(self):")[1][:600]
        self.assertIn("self.stored_vop_id_blob = None", clearing)


if __name__ == "__main__":
    unittest.main()
