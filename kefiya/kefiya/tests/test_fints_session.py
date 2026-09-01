# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import inspect
import unittest

import frappe

from kefiya.utils import client, fints_controller
from kefiya.utils.fints_controller import FinTSController, fints_session


class TestSessionScope(unittest.TestCase):
    """A shared dialog must never outlive the work it belongs to.

    It lives on frappe.local, which is rebuilt for every request and every
    background job, so a leaked session cannot reach the next one. Inside a
    request it still has to clean up after itself.
    """

    def test_session_is_cleared_afterwards(self):
        self.assertIsNone(fints_controller._active_session())
        with fints_session():
            self.assertIsNotNone(fints_controller._active_session())
        self.assertIsNone(fints_controller._active_session())

    def test_session_is_cleared_after_an_error(self):
        try:
            with fints_session():
                raise ValueError("fetch blew up")
        except ValueError:
            pass
        self.assertIsNone(fints_controller._active_session())

    def test_nesting_joins_instead_of_replacing(self):
        with fints_session():
            outer = fints_controller._active_session()
            with fints_session():
                self.assertIs(fints_controller._active_session(), outer)
            # The inner block must not have torn the session down.
            self.assertIs(fints_controller._active_session(), outer)

    def test_the_session_lives_on_frappe_local(self):
        source = inspect.getsource(fints_controller.fints_session)
        self.assertIn("frappe.local", source)


class TestAccessKey(unittest.TestCase):
    """One dialog may serve one bank access -- never two."""

    def _login(self, blz, fints_login):
        return frappe._dict({"blz": blz, "fints_login": fints_login})

    def test_same_access_shares_a_key(self):
        self.assertEqual(
            fints_controller._access_key(self._login("67092300", "u1")),
            fints_controller._access_key(self._login("67092300", "u1")),
        )

    def test_two_contracts_at_one_bank_do_not(self):
        """The BLZ alone would merge them and send one contract's requests
        over the other's credentials."""
        self.assertNotEqual(
            fints_controller._access_key(self._login("67092300", "u1")),
            fints_controller._access_key(self._login("67092300", "u2")),
        )

    def test_incomplete_credentials_are_never_grouped(self):
        self.assertIsNone(fints_controller._access_key(
            self._login(None, "u1")))
        self.assertIsNone(fints_controller._access_key(
            self._login("67092300", None)))


class TestDialogReuse(unittest.TestCase):
    """Every command used to open its own dialog: handshake, authentication,
    one command, HKEND. The handshake costs several round trips, the data
    costs one -- which is why a fetch of 48 accounts took twelve minutes."""

    def test_reads_go_through_the_shared_session(self):
        source = inspect.getsource(fints_controller)
        body = source[source.index("class FinTSController"):]
        # The money paths deliberately keep their own dialog.
        self.assertEqual(
            body.count("with self.fints_connection:"), 2,
            "Only submit_sepa_transfer and submit_sepa_debit may open their "
            "own dialog; every read must use client_session().",
        )
        self.assertGreaterEqual(body.count("with self.client_session():"), 10)

    def test_money_paths_are_excluded(self):
        for method in (FinTSController.submit_sepa_transfer,
                       FinTSController.submit_sepa_debit):
            self.assertIn(
                "with self.fints_connection:", inspect.getsource(method),
                "A transfer parks the dialog on a TAN request; freezing a "
                "dialog a surrounding fetch is still using would break it.",
            )

    def test_an_open_dialog_is_joined_not_reopened(self):
        source = inspect.getsource(FinTSController.client_session)
        self.assertIn("if conn._standing_dialog:", source)
        self.assertLess(
            source.index("if conn._standing_dialog:"),
            source.index("conn.__enter__()"),
            "Entering a client that already has a standing dialog raises "
            "'Cannot double __enter__'.",
        )

    def test_the_session_closes_what_it_opened(self):
        # Closing moved into _end_dialog_unless_paused, which both teardown
        # paths now share -- the session used to end dialogs itself and so
        # ended parked ones too. What this test is about is unchanged: the
        # session closes what it opened, on the error path as well, and a bank
        # that dropped the line does not become a second exception.
        source = inspect.getsource(fints_controller.fints_session)
        self.assertIn("_end_dialog_unless_paused(conn", source)
        self.assertIn(
            "finally:", source,
            "The dialog has to be closed on the error path as well.",
        )
        self.assertIn(
            "except Exception:",
            inspect.getsource(fints_controller._end_dialog_unless_paused),
            "A bank that dropped the connection must not turn into a second "
            "exception on the way out.",
        )


class TestConnectionSharing(unittest.TestCase):
    """Inside a session the logins of one access share a client, so a second
    account of the same bank costs one command instead of a handshake."""

    def _source(self):
        return inspect.getsource(
            FinTSController._FinTSController__init_fints_connection)

    def test_sharing_only_happens_inside_a_session(self):
        source = self._source()
        self.assertIn("_active_session()", source)
        self.assertIn(
            "if access and access in session", source,
            "Outside a session nothing may be shared -- behaviour has to stay "
            "exactly as it was.",
        )

    def test_a_shared_client_is_registered_for_the_access(self):
        source = self._source()
        self.assertIn('session["connections"][access] = self.fints_connection',
                      source)

    def test_a_pending_tan_is_not_resumed_inside_an_open_dialog(self):
        """resume_dialog() refuses to run inside a standing dialog."""
        source = inspect.getsource(FinTSController.__init__)
        self.assertIn("not self.fints_connection._standing_dialog", source)


