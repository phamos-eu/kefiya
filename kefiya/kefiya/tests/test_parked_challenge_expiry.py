# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A parked TAN challenge must not outlive its usefulness.

The challenge is kept so a release given in the banking app still finds
something to unlock after the request that asked for it has ended. Nothing
expired it, though -- and an unreleased challenge is not inert: every later
connection replays it, the bank answers without a TAN status, and fints raises.
The raise ends the connection attempt, so one challenge nobody released in July
left an account unable to send anything in August.

These tests pin the shelf life down. The replay itself needs a bank and is
covered by hand; what is testable is when we stop trying.
"""

import unittest
from unittest.mock import patch

from frappe.utils import add_to_date, now_datetime

from kefiya.utils.fints_controller import (
    PARKED_CHALLENGE_MAX_AGE_HOURS,
    FinTSController,
)


class Login:
    """Just enough Kefiya Login for the age check."""

    def __init__(self, tan_state_updated):
        self.tan_state_updated = tan_state_updated
        self.stored_tan_blob = b"challenge"
        self.stored_dialog_blob = b"dialog"
        self.stored_tan_state_decoupled = 1
        self.saved = False

    def save(self):
        self.saved = True


def controller(login):
    """A controller without __init__ -- constructing one talks to a bank."""
    instance = FinTSController.__new__(FinTSController)
    instance.kefiya_login = login
    return instance


class TestParkedChallengeExpiry(unittest.TestCase):
    def test_a_fresh_challenge_is_still_worth_replaying(self):
        login = Login(add_to_date(now_datetime(), hours=-1))
        self.assertFalse(
            controller(login)._FinTSController__parked_challenge_is_stale()
        )

    def test_a_challenge_from_last_month_is_not(self):
        login = Login(add_to_date(now_datetime(), days=-15))
        self.assertTrue(controller(login)._FinTSController__parked_challenge_is_stale())

    def test_the_boundary_belongs_to_the_challenge(self):
        """Exactly at the limit it still gets its chance."""
        login = Login(
            add_to_date(now_datetime(), hours=-PARKED_CHALLENGE_MAX_AGE_HOURS)
        )
        self.assertFalse(
            controller(login)._FinTSController__parked_challenge_is_stale()
        )

    def test_without_a_timestamp_it_counts_as_old(self):
        """State from before the field existed. Nothing says it is fresh."""
        self.assertTrue(
            controller(Login(None))._FinTSController__parked_challenge_is_stale()
        )

    def test_discarding_clears_all_three_fields_and_saves(self):
        login = Login(add_to_date(now_datetime(), days=-15))
        controller(login)._FinTSController__discard_parked_challenge()

        self.assertIsNone(login.stored_tan_blob)
        self.assertIsNone(login.stored_dialog_blob)
        self.assertIsNone(login.stored_tan_state_decoupled)
        self.assertTrue(
            login.saved, "a discarded challenge that is not written stays parked"
        )

    def test_the_client_state_is_left_alone(self):
        """Discarding must not push a broken session onto the sibling logins."""
        login = Login(add_to_date(now_datetime(), days=-15))
        instance = controller(login)

        with patch.object(
            FinTSController, "_persist_fints_state"
        ) as persist:
            instance._FinTSController__discard_parked_challenge()

        persist.assert_not_called()


class TestTheDecoupledWaitCanActuallyPoll(unittest.TestCase):
    """The one that cost three accounts three months of statements.

    A decoupled release is polled for on the live dialog. Parking the
    challenge pauses that dialog, and python-fints refuses to send on a paused
    one -- "Cannot send() on a paused dialog". The fetch parked FIRST and
    waited second, so the very first poll raised, the wait answered None, and
    the user was told the release had not arrived in time after no time had
    passed at all. Every attempt asked for another release.
    """

    def _source(self):
        import inspect

        import kefiya.utils.fints_controller as fc
        return inspect.getsource(fc.FinTSController._get_transactions_checked)

    def test_the_prompt_goes_up_before_the_wait(self):
        source = self._source()
        # The call, not the word: "See _await_release." stands in the comment
        # above it, and matching that made this pass with the code the wrong
        # way round.
        self.assertLess(
            source.index("self._publish_tan_prompt(response, decoupled=True)"),
            source.index("self._await_release(response)"))

    def test_nothing_is_parked_before_the_wait(self):
        """Parking is what pauses the dialog. Doing it first is the bug."""
        source = self._source()
        self.assertLess(source.index("self._await_release(response)"),
                        source.index("self._park_tan_challenge(response)"))
        self.assertNotIn("self.ask_for_tan(response, decoupled=True)", source)

    def test_it_is_still_parked_when_the_wait_runs_out(self):
        """Otherwise a release given a minute later unlocks nothing."""
        source = self._source()
        self.assertIn("self._park_tan_challenge(response)", source)
        self.assertLess(source.index("self._park_tan_challenge(response)"),
                        source.index("TanInteractionRequired"))

    def test_asking_still_parks_for_every_other_caller(self):
        import inspect

        from kefiya.utils.fints_controller import FinTSController
        source = inspect.getsource(FinTSController.ask_for_tan)
        self.assertIn("self._park_tan_challenge(response)", source)
        self.assertIn("self._publish_tan_prompt(", source)


class TestHowLongToWaitForARelease(unittest.TestCase):
    """The bank says both numbers itself. Waiting a flat two minutes stopped
    at 40 % of what this bank allows -- 150 polls two seconds apart."""

    def test_the_banks_numbers_win(self):
        from kefiya.utils.decoupled_budget import decoupled_wait

        class Parameters:
            wait_before_next_poll = 2
            decoupled_max_poll_number = 150

        pause, total = decoupled_wait(Parameters(), 2, 120)
        self.assertEqual(pause, 2.0)
        self.assertEqual(total, 300)

    def test_without_them_the_old_behaviour_stands(self):
        from kefiya.utils.decoupled_budget import decoupled_wait

        self.assertEqual(decoupled_wait(None, 2, 120), (2, 120))
        self.assertEqual(decoupled_wait(object(), 2, 120), (2, 120))

    def test_a_bank_that_asks_for_less_does_not_shorten_it(self):
        """Its count is a maximum, not a promise. Somebody may still be
        reaching for their phone."""
        from kefiya.utils.decoupled_budget import decoupled_wait

        class Parameters:
            wait_before_next_poll = 2
            decoupled_max_poll_number = 5

        self.assertEqual(decoupled_wait(Parameters(), 2, 120)[1], 120)

    def test_nobody_waits_longer_than_the_ceiling(self):
        from kefiya.utils.decoupled_budget import (
            DECOUPLED_WAIT_CEILING_SECONDS, decoupled_wait)

        class Parameters:
            wait_before_next_poll = 10
            decoupled_max_poll_number = 9999

        self.assertEqual(decoupled_wait(Parameters(), 2, 120)[1],
                         DECOUPLED_WAIT_CEILING_SECONDS)

    def test_nonsense_parameters_do_not_make_a_busy_loop(self):
        from kefiya.utils.decoupled_budget import decoupled_wait

        class Parameters:
            wait_before_next_poll = 0
            decoupled_max_poll_number = "viele"

        pause, total = decoupled_wait(Parameters(), 2, 120)
        self.assertEqual(pause, 2)
        self.assertEqual(total, 120)
