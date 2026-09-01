# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A standing dialog is not the same thing as a usable one.

    Could not load sepa accounts with error:
    Cannot send on dialog that is not open

Reported for one login after another of the same bank, in scheduled runs, and
read by everybody as a bank problem. It was ours.

python-fints' resume_dialog() has no try/finally:

    self._standing_dialog = FinTSDialog.create_resume(self, dialog_data)
    with self._standing_dialog:          # __exit__ ends the dialog
        yield self
    self._standing_dialog = None         # skipped when the block raises

The block raises on the ordinary path: a parked TAN that comes back
unanswered raises TanInteractionRequired. So the client keeps a reference to a
dialog that has been ENDED. Nothing clears it, and inside a fetch session the
client is shared by every login of the bank access -- so each of them joins the
wreck and reports a bank error where there was none.
"""

import os
import unittest


def _source():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "utils", "fints_controller.py")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class Dialog(object):
    def __init__(self, open=False, paused=False):
        self.open = open
        self.paused = paused


class Connection(object):
    def __init__(self, dialog=None):
        self._standing_dialog = dialog


class TestUsableIsMoreThanPresent(unittest.TestCase):

    def setUp(self):
        from kefiya.utils import fints_dialog_state
        self.mod = fints_dialog_state

    def test_an_open_dialog_is_usable(self):
        self.assertTrue(self.mod.dialog_is_usable(
            Connection(Dialog(open=True))))

    def test_an_ended_dialog_is_not(self):
        """The case the error message named."""
        self.assertFalse(self.mod.dialog_is_usable(
            Connection(Dialog(open=False))))

    def test_a_paused_dialog_is_not(self):
        """Somebody is answering a TAN in their banking app."""
        self.assertFalse(self.mod.dialog_is_usable(
            Connection(Dialog(open=True, paused=True))))

    def test_no_dialog_at_all_is_not(self):
        self.assertFalse(self.mod.dialog_is_usable(Connection(None)))


class TestOnlyTheDeadOneIsDropped(unittest.TestCase):

    def setUp(self):
        from kefiya.utils import fints_dialog_state
        self.mod = fints_dialog_state

    def test_an_ended_dialog_is_forgotten(self):
        conn = Connection(Dialog(open=False))
        self.assertTrue(self.mod.discard_unusable_dialog(conn))
        self.assertIsNone(conn._standing_dialog)

    def test_a_paused_dialog_is_left_exactly_where_it_is(self):
        """It is somebody's half-finished authentication. Throwing it away
        means the release, when it comes, unlocks nothing."""
        dialog = Dialog(open=True, paused=True)
        conn = Connection(dialog)
        self.assertFalse(self.mod.discard_unusable_dialog(conn))
        self.assertIs(conn._standing_dialog, dialog)

    def test_an_open_dialog_is_left_alone(self):
        dialog = Dialog(open=True)
        conn = Connection(dialog)
        self.assertFalse(self.mod.discard_unusable_dialog(conn))
        self.assertIs(conn._standing_dialog, dialog)

    def test_nothing_to_drop_is_not_an_error(self):
        self.assertFalse(self.mod.discard_unusable_dialog(Connection(None)))


class TestTheWreckIsNeitherMadeNorJoined(unittest.TestCase):
    """Two repairs, because either alone leaves the other half of the problem:
    stop creating the dead reference, and stop trusting one that exists."""

    @staticmethod
    def _resume_block():
        """The block that answers a parked challenge, whole.

        Read to its end rather than by character count: it carries two repairs
        now -- this one and the stale-challenge expiry from develop -- and a
        fixed window silently stopped covering the second half the moment they
        met.
        """
        source = _source()
        return source.split("stored_tan_blob \\")[1].split(
            "\n        # After successful login")[0]

    def test_the_resume_block_cleans_up_after_itself(self):
        body = self._resume_block()
        self.assertIn("self._resume_and_answer_the_parked_tan(tan)", body)
        self.assertIn("except Exception:", body)
        self.assertIn("discard_unusable_dialog(self.fints_connection)", body)

    def test_the_ask_the_user_path_drops_the_dialog_too(self):
        """TanInteractionRequired is the legitimate path, and it still leaves
        an ended dialog behind: the exception leaves resume_dialog()'s block,
        __exit__ ends the dialog, and the client keeps the reference."""
        body = self._resume_block()
        asked = body.split("except TanInteractionRequired:")[1].split(
            "except Exception:")[0]
        self.assertIn("discard_unusable_dialog(self.fints_connection)", asked)
        self.assertIn("raise", asked)

    def test_a_challenge_the_bank_refuses_is_thrown_away_as_well(self):
        """The other repair, and it is not the same one: a dead dialog is a
        reference to drop, a challenge the bank no longer knows is stored
        state that would brick the access on every later attempt."""
        body = self._resume_block()
        failed = body.split("except Exception:")[1]
        self.assertIn("discard_unusable_dialog(self.fints_connection)", failed)
        self.assertIn("self.__discard_parked_challenge()", failed)

    def test_joining_checks_that_it_can_be_sent_on(self):
        body = _source().split("def client_session(self):")[1][:2600]
        self.assertIn("not dialog_is_usable(conn)", body)

    def test_a_dead_shared_client_leaves_the_session(self):
        """Inside a fetch session the client is shared by every login of the
        access -- one wreck would otherwise take the whole access down."""
        body = _source().split("def client_session(self):")[1][:2600]
        self.assertIn('session.get("connections", {}).pop(', body)

    def test_the_reach_into_the_library_is_admitted_not_hidden(self):
        """_standing_dialog is private. Touching it needs a reason on the
        record, not a silent line."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "utils", "fints_dialog_state.py")
        with open(path, encoding="utf-8") as handle:
            body = handle.read().split("def discard_unusable_dialog(")[1]
        self.assertIn("not something to be proud of", body[:1400])