class TestRetireOnFailure(unittest.TestCase):
    """With one dialog per command, a broken dialog died with the command.
    Shared, every later command in the session would inherit it -- one failure
    would take down the rest of the bank access."""

    def test_a_failed_command_retires_the_connection(self):
        source = inspect.getsource(FinTSController.client_session)
        self.assertIn("_retire_connection(session, conn)", source)
        self.assertIn(
            "raise", source,
            "Retiring must not swallow the failure.",
        )

    def test_an_unsupported_segment_keeps_the_dialog(self):
        """Decided from the stored BPD without touching the dialog -- and it
        is the common case: twelve of them in one collective run."""
        source = inspect.getsource(FinTSController.client_session)
        self.assertIn(
            "if not _is_unsupported_operation(exc):", source,
            "A segment the bank does not offer must not retire the shared "
            "dialog -- twelve of them in one collective run would tear it "
            "down twelve times.",
        )
        self.assertIn(
            "FinTSUnsupportedOperation",
            inspect.getsource(fints_controller._is_unsupported_operation),
        )

    def test_a_parked_dialog_is_removed_but_not_ended(self):
        # The paused check now lives in _end_dialog_unless_paused so that the
        # session teardown obeys it too; _retire_connection still has to go
        # through it.
        self.assertIn(
            "_end_dialog_unless_paused(conn",
            inspect.getsource(fints_controller._retire_connection),
        )
        source = inspect.getsource(fints_controller._end_dialog_unless_paused)
        self.assertIn('getattr(dialog, "paused", False)', source)
        self.assertIn(
            "return", source,
            "A TAN request parks the dialog for the user to release later; "
            "ending it would throw that away.",
        )

    def test_a_retired_connection_leaves_the_pool(self):
        source = inspect.getsource(fints_controller._retire_connection)
        self.assertIn('session["connections"].pop(access, None)', source)
        self.assertIn('session["open"].remove(conn)', source)


class TestJoinedDialogIsRetiredToo(unittest.TestCase):
    """The join branch is the normal case, not the exception.

    Inside a session every command after the first joins, and so does every
    login of the access after the first. It had no error handling at all: a TAN
    request parked the dialog and left it registered, and the next command sent
    on a frozen dialog -- "Cannot send() on a paused dialog", for every
    remaining account of that bank.
    """

    def _source(self):
        return inspect.getsource(FinTSController.client_session)

    def test_the_join_branch_retires_on_failure(self):
        source = self._source()
        join_at = source.index("if conn._standing_dialog:")
        opener_at = source.index("conn.__enter__()")
        retires_before_opener = source.count(
            "_retire_connection(session, conn)",
        )
        self.assertGreaterEqual(
            retires_before_opener, 2,
            "Both the join branch and the opener branch must retire; only the "
            "opener did.",
        )
        self.assertLess(
            join_at,
            source.index("_retire_connection(session, conn)"),
            "The join branch comes first and must carry its own handler.",
        )
        self.assertLess(join_at, opener_at)

    def test_the_join_branch_spares_an_unsupported_segment(self):
        source = self._source()
        self.assertEqual(
            source.count("_is_unsupported_operation(exc)"), 2,
            "Both branches must apply the same exemption, or a bank that does "
            "not offer a segment costs a fresh handshake.",
        )

    def test_a_dialog_without_a_session_is_never_retired(self):
        """It belongs to a surrounding `with conn:` that cleans up itself;
        retiring it here would close a dialog still in use."""
        source = self._source()
        self.assertIn("if session is None:", source)
        self.assertLess(
            source.index("session = _active_session()"),
            source.index("if conn._standing_dialog:"),
            "The session has to be known before the join branch decides.",
        )


class TestParkedChallengeSurvivesSiblings(unittest.TestCase):
    """"TAN once per bank" shares a fresh client state with every sibling of
    the access. A sibling holding a parked challenge must be left alone: the
    paused dialog belongs to the client state it was frozen with, so replacing
    that state leaves the user releasing the payment in their banking app while
    the resumed dialog no longer fits its client."""

    def _source(self):
        return inspect.getsource(
            FinTSController._propagate_client_state_to_siblings)

    def test_a_parked_sibling_is_skipped(self):
        source = self._source()
        self.assertIn("s.stored_tan_state or s.stored_dialog_state", source)

    def test_the_skip_comes_before_the_freshness_check(self):
        source = self._source()
        self.assertLess(
            source.index("s.stored_tan_state or s.stored_dialog_state"),
            source.index("s.client_state_updated >= mine"),
            "A parked challenge outranks freshness: the newer state is exactly "
            "what would break it.",
        )

    def test_both_halves_of_the_parked_state_are_read(self):
        source = self._source()
        self.assertIn('"stored_tan_state", "stored_dialog_state"', source,
                      "Both fields have to be selected to be testable.")


