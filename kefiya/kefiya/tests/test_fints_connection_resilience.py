# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt
"""What has to survive when a bank stops answering.

The Consorsbank access showed the shape of it: connect, ask for the TAN
settings, "release required" -- and then nothing. No error, no log entry, no
stored state. The user released the request in their banking app, the next
attempt found nothing parked, opened a fresh dialog and asked for a fresh
release. Three steps, over and over.

Three separate things have to hold for that loop not to exist:

* every FinTS message carries a timeout, so a silent gateway ends the request
  instead of parking a worker on it,
* a parked challenge is committed the moment it is parked, so it does not
  depend on the rest of the request finishing,
* a paused dialog is never ended on the way out, so the release the user is
  about to give still has something to unlock.
"""

import inspect
import unittest

from kefiya.utils import fints_controller
from kefiya.utils.fints_controller import (
    FINTS_TIMEOUT,
    FinTSController,
    _apply_connection_timeout,
    _end_dialog_unless_paused,
)


class _FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return "response"

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


class _FakeConnection:
    def __init__(self, session):
        self.session = session


class _FakeClient:
    def __init__(self, session=None):
        if session is not None:
            self.connection = _FakeConnection(session)


class TestConnectionTimeout(unittest.TestCase):
    """python-fints posts with no timeout at all."""

    def test_a_post_without_a_timeout_gets_one(self):
        session = _FakeSession()
        _apply_connection_timeout(_FakeClient(session))

        session.post("https://example.invalid/fints", data=b"x")

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][2].get("timeout"), FINTS_TIMEOUT)

    def test_a_caller_that_names_its_own_timeout_keeps_it(self):
        session = _FakeSession()
        _apply_connection_timeout(_FakeClient(session))

        session.post("https://example.invalid/fints", timeout=5)

        self.assertEqual(session.calls[0][2].get("timeout"), 5)

    def test_applying_it_twice_does_not_stack_wrappers(self):
        """A client is reused across the logins of one bank access."""
        session = _FakeSession()
        _apply_connection_timeout(_FakeClient(session))
        wrapped_once = session.request
        _apply_connection_timeout(_FakeClient(session))

        self.assertIs(session.request, wrapped_once)

    def test_a_client_without_a_session_is_left_alone(self):
        """A library version that arranges its connection differently must
        keep its own behaviour rather than fail the fetch on the way in."""
        _apply_connection_timeout(_FakeClient())  # must not raise

    def test_the_read_timeout_is_generous_enough_for_a_slow_bank(self):
        connect, read = FINTS_TIMEOUT
        self.assertGreaterEqual(connect, 5)
        self.assertGreaterEqual(
            read, 60,
            "The read value is the gap between bytes; a statement fetch may "
            "well think for a minute before it starts answering.",
        )

    def test_the_timeout_is_applied_where_the_client_is_built(self):
        source = inspect.getsource(
            FinTSController._FinTSController__init_fints_connection)
        self.assertIn("_apply_connection_timeout", source)
        self.assertLess(
            source.index("FinTS3PinTanClient("),
            source.index("_apply_connection_timeout"),
            "The timeout goes on after the client exists and before the "
            "first message leaves.",
        )


class _FakeDialog:
    def __init__(self, paused):
        self.paused = paused


class _FakeConn:
    def __init__(self, dialog=None, raises=False):
        self._standing_dialog = dialog
        self.raises = raises
        self.ended = False

    def __exit__(self, *args):
        self.ended = True
        if self.raises:
            raise RuntimeError("bank dropped the connection")


class TestPausedDialogsSurvive(unittest.TestCase):
    def test_a_paused_dialog_is_not_ended(self):
        conn = _FakeConn(_FakeDialog(paused=True))
        _end_dialog_unless_paused(conn)
        self.assertFalse(
            conn.ended,
            "Ending it sends HKEND and throws away the challenge the user is "
            "on their way to release.",
        )

    def test_a_running_dialog_is_ended(self):
        conn = _FakeConn(_FakeDialog(paused=False))
        _end_dialog_unless_paused(conn)
        self.assertTrue(conn.ended)

    def test_a_connection_without_a_dialog_is_ended(self):
        conn = _FakeConn(None)
        _end_dialog_unless_paused(conn)
        self.assertTrue(conn.ended)

    def test_a_bank_that_dropped_the_line_does_not_raise_on_the_way_out(self):
        conn = _FakeConn(_FakeDialog(paused=False), raises=True)
        _end_dialog_unless_paused(conn)  # must not raise
        self.assertTrue(conn.ended)

    def test_both_teardown_paths_go_through_the_same_check(self):
        """_retire_connection always spared paused dialogs; the session
        teardown did not, so a challenge parked inside a fetch session was
        ended again on the way out."""
        for func in (fints_controller._retire_connection,
                     fints_controller.fints_session):
            source = inspect.getsource(func)
            self.assertIn(
                "_end_dialog_unless_paused", source,
                "{0} must not close dialogs on its own.".format(
                    func.__name__),
            )
            self.assertNotIn(
                "conn.__exit__(None, None, None)", source,
                "{0} still ends dialogs without asking whether one is "
                "parked.".format(func.__name__),
            )


class TestParkedChallengeIsCommitted(unittest.TestCase):
    def _source(self):
        return inspect.getsource(FinTSController.ask_for_tan)

    def test_the_challenge_is_committed(self):
        source = self._source()
        self.assertIn(
            "frappe.db.commit()", source,
            "A parked challenge that is only written to the transaction is "
            "lost when the request does not finish -- which is exactly what "
            "happens when the bank stops answering.",
        )

    def test_it_is_written_before_it_is_committed(self):
        source = self._source()
        self.assertLess(
            source.index("_persist_fints_state(response)"),
            source.index("frappe.db.commit()"),
        )

    def test_the_user_is_prompted_only_after_the_commit(self):
        """The prompt travels over the socket and arrives at once. Prompting
        first means the user can release a challenge that is still only in an
        uncommitted transaction."""
        source = self._source()
        self.assertLess(
            source.index("frappe.db.commit()"),
            source.index("request_mfa_confirmation"),
        )

    def test_a_failing_commit_does_not_swallow_the_prompt(self):
        source = self._source()
        self.assertIn("except Exception:", source)
        self.assertIn(
            "frappe.log_error", source,
            "If the commit fails the user still has to be told what the bank "
            "is waiting for.",
        )


if __name__ == "__main__":
    unittest.main()