class TestFailedHandshake(unittest.TestCase):
    """python-fints assigns _standing_dialog before entering it, so a dialog
    whose init fails leaves the client looking like it has one. Shared, the
    next login of that access would join a dialog that never opened."""

    def test_a_failed_enter_is_retired(self):
        source = inspect.getsource(FinTSController.client_session)
        self.assertIn("conn.__enter__()", source)
        self.assertLess(
            source.index("_retire_connection(session, conn)"),
            source.index('session["open"].append(conn)'),
            "The failed-handshake retire must sit between entering the client "
            "and registering it, otherwise the dead client stays in the pool.",
        )

    def test_the_client_is_registered_only_after_a_successful_enter(self):
        source = inspect.getsource(FinTSController.client_session)
        self.assertLess(
            source.index("conn.__enter__()"),
            source.index('session["open"].append(conn)'),
        )


class TestMoneyPathsRefuseAsharedDialog(unittest.TestCase):
    """A transfer parks the dialog on the TAN request, and parking is its
    SUCCESS case -- it returns rather than raising, so the retire-on-failure
    path never runs and the session would keep handing out a frozen dialog."""

    def test_the_guard_exists_and_checks_the_session(self):
        source = inspect.getsource(
            FinTSController._refuse_inside_fetch_session)
        self.assertIn("_active_session() is not None", source)
        self.assertIn("frappe.throw", source)

    def test_every_money_path_is_guarded(self):
        for method in (FinTSController.submit_sepa_transfer,
                       FinTSController.submit_sepa_debit,
                       FinTSController.approve_pending_vop):
            source = inspect.getsource(method)
            self.assertIn(
                "self._refuse_inside_fetch_session()", source,
                "{0} moves money or resumes a stored dialog; neither may run "
                "inside a shared fetch dialog.".format(method.__name__),
            )

    def test_the_guard_runs_before_the_bank_is_contacted(self):
        source = inspect.getsource(FinTSController.submit_sepa_transfer)
        self.assertLess(
            source.index("self._refuse_inside_fetch_session()"),
            source.index("with self.fints_connection:"),
            "Refusing after opening the dialog would defeat the purpose.",
        )


class TestFetchGroup(unittest.TestCase):
    """One access, one dialog, every account of it."""

    def _source(self):
        return inspect.getsource(client.fetch_group)

    def test_the_group_runs_inside_one_session(self):
        """_fetch_session() is the session in legacy-safe form: it opens a
        shared dialog where the whole fetch is session-aware and a no-op where
        it is not. Asserting the raw context manager missed that rename."""
        self.assertIn("with _fetch_session():", self._source())
        self.assertIn("fints_session", inspect.getsource(client._fetch_session))

    def test_one_failure_does_not_abandon_the_rest(self):
        source = self._source()
        self.assertIn("except Exception as exc:", source)
        self.assertIn(
            "failed[name]", source,
            "Giving up on the group would abandon thirty accounts over one.",
        )

    def test_duplicates_cannot_import_twice(self):
        source = self._source()
        self.assertIn(
            "dict.fromkeys", source,
            "The same login twice would fetch and import it twice.",
        )

    def test_a_single_fetch_also_shares_its_dialog(self):
        """Five retrievals per login used to mean five dialogs."""
        self.assertIn("with _fetch_session():",
                      inspect.getsource(client.fetch_all))

    def test_the_group_runs_through_the_same_helper(self):
        self.assertIn("with _fetch_session():",
                      inspect.getsource(client.fetch_group))

    def test_one_failed_login_does_not_commit_its_leftovers(self):
        """The next successful login would otherwise commit the failed one's
        empty draft import and half-written login state."""
        source = inspect.getsource(client.fetch_group)
        self.assertIn("frappe.db.rollback()", source)
        self.assertLess(
            source.index("frappe.db.rollback()"),
            source.index("frappe.log_error"),
            "Roll back first, then log -- the rollback would drop the Error "
            "Log entry.",
        )
        self.assertIn(
            "frappe.db.commit()", source,
            "The rollback drops the Error Log too, so it needs its own commit.",
        )


class TestLegacyModeOpensNoSecondDialog(unittest.TestCase):
    """The legacy controller knows nothing about sessions.

    It builds its own client and opens its own dialog in the constructor. With
    the session held open by the extras, the legacy import would open a SECOND
    dialog on the same bank access -- the exact double dialog get_fetch_groups
    exists to avoid.
    """

    def test_the_session_is_skipped_without_the_tan_controller(self):
        source = inspect.getsource(client._fetch_session)
        self.assertIn("_use_tan_authentication()", source)
        self.assertIn(
            "nullcontext()", source,
            "In legacy mode every command must open and close its own dialog, "
            "as it did before sessions existed.",
        )

    def test_no_caller_enters_the_session_directly(self):
        source = inspect.getsource(client)
        self.assertNotIn(
            "with fints_session():", source,
            "Every entry point must go through _fetch_session(), otherwise it "
            "bypasses the legacy-mode check.",
        )
