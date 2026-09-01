# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import contextlib
import frappe
import json
import mt940

from dateutil.relativedelta import relativedelta
from fints.client import FinTS3PinTanClient, FinTSClientMode, NeedTANResponse, NeedRetryResponse

from frappe import _
from frappe.utils import now_datetime, cint, time_diff_in_hours
from frappe.utils.file_manager import (
    save_file,
    get_file,
    get_content_hash,
)
from kefiya.utils import fints_response
from kefiya.utils.fints_dialog_state import (
    dialog_is_usable, discard_unusable_dialog,
)
from .import_bank_transaction import (
    ImportBankTransaction,
    resolve_incremental_from_date,
)
from .auto_reconcile import run_after_import
from .assign_payment_controller import AssignmentController
from kefiya.utils import accepted_payee
from kefiya.utils import fints_vop
from kefiya.utils import fints_vop_client
from kefiya.utils import pain_payee
from kefiya.utils import vop_rule
from kefiya.utils.fints_interactive import FinTSInteractive  # noqa: F401
from kefiya.utils.fints_masking import mask_iban
from kefiya.utils.fints_tan_session import TanSession

#: How long a parked TAN challenge is still worth replaying. Banks keep a
#: challenge for minutes; a day covers even a decoupled release that someone
#: confirms much later. Beyond that the replay can only fail -- and a failing
#: replay used to end every attempt to use the access.
PARKED_CHALLENGE_MAX_AGE_HOURS = 24

# Weitergereicht, nicht neu definiert: fints_tan_session braucht dieselben
# beiden Ausnahmen, und dieses Modul importiert fints_tan_session -- also
# wohnen sie in fints_errors. Die Namen bleiben hier sichtbar, weil client.py
# sie an drei Stellen von hier holt.
from kefiya.utils.fints_errors import (  # noqa: F401
    InitFailedException,
    TanInteractionRequired,
)


#: How many of the bank's IBANs an "account not found" message lists before it
#: switches to a count. Enough to identify the access, short enough to read.
OFFERED_IBAN_PREVIEW = 10


#: (connect, read) timeout for every FinTS message, in seconds.
#:
#: python-fints posts each message with ``requests.Session.post()`` and passes
#: no timeout at all, so a gateway that accepts the connection and then says
#: nothing blocks the worker for as long as the operating system lets it. That
#: is not a theoretical concern: it leaves no error, no log line and no commit
#: -- the request simply never ends, the browser keeps showing the last
#: progress step, and everything the request had written is lost. A collective
#: fetch walks 57 logins, so one silent gateway is a site-wide hazard.
#:
#: The read value is the gap BETWEEN bytes, not the total duration, so a slow
#: statement fetch is unaffected; two minutes of complete silence is a dead
#: connection by any reading.
FINTS_TIMEOUT = (15, 120)


def _is_missing_bank_parameters(exc):
    """Is this the "could not fetch BPD" failure, whatever raised it?

    Matched on the message rather than the class: python-fints raises a plain
    FinTSClientError for it, the same class it uses for a dozen unrelated
    things. Narrow on purpose -- everything else must keep propagating.
    """
    text = str(exc or "")
    return "could not fetch BPD" in text


#: The bank checked the payee and now wants that check confirmed.
VOP_CONFIRMATION_DEMANDED = "3945"

#: There was a diagnostic here that wrote the bank's HIVPP segment to the
#: Error Log on a 3945, so the confirmation could be built from real data
#: instead of guesswork. It could not have worked, and shipping it was a
#: second guess on top of the first.
#:
#: By the time submit_sepa_transfer sees the answer, python-fints has turned
#: the FinTS message into a TransactionResponse -- which holds status,
#: responses and data, and neither the raw segments nor a find_segment_first
#: to look for them in. The log line would have read "HIVPP: not in the
#: response" every time, and read as a fact about the bank.
#:
#: The same applies to the confirmation itself, and that is the real finding:
#: approve_vop_response needs the HIVPP *and* the command segment that was
#: sent, and both are local to _send_pay_with_possible_retry inside the
#: library. Neither can be recovered downstream. Closing this needs the send
#: flow itself, not something bolted to its result.


def _has_bank_parameters(connection):
    """Did this connection actually reach the bank's parameter data?

    The BPD is what a dialog is built from. python-fints starts with an empty
    SegmentSequence and fills it during dialog initialisation, so an empty one
    means the initialisation never got there.

    Unknown shapes answer True: a library version this cannot read must behave
    exactly as it did before this guard existed, and that was "always persist".
    """
    try:
        bpd = getattr(connection, "bpd", None)
        if bpd is None:
            return True
        segments = getattr(bpd, "segments", None)
        if segments is None:
            return True
        return bool(segments)
    except Exception:
        return True


def _apply_connection_timeout(client):
    """Give a FinTS client's HTTP session a timeout it does not bring itself.

    Patches the bound ``request`` of the session python-fints created, which is
    what ``post()`` goes through. Best-effort and idempotent: a library version
    that arranges its connection differently simply keeps its own behaviour
    rather than failing the fetch on the way in.
    """
    try:
        session = client.connection.session
    except AttributeError:
        return
    if getattr(session, "_kefiya_timeout_applied", False):
        return

    original_request = session.request

    def request_with_timeout(*args, **kwargs):
        kwargs.setdefault("timeout", FINTS_TIMEOUT)
        return original_request(*args, **kwargs)

    try:
        session.request = request_with_timeout
        session._kefiya_timeout_applied = True
    except Exception:
        frappe.logger("kefiya").exception(
            "Kefiya: could not put a timeout on the FinTS connection")


def _end_dialog_unless_paused(conn, context=""):
    """End a FinTS dialog -- unless a TAN request parked it.

    A paused dialog is somebody's half-finished authentication: the bank is
    holding a challenge open, and the user is on their way to release it in the
    banking app. Sending HKEND throws that challenge away, so the release, when
    it comes, unlocks nothing and the next attempt starts a fresh one -- which
    is a loop the user cannot get out of by trying harder.
    """
    dialog = getattr(conn, "_standing_dialog", None)
    if dialog is not None and getattr(dialog, "paused", False):
        return

    try:
        conn.__exit__(None, None, None)
    except Exception:
        frappe.logger("kefiya").exception(
            "Kefiya: closing a {0}FinTS dialog failed".format(
                context + " " if context else ""))


#: Key under which the active fetch session lives on ``frappe.local``. Request
#: scoped by construction: frappe.local is rebuilt for every request and every
#: background job, so a session can never outlive the work it belongs to.
_SESSION_KEY = "kefiya_fints_session"


def _active_session():
    return getattr(frappe.local, _SESSION_KEY, None)


def _access_key(kefiya_login):
    """Identify the bank access (online-banking contract) of a login.

    Two logins with the same BLZ and the same FinTS login are the same access
    mapped to two accounts -- the bank sees one contract, and one dialog can
    serve both. The BLZ alone is not enough: two separate contracts at the same
    bank must never share a dialog.
    """
    blz = kefiya_login.blz
    fints_login = kefiya_login.fints_login
    if not (blz and fints_login):
        return None
    return (str(blz), str(fints_login))


def _is_unsupported_operation(exc):
    """True for "this bank does not offer that segment".

    Decided from the stored BPD without ever touching the dialog, so it leaves
    the connection perfectly usable -- and it is the common case here, twelve
    in a single collective run. Every other failure may have left the dialog
    broken or parked.
    """
    try:
        from fints.exceptions import FinTSUnsupportedOperation
    except Exception:
        return False
    return isinstance(exc, FinTSUnsupportedOperation)


def _retire_connection(session, conn):
    """Drop a connection from the session after a command failed on it.

    With a dialog per command, a broken dialog died with the command that broke
    it. In a shared session every later command inherits it, so one failure
    would take down the rest of the login -- or, in a group fetch, the rest of
    the bank access. Retiring it means the next controller opens a fresh dialog
    and pays one handshake, which is what would have happened anyway before.

    A paused dialog is deliberately left alone: a TAN request parks it for the
    user to release later, and ending it would throw that away. It is still
    removed from the session, because nothing may send on a paused dialog.
    """
    for access, registered in list(session.get("connections", {}).items()):
        if registered is conn:
            session["connections"].pop(access, None)

    if conn in session.get("open", []):
        session["open"].remove(conn)

    _end_dialog_unless_paused(conn, "failed")


@contextlib.contextmanager
def fints_session():
    """Hold one FinTS dialog open across everything done inside.

    Without this, a single "fetch everything" for one login builds five
    controllers -- transactions, balance, standing orders, statements, credit
    card -- and each opens its own dialog: HKIDN/HKVVB, authentication, the one
    command that actually carries data, HKEND. The handshake costs several
    round trips, the data costs one, and that ratio is the whole reason a
    collective fetch of 48 accounts takes twelve minutes.

    Inside a session, controllers of the same bank access share one client and
    one open dialog, so the handshake is paid once instead of once per command
    -- and, for a caller that wraps several logins of the same access, once per
    access instead of once per account.

    Nesting is allowed and simply joins the outer session, so wrapping
    fetch_all() is enough for a single login and wrapping a whole group is
    enough for a bank access.

    Outside a session nothing changes: every dialog is opened and closed
    exactly as before.
    """
    if _active_session() is not None:
        yield
        return

    setattr(frappe.local, _SESSION_KEY, {"connections": {}, "open": []})
    try:
        yield
    finally:
        session = _active_session() or {"open": []}
        setattr(frappe.local, _SESSION_KEY, None)
        # Close every dialog this session opened. Guarded per connection: the
        # session ends on the error path too, and a bank that dropped the
        # connection must not turn into a second exception on the way out.
        #
        # A dialog a TAN request parked is left standing. _retire_connection
        # has always spared those; this loop did not, so a challenge parked
        # inside a fetch session was ended again on the way out -- and the
        # release the user then gave in their banking app had nothing left to
        # unlock.
        for conn in session.get("open", []):
            _end_dialog_unless_paused(conn, "shared")



#: Kept as a module-level name because callers throughout this file use it.
_mask_iban = mask_iban


def _advice_block(verdict):
    """What to do about the codes the bank sent, appended to a refusal.

    Empty for a code nobody here has met, and that is the point: the bank's
    own sentence is always shown, and an explanation is added only where we
    know one. An invented explanation reads exactly like a researched one.
    """
    hints = fints_response.advice(verdict)
    if not hints:
        return ""
    return "\n\n" + _("What this means:") + "\n" + "\n".join(
        "• " + _(hint) for hint in hints)

class FinTSController(TanSession):
    def __init__(self, kefiya_login_docname:str, interactive:bool=False, tan_mode:str=None, tan_medium:str=None, tan:str=...):
        self.kefiya_login = frappe.get_doc("Kefiya Login", kefiya_login_docname)

        # If this is load during a user interaction, verify its permissions (ignore for scheduled background tasks)
        if interactive and interactive["enabled"]:
            frappe.has_permission(ptype="write", doc=self.kefiya_login, throw=True)

        self.name = self.kefiya_login.name
        self.interactive = FinTSInteractive(interactive)
        # Which login, as opposed to which screen. A transfer passes its own
        # docname as the UI scope, and everything that read the login out of
        # that was reading the wrong document.
        self.interactive.fints_login = self.kefiya_login.name

        self.__init_fints_connection()

        # If state is new and did not setup tan requirements, init tan mode (and probably ask user for mode and medium).
        if self.__init_tan_mode(tan_mode, tan_medium, tan) is False:
            raise TanInteractionRequired()

        # If there is an open TAN request, try to fulfill it.
        #
        # Not while a dialog of this access is already standing: resume_dialog()
        # refuses to run inside one, and it would be pointless anyway -- an open
        # dialog means the access is authenticated, so this login's parked
        # challenge is a leftover from an earlier attempt and has nothing left
        # to unlock.
        if self.kefiya_login.stored_tan_blob \
                and not self.fints_connection._standing_dialog:
            # A challenge lives only as long as the bank keeps the dialog
            # behind it. Nothing expired it on our side, so an unanswered
            # challenge stayed parked forever -- and replaying it does not
            # fail quietly: the bank answers without a TAN status, fints
            # raises, and the raise ends every attempt to use this access. One
            # challenge nobody released in July made an account unable to send
            # anything in August.
            if self.__parked_challenge_is_stale():
                self.__discard_parked_challenge()
            else:
                try:
                    self._resume_and_answer_the_parked_tan(tan)
                except TanInteractionRequired:
                    # The legitimate "ask the user" path, not a failure -- but
                    # the dialog behind it is gone all the same. The exception
                    # leaves resume_dialog()'s block, its __exit__ ends the
                    # dialog, and the client goes on holding a reference to it.
                    # Dropping that reference is what lets the next attempt
                    # open a fresh dialog instead of sending on a dead one.
                    discard_unusable_dialog(self.fints_connection)
                    raise
                except Exception:
                    # Two things are wrong here at once, and each needs its own
                    # cleanup.
                    #
                    # resume_dialog() clears the client's reference to the
                    # dialog when its block ends -- but it has no try/finally,
                    # so an exception inside skips that line and leaves an
                    # ENDED dialog registered. Inside a fetch session that
                    # client is shared by every login of the bank access, so
                    # each of them in turn joins the wreck: "Cannot send on
                    # dialog that is not open", for one account after another.
                    discard_unusable_dialog(self.fints_connection)

                    # And the bank does not accept this challenge any more.
                    # Keeping it would brick the access; throwing it away costs
                    # one fresh TAN, which the ordinary login now asks for.
                    self.__discard_parked_challenge()

        # After successful login/tan verification fetch available accounts if not already present
        if self.__fetch_fints_accounts() is False:
            raise InitFailedException()

        # Afterwards the controller/connection is ready to go for further operations
        if self.kefiya_login.failed_connection:
            self.kefiya_login.failed_connection = 0
            self.kefiya_login.save()

        self.interactive.show_progress_realtime(
           _("Connection established"), 100, reload=False
        )

    def _refuse_a_refused_order(self, response=None):
        """Stop where the bank turned the order down. Answers the verdict.

        The last question on every money path, and it was asked on exactly
        one of them. submit_sepa_transfer read the answer; the payee release
        and the direct debit both ended with

            frappe.log_error("... completed without TAN challenge")
            return {"status": "submitted"}

        -- noting that the bank had said something, and reporting success
        anyway. Same file, same question, three different answers.

        :param response: what the bank last said; the answer to the TAN when
            omitted, which is where this is called from after a release
        :return: the verdict, for a caller that also wants the unsigned guard
        """
        if response is None:
            response = getattr(
                self.fints_connection, "init_tan_response", None)
        verdict = fints_response.verdict_of(response)
        if not fints_response.refused(verdict):
            return verdict
        frappe.log_error(
            title="Kefiya: the bank refused the order",
            message="login={0}\n{1}".format(
                self.kefiya_login.name, fints_response.as_text(verdict)),
        )
        frappe.throw(
            _("The bank refused this order. It was NOT sent.\n\n{0}").format(
                fints_response.as_text(verdict))
            + _advice_block(verdict),
            title=_("Refused by the bank"),
        )

    def __init_fints_connection(self):
        """Private: Initialise new fints connection.

        :return: None
        """
        if hasattr(self, "fints_connection"):
            return

        # Inside a fetch session, the logins of one bank access share a single
        # client -- and with it the dialog that client_session() keeps open. A
        # second login of the same access then costs one command instead of a
        # full handshake. Strictly keyed on (BLZ, FinTS login): routing a
        # login's requests through another contract's credentials would read
        # the wrong accounts, so anything else gets its own connection.
        session = _active_session()
        access = _access_key(self.kefiya_login) if session else None
        if access and access in session["connections"]:
            self.fints_connection = session["connections"][access]
            return

        # "TAN once per bank": before opening a brand-new dialog (which would
        # trigger a fresh SCA/TAN), try to reuse an already-authenticated FinTS
        # session from a sibling login of the same bank (same blz + fints_login).
        self._seed_client_state_from_sibling()

        self.interactive.show_progress_realtime(
            _("Initialise connection"), 10, reload=False
        )

        try:
            # The library's own class, unless this is the one release the
            # missing VoP branch was written against. See fints_vop_client.
            client_class = fints_vop_client.client_class() or FinTS3PinTanClient
            self.fints_connection = client_class(
                self.kefiya_login.blz,
                self.kefiya_login.fints_login,
                self.kefiya_login.get_password("fints_password"),
                self.kefiya_login.fints_url,
                product_id=self.kefiya_login.get_password("product_id"),
                mode=FinTSClientMode.INTERACTIVE,
                from_data=self.kefiya_login.stored_client_blob,
            )
        except Exception as e:
            frappe.throw(
                _("Could not conntect to fints server with error<br>{0}").format(e)
            )

        # The library posts every message without a timeout; give it one before
        # the first message goes out.
        _apply_connection_timeout(self.fints_connection)

        # Offer it to the rest of the session, so the next login of this access
        # joins this client instead of opening its own.
        if access:
            session["connections"][access] = self.fints_connection

    @contextlib.contextmanager
    def client_session(self):
        """Open a FinTS dialog, join the open one, or leave it open.

        Three cases. A dialog is already standing on this client: join it, and
        leave closing to whoever opened it. A fetch session is active: open the
        dialog and hand it to the session, which keeps it open for every
        further command and closes it at the end. Neither: open and close it
        here, exactly as before this existed.

        Every read below used to open the client directly, and
        FinTS3PinTanClient refuses to be entered twice ("Cannot double
        __enter__"). One dialog per command means an HKIDN/HKVVB round trip
        plus authentication plus an HKEND for every single command -- which is
        where nearly all the time of a fetch goes. The data itself is one
        message; the handshake around it is four.

        Joining an already-open dialog lets a caller wrap a whole login, or a
        whole bank access, in one dialog and pay that cost once. Nothing
        changes for callers that do not: with no standing dialog this behaves
        exactly as before.

        Deliberately not used by the money paths (submit_sepa_transfer,
        submit_sepa_debit). Those park the dialog on a TAN request, and
        freezing a dialog that a surrounding fetch is still using would break
        it.
        """
        conn = self.fints_connection
        session = _active_session()

        # A dialog that was ended but never unregistered cannot be sent on, and
        # joining it is how one login's parked TAN broke every later login of
        # the same access. Dropped here so the normal open path runs.
        if conn._standing_dialog and not dialog_is_usable(conn):
            if discard_unusable_dialog(conn) and session is not None:
                # The shared client is only as good as its dialog was: take it
                # out so the next login builds a clean one rather than
                # inheriting whatever put this one in this state.
                session.get("connections", {}).pop(
                    _access_key(self.kefiya_login), None)

        if conn._standing_dialog:
            # Joining a dialog somebody else opened. Inside a session that is
            # the normal case -- every command after the first lands here, and
            # so does every login of the access after the first -- so a failure
            # has to retire the connection exactly as the opener below does.
            # Without that, a TAN request parks the dialog and leaves it
            # registered, and the next command sends on a frozen dialog:
            # "Cannot send() on a paused dialog", for every remaining account
            # of that bank.
            #
            # With no session the dialog belongs to a surrounding `with conn:`
            # that will clean up after itself. Retiring it here would close a
            # dialog still in use.
            if session is None:
                yield conn
                return

            try:
                yield conn
            except Exception as exc:
                if not _is_unsupported_operation(exc):
                    _retire_connection(session, conn)
                raise
            return

        if session is None:
            with conn:
                yield conn
            return

        try:
            conn.__enter__()
        except Exception:
            # A dialog whose init failed -- wrong credentials, bank in
            # maintenance -- leaves _standing_dialog set but never opened.
            # python-fints then reads that as "a dialog is standing", so the
            # next login of this access would join a dialog that is dead, and
            # every command on it would fail. One bad handshake would take the
            # whole access down for the rest of the run. Take it out of the
            # session so the next controller starts clean.
            _retire_connection(session, conn)
            raise

        session["open"].append(conn)
        try:
            yield conn
        except Exception as exc:
            # Anything but an unsupported segment may have left the dialog
            # broken or parked, and every later command in this session would
            # inherit it.
            if not _is_unsupported_operation(exc):
                _retire_connection(session, conn)
            raise

    def _refuse_inside_fetch_session(self):
        """Refuse to move money through a dialog a fetch is sharing.

        A transfer or direct debit parks the dialog on the TAN request
        (pause_dialog). The read paths avoid that by construction: a parked
        dialog is retired from the session so nothing sends on it again. The
        money paths cannot be retired the same way, because parking is their
        SUCCESS case -- they return "tan_required" rather than raising -- so the
        session would keep handing a frozen dialog to every later command.

        Nothing does this today: fetching and transferring are separate
        requests. But since a controller built inside a session adopts the
        shared client, it became possible, and a corrupted collective fetch is
        a far worse outcome than a refused order.
        """
        if _active_session() is not None:
            frappe.throw(_(
                "A SEPA order cannot be sent while a bank fetch is running."
                " Wait for the fetch to finish and send it again."
            ))

    @contextlib.contextmanager
    def trusted_client_context(self):
        """
        Opens the fints client context (will most likely generate a new fints dialog) and checks if a TAN is requested.
        If so, it will be requested from the user and the context body will be skipped.
        """
        with self.client_session():
            if self.is_tan_required_and_requested(self.fints_connection.init_tan_response):
                raise TanInteractionRequired()

            yield self.fints_connection

    def is_tan_required_and_requested(self, response) -> bool:
        """Checks the given response, if a TAN is required. If so, it is requested from the user.
        :param response: The response object from the FinTS server
        :return: True if a TAN is required (and requested), False otherwise
        """
        if isinstance(response, NeedTANResponse):
            self.ask_for_tan(response)
            return True

        return False

    def __parked_challenge_is_stale(self):
        """Is the parked TAN challenge too old to still be worth replaying?

        Banks keep a challenge for minutes. A day is generous even for a
        decoupled release that someone confirms after a long lunch -- and every
        hour beyond that is an hour in which the replay can only fail.
        """
        parked_since = self.kefiya_login.tan_state_updated
        if not parked_since:
            # No timestamp means the state predates the field. Old by definition.
            return True

        return time_diff_in_hours(now_datetime(), parked_since) > PARKED_CHALLENGE_MAX_AGE_HOURS

    def __discard_parked_challenge(self):
        """Forget a challenge that can no longer be released.

        Cleared directly rather than through _persist_fints_state, because the
        client state at this moment may be the one the failed replay left
        behind -- and that is not a state worth keeping, let alone sharing with
        the sibling logins of the same bank.
        """
        self.kefiya_login.stored_tan_blob = None
        self.kefiya_login.stored_tan_state_decoupled = None
        self.kefiya_login.stored_vop_id_blob = None
        self.kefiya_login.stored_dialog_blob = None
        self.kefiya_login.save()

    def _forget_client_state(self):
        """Drop the stored connection state of this login and its siblings.

        The siblings matter: the state is shared across the logins of one bank
        access, so leaving theirs in place would hand the same unusable state
        straight back on the next attempt through any of them.
        """
        try:
            self.kefiya_login.stored_client_blob = None
            self.kefiya_login.stored_tan_blob = None
            self.kefiya_login.stored_tan_state_decoupled = None
            self.kefiya_login.stored_vop_id_blob = None
            self.kefiya_login.stored_dialog_blob = None
            self.kefiya_login.save()

            # The siblings share this state -- _seed_client_state_from_sibling
            # hands the freshest one to whichever login has none. Clearing only
            # this login would fetch the same unusable state straight back from
            # the one next door.
            filters = self._sibling_login_filters()
            if filters:
                filters["stored_client_state"] = ("is", "set")
                for row in frappe.get_all("Kefiya Login", filters=filters,
                                          fields=["name"],
                                          limit_page_length=0):
                    frappe.db.set_value(
                        "Kefiya Login", row["name"],
                        {"stored_client_state": None,
                         "stored_tan_state": None,
                         "stored_tan_state_decoupled": None,
                         "stored_dialog_state": None},
                        update_modified=False)

            frappe.db.commit()
        except Exception:
            frappe.log_error(
                title="Kefiya: discarding the unusable client state failed",
                message=frappe.get_traceback(),
                reference_doctype="Kefiya Login",
                reference_name=self.kefiya_login.name,
            )

    def _persist_fints_state(self, tan_state=None, clear:bool=False):
        """Persist the current client/dialog state to the database.
        :param tan_state: An additional TAN state to persist if given.
        :param clear: Reset all the stored states
        :return: None
        """
        # A state without bank parameters is worse than no state at all. It is
        # restored on the next attempt, the dialog it builds has no BPD, and
        # python-fints answers "could not fetch BPD" -- which persists the same
        # empty state again. A send that failed once then fails the same way
        # for good, and the only way out is deleting the field by hand.
        #
        # So an empty BPD keeps the last good state rather than overwriting it.
        # The cost of being wrong here is one extra handshake; the cost of the
        # other direction is an access nobody can use.
        if _has_bank_parameters(self.fints_connection):
            self.kefiya_login.stored_client_blob = \
                self.fints_connection.deconstruct(including_private=True)

        if tan_state and isinstance(tan_state, NeedTANResponse):
            self.kefiya_login.stored_tan_blob = tan_state.get_data()
            self.kefiya_login.stored_tan_state_decoupled = tan_state.decoupled
            # And the VoP-ID, for the same reason `decoupled` is carried by
            # hand one line up: get_data() serialises the command and the TAN
            # request only. A challenge parked with a payee check comes back
            # without one, the approval segment HKVPA(vop_id=...) is then not
            # sent with the TAN, and the bank refuses -- 3945, "Freigabe ohne
            # VOP-Bestaetigung nicht moeglich". Which is what every Volksbank
            # transfer ended on.
            self.kefiya_login.stored_vop_id_blob = fints_vop.vop_id_of(tan_state)
            self.kefiya_login.stored_dialog_blob = self.fints_connection.pause_dialog()
        else:
            self.kefiya_login.stored_tan_blob = None
            self.kefiya_login.stored_tan_state_decoupled = None
            self.kefiya_login.stored_vop_id_blob = None
            self.kefiya_login.stored_dialog_blob = None

        self.kefiya_login.save()

        # A clean, TAN-free authenticated state was just persisted -> share it
        # with the sibling logins of the same bank so a single TAN unlocks every
        # account ("TAN once per bank"). Never propagate a state that still
        # carries a pending TAN challenge.
        if not (tan_state and isinstance(tan_state, NeedTANResponse)):
            self._propagate_client_state_to_siblings()

    def _sibling_login_filters(self):
        """Filters selecting the OTHER Kefiya Logins that share this login's
        bank credentials (same BLZ + FinTS login) -- i.e. the same online-banking
        access, just mapped to different bank accounts. Returns None when this
        login has no usable credentials yet."""
        blz = self.kefiya_login.blz
        fints_login = self.kefiya_login.fints_login
        if not (blz and fints_login):
            return None
        return {
            "name": ("!=", self.kefiya_login.name),
            "blz": blz,
            "fints_login": fints_login,
        }

    def _seed_client_state_from_sibling(self):
        """If this login has no stored FinTS client state, borrow the freshest
        one from a sibling login of the same bank so a single TAN authorises
        every account of that bank. The encrypted blob is portable because it is
        sealed with the site key, not a per-record salt. In-memory only; it is
        persisted on the next successful _persist_fints_state(). Best-effort --
        on any problem we simply fall back to the normal (per-login) TAN flow."""
        try:
            if self.kefiya_login.stored_client_state:
                return
            filters = self._sibling_login_filters()
            if not filters:
                return
            filters["stored_client_state"] = ("is", "set")
            rows = frappe.get_all(
                "Kefiya Login",
                filters=filters,
                fields=["stored_client_state"],
                order_by="client_state_updated desc",
                limit=1,
            )
            if rows and rows[0].stored_client_state:
                self.kefiya_login.stored_client_state = rows[0].stored_client_state
        except Exception:
            frappe.log_error(
                title="Kefiya: seed client state from sibling failed",
                message=frappe.get_traceback(),
            )

    def _propagate_client_state_to_siblings(self):
        """Share this login's freshly authenticated FinTS client state with the
        sibling logins of the same bank that have no state yet (or an older one),
        so one TAN unlocks every account. Best-effort; never raises."""
        try:
            filters = self._sibling_login_filters()
            state = self.kefiya_login.stored_client_state
            mine = self.kefiya_login.client_state_updated
            if not (filters and state):
                return
            siblings = frappe.get_all(
                "Kefiya Login",
                filters=filters,
                fields=["name", "stored_client_state", "client_state_updated",
                        "stored_tan_state", "stored_dialog_state"],
            )
            for s in siblings:
                # Never pull the client state out from under a parked
                # challenge. A paused dialog belongs to the client state it was
                # frozen with -- message counters, dialog id, signature
                # counter -- so replacing that state leaves the user releasing
                # the payment in their banking app while the resumed dialog no
                # longer fits its client, and the release fails.
                #
                # This went unnoticed until the challenge started surviving at
                # all: before that it was destroyed by the unpacking bug long
                # before a sibling could overwrite anything. The parallel fetch
                # makes it near-certain, because the siblings of one access now
                # run seconds apart.
                if s.stored_tan_state or s.stored_dialog_state:
                    continue

                # keep a sibling's own session if it is at least as fresh
                if s.stored_client_state and mine and s.client_state_updated and s.client_state_updated >= mine:
                    continue
                frappe.db.set_value(
                    "Kefiya Login",
                    s.name,
                    {"stored_client_state": state, "client_state_updated": mine},
                    update_modified=False,
                )
        except Exception:
            frappe.log_error(
                title="Kefiya: propagate client state to siblings failed",
                message=frappe.get_traceback(),
            )

    def __init_tan_mode(self, tan_mode:str=None, tan_medium:str=None, tan:str=...) -> bool:
        """
        Initializes the TAN mode to use. If there are multiple TAN mechanisms available, the user is asked to choose one (if no choise is permitted).

        :param tan_mode: The name of the TAN mechanism to use (choice was already made, if permitted)
        :param tan_medium: The name of the TAN medium (on several devices, ...) to use (choice was already made, if permitted)
        :return bool: Indicates if initialization was complete or should be stopped (because user interaction is required)
        """
        if not self.fints_connection.get_current_tan_mechanism():
            self.interactive.show_progress_realtime(
                _("Initialise TAN settings"), 20, reload=False
            )

            self.fints_connection.fetch_tan_mechanisms()

            mechanisms = self.fints_connection.get_tan_mechanisms().items()

            if len(mechanisms) == 0:
                frappe.throw(_("No TAN mechanisms available"))

            mechanism_names = ["{p.security_function}: {p.name}".format(p=m[1]) for m in mechanisms]

            # If there is only one tan mechanism available, use it without asking
            if len(mechanisms) == 1:
                tan_mode = mechanism_names[0]

            if tan_mode and tan_mode in mechanism_names:
                # set tan mechanism
                selected_mode = list(mechanisms)[mechanism_names.index(tan_mode)]
                self.fints_connection.set_tan_mechanism(selected_mode[0])
            else:
                # request tan mechanism from user
                self.interactive.request_tan_mechanism(mechanism_names)
                return False

        # discover tan medium handling
        if self.fints_connection.selected_tan_medium is None and self.fints_connection.is_tan_media_required():
            m = self.fints_connection.get_tan_media() # this request can already trigger another TAN request, which is not cool, but for other banks this makes more sense hopefully
            medium_names = None

            if len(m[1]) == 1:
                self.fints_connection.set_tan_medium(m[1][0])
            elif len(m[1]) == 0:
                # This is a workaround for when the dialog already contains return code 3955.
                # This occurs with e.g. Sparkasse Heidelberg, which apparently does not require us to choose a
                # medium for pushTAN but is totally fine with keeping "" as a TAN medium.
                self.fints_connection.selected_tan_medium = ""
            elif tan_medium and tan_medium in [mm.tan_medium_name for mm in m[1]]:
                self.fints_connection.set_tan_medium(next(mm for mm in m[1] if mm.tan_medium_name == tan_medium))
            else:
                # Multiple tan media available. Prompt user
                medium_names = []
                for mm in m[1]:
                    medium_names.append("Medium {p.tan_medium_name}: Phone no. {p.mobile_number_masked}, Last used {p.last_use}".format(p=mm))

                self.interactive.request_tan_mechanism([tan_mode], medium_names)
                return False

            # if the request already claimed a tan, persist state and ask for tan
            if self.fints_connection.init_tan_response and self.fints_connection._standing_dialog:
                # (Only!) If the tan request opened a dialog, which can be paused for later (continue after user-interaction)
                # If not, it needs to be skipped (leads to multiple requests on the phone, but there is now option to persist the state, and it works)
                self.ask_for_tan(self.fints_connection.init_tan_response, possible_tan_modes=[tan_mode], possible_tan_mediums=medium_names)
                return False

        return True

    def __fetch_fints_accounts(self) -> bool:
        """Fetch FinTS Accounts.

        :return: True on success. False or an exception otherwise.
        """
        if hasattr(self, 'fints_accounts'):
            return True

        if self.kefiya_login.iban_list:
            cached_accounts = json.loads(self.kefiya_login.iban_list)
            if cached_accounts and isinstance(cached_accounts[0], dict):
                self.fints_accounts = [frappe._dict(acc) for acc in cached_accounts]
                return True

        try:
            with self.trusted_client_context() as client:
                self.interactive.show_progress_realtime(
                    _("Loading accounts"), 30, reload=False
                )

                accounts_response = client.get_sepa_accounts()
                if self.is_tan_required_and_requested(accounts_response):
                    return False

                self.fints_accounts = accounts_response
                self.kefiya_login.iban_list = json.dumps(
                    [a.iban for a in self.fints_accounts if getattr(a, "iban", None)]
                )

                # Reading the accounts is part of the first initialization. So after this was successful, the
                # client state can be persisted for future use.
                self._persist_fints_state()

                self.interactive.show_progress_realtime(
                    _("Loading accounts completed"), 100, reload=True
                )
                return True

        except TanInteractionRequired:
            raise
        except Exception as e:
            frappe.throw(_(
                "Could not load sepa accounts with error:<br>{0}"
            ).format(e))

        return False

    def __get_fints_account_by_key(self, key, value):
        if not value:
            return None

        for acc in self.fints_accounts:
            if isinstance(acc, dict):
                if acc.get(key) == value:
                    return acc
            else:
                try:
                    if getattr(acc, key) == value:
                        return acc
                except AttributeError:
                    frappe.throw(_(
                        "SEPA account object has no key '{0}'"
                    ).format(key))

        # Account can be None
        return None


    @staticmethod
    def get_kefiya_import_file_content(kefiya_import):
        """Get Kefiya Import json file content as json.

        :param kefiya_import: kefiya_import doc
        :type kefiya_import: kefiya_import doc
        :return: Transaction from file as json object list
        """
        if kefiya_import.file_url:
            content = get_file(kefiya_import.file_url)[1]
            # Check content hash for file manipulations
            if kefiya_import.file_hash == get_content_hash(content):
                return json.loads(
                    content,
                    strict=False
                )
            else:
                raise ValueError('File hash does not match')
        else:
            return []

    def get_fints_connection(self):
        """Get the FinTS Connection object.

        :return: FinTS3PinTanClient
        """
        return self.fints_connection

    def get_fints_accounts(self):
        """Get FinTS Accounts.

        :return: List of SEPAAccount objects.
        """
        return self.fints_accounts

    def get_fints_account_by_iban(self, iban):
        """Get FinTS account by iban number.

        :param iban: bank iban number
        :type iban: str
        :return: SEPAAccount
        """
        return self.__get_fints_account_by_key("iban", iban)

    def _get_transactions_raw(self, account, start_date, end_date,
                              include_pending=False):
        """python-fints' ``get_transactions()``, but keeping a TAN challenge.

        The library unpacks the camt result into (booked, pending) and therefore
        dies with "cannot unpack non-iterable NeedTANResponse object" as soon as
        the bank answers the statement request with a TAN challenge instead of
        the data -- and the challenge object dies with it. Without it neither a
        TAN nor a decoupled confirmation can ever be completed, so the login is
        stuck for good.

        Mirroring the library internals here is the same approach
        ``get_fints_balance`` already takes for HKSAL, for the same reason: the
        public method throws information away that we need. Should a future
        library version rename those internals, we fall back to the public call
        and are no worse off than before.

        :return: the transaction list, or the NeedTANResponse to act on
        """
        conn = self.fints_connection
        try:
            from fints.camt_parser import camt053_to_dict
            from fints.exceptions import FinTSUnsupportedOperation
            from fints.models import Transaction
            from fints.segments.statement import (
                HKCAZ1, HKKAZ5, HKKAZ6, HKKAZ7)

            get_dialog = conn._get_dialog
            find_command = conn._find_highest_supported_command
            fetch_mt940 = conn._get_transactions_mt940
            fetch_xml = conn._get_transactions_xml
        except (AttributeError, ImportError):
            return conn.get_transactions(
                account, start_date, end_date, include_pending)

        with get_dialog() as dialog:
            try:
                hkkaz = find_command(HKKAZ5, HKKAZ6, HKKAZ7)
                # MT940 banks return the challenge instead of the statements;
                # it is passed through untouched for the caller to handle.
                return fetch_mt940(
                    dialog, hkkaz, account, start_date, end_date,
                    include_pending)
            except FinTSUnsupportedOperation:
                hkcaz = find_command(HKCAZ1)
                result = fetch_xml(
                    dialog, hkcaz, account, start_date, end_date)
                if isinstance(result, NeedRetryResponse):
                    return result
                streams = [x for x in result[0] if x]
                if include_pending:
                    # python-fints appends seg.statement_pending unfiltered, so
                    # a bank that sends no pending block yields [None] -- which
                    # is truthy. camt053_to_dict(None) then dies on the parser,
                    # the shared dialog is retired, and every later command of
                    # that login fails with "could not fetch BPD". One missing
                    # optional field took out holdings and statements too.
                    streams += [x for x in (result[1] or []) if x]
                return [
                    Transaction(txn)
                    for stream in streams
                    for txn in camt053_to_dict(stream)
                ]

    def _get_transactions_checked(self, account, start_date, end_date):
        """Fetch transactions, turning a mid-fetch TAN request into a TAN flow.

        When the bank's strong authentication has expired -- PSD2 requires it
        every 90 days -- it answers the statement request with a challenge
        instead of the data. Parking that challenge (exactly as the dialog-level
        TAN handling does) is what lets the user finish the authentication and
        fetch again.

        Which prompt is correct depends on the bank: a decoupled login
        (pushTAN 2.0, as the Volksbank uses) is released in the banking app and
        never produces a code to type in, so asking for "the TAN" there sends
        the user looking for something that does not exist.
        """
        response = self._get_transactions_raw(account, start_date, end_date)

        if not isinstance(response, NeedTANResponse):
            return response

        decoupled = bool(getattr(response, "decoupled", False))

        # Ask, then wait. A decoupled release is given in the banking app and
        # the bank will tell us when it lands -- so the run does not have to
        # end here and make the user start it again. See _await_release.
        #
        # The prompt first, the parking last, and that order is the whole
        # point. Parking pauses the dialog, and python-fints refuses to send
        # on a paused one::
        #
        #     FinTSDialogStateError: Cannot send() on a paused dialog
        #
        # This method used to park before it waited, so the very first poll
        # raised, _await_release answered None, and the user was told the
        # release had not arrived in time -- after waiting no time at all.
        # It never once worked. Three Volksbank accounts went unfetched from
        # May to August because of it, and every attempt asked for another
        # release.
        if decoupled:
            self._publish_tan_prompt(response, decoupled=True)
            released = self._await_release(response)
            if released is not None:
                return released
            # Out of patience. NOW park it, so the release still finds
            # something to unlock when it arrives; the prompt is already up.
            if not self._park_tan_challenge(response):
                self._cannot_note_the_challenge()
            raise TanInteractionRequired(_(
                "{0}: the release did not arrive in time. Confirm it in your"
                " banking app and fetch this access again -- the challenge is"
                " still open."
            ).format(self.kefiya_login.name))

        # Park the challenge and publish the matching prompt. Guarded: the
        # scheduler runs with no UI attached, and this error path must not fail
        # on its own -- the TanInteractionRequired below has to reach the caller
        # either way.
        try:
            self.ask_for_tan(response, decoupled=decoupled)
        except Exception:
            frappe.log_error(
                title="Kefiya: parking mid-fetch TAN failed",
                message=frappe.get_traceback(),
                reference_doctype="Kefiya Login",
                reference_name=self.kefiya_login.name,
            )

        # Both messages end in "fetch again", and that is not a hedge: the
        # TAN authorises the SESSION, it does not deliver the bookings.
        # resolve_tan_interaction builds an authenticated controller and stops
        # there -- nothing asks for the transactions a second time. So the
        # user hands over six digits and watches nothing happen, which reads
        # as a failure and is not one. Saying why the second run is needed,
        # and that it costs no second TAN, is the difference between an
        # instruction and a dead end.
        again = _("The session is then open: fetch this access once more and"
                  " the bookings come through without another TAN. The panel"
                  " offers that as a link once the run has finished.")

        if decoupled:
            raise TanInteractionRequired(_(
                "{0}: the bank is waiting for the release in your banking app."
                " Confirm it there -- this login uses a decoupled procedure"
                " and issues no TAN to type in."
            ).format(self.kefiya_login.name) + " " + again)

        raise TanInteractionRequired(_(
            "{0}: the bank requires a TAN before the bookings can be read."
            " Enter it in the window."
        ).format(self.kefiya_login.name) + " " + again)


    def _require_fints_account(self, iban=None):
        """Resolve the login's FinTS account, or fail with a usable message.

        python-fints dereferences ``account.iban`` unguarded, so handing it a
        None surfaces as a bare "'NoneType' object has no attribute 'iban'"
        with no hint at which login is misconfigured. That applies to every
        operation below -- balance, holdings, statements, credit card, and the
        money-moving transfer and debit paths alike -- so resolve centrally
        rather than guarding each call site. Typical cause: an account that
        carries no IBAN at all (credit cards), which cannot be addressed this
        way at all.

        :param iban: IBAN to look up; defaults to the login's account IBAN
        :return: the matching SEPAAccount (never None)
        """
        if iban is None:
            iban = self.kefiya_login.account_iban

        account = self.get_fints_account_by_iban(iban)
        if account is not None:
            return account

        # `fints_accounts` is only set once __fetch_fints_accounts() succeeded;
        # read it defensively, since this is the error path and must not raise
        # an AttributeError of its own while reporting another failure.
        offered = []
        for acc in (getattr(self, "fints_accounts", None) or []):
            value = acc.get("iban") if isinstance(acc, dict) \
                else getattr(acc, "iban", None)
            offered.append(_mask_iban(value))

        # A collective bank access can offer dozens of accounts; printing all
        # forty of them turns one misconfigured login into an unreadable wall of
        # text in the Error Log. Enough to recognise the access, not more.
        shown = offered[:OFFERED_IBAN_PREVIEW]
        rest = len(offered) - len(shown)
        if rest > 0:
            shown.append(_("and {0} more").format(rest))

        frappe.throw(_(
            "No FinTS account matching IBAN {0} for login {1}. "
            "The bank offered: {2}. Accounts without an IBAN "
            "(e.g. credit cards) cannot be fetched this way."
        ).format(
            _mask_iban(iban),
            self.kefiya_login.name,
            ", ".join(shown) or "<none>",
        ))

    def get_fints_account_by_nr(self, account_nr):
        """Get FinTS account by account number.

        :param account_nr: bank account number
        :type account_nr: str
        :return: SEPAAccount
        """
        return self.__get_fints_account_by_key(
            "accountnumber",
            account_nr
        )

    def get_fints_transactions(self, start_date=None, end_date=None):
        """Get FinTS transactions.

        The fetch window is limited to the login's allowed_sync_days_in_past
        (default 90). Also only transaction from atleast one day ago can be
        fetched

        :param start_date: Date to start the fetch
        :param end_date: Date to end the fetch
        :type start_date: date
        :type end_date: date
        :return: Transaction as json object list
        """
        allowed_days = cint(self.kefiya_login.allowed_sync_days_in_past) or 90

        if start_date is None:
            start_date = now_datetime().date() - relativedelta(days=allowed_days)

        if end_date is None:
            end_date = now_datetime().date() - relativedelta(days=1)

        if (now_datetime().date() - start_date).days > allowed_days:
            raise NotImplementedError(
                _("Start date is more than the allowed {0} days in the past").format(
                    allowed_days
                )
            )

        with self.client_session():
            account = self._require_fints_account()
            return json.loads(
                json.dumps(
                    self._get_transactions_checked(
                        account,
                        start_date,
                        end_date
                    ),
                    cls=mt940.JSONEncoder
                )
            )

    def get_fints_pending_transactions(self, start_date=None, end_date=None):
        """Fetch the entries the bank shows as pending (vorgemerkte Umsaetze).

        The bank delivers them in the same MT940/camt message as the booked
        ones, in a separate field, and python-fints only returns them when
        asked -- ``include_pending``. The booked fetch deliberately leaves it
        off: a pending entry is not a booking and must not reach the ledger.

        This asks for both and returns the difference, because the library has
        no "pending only" call. Cheap: it is one more command on a dialog that
        is already open, not a second handshake.

        :return: list of entries the bank has not booked yet
        """
        from kefiya.utils.mt940_compat import ensure_optional_timezone_is_optional

        # Without this the pending block of several Sparkassen cannot be parsed
        # at all: its date carries an optional timezone, and the parser reads a
        # missing one as int(None). See mt940_compat.
        ensure_optional_timezone_is_optional()

        allowed_days = cint(self.kefiya_login.allowed_sync_days_in_past) or 90
        if start_date is None:
            start_date = now_datetime().date() - relativedelta(days=allowed_days)
        if end_date is None:
            end_date = now_datetime().date()

        with self.client_session():
            account = self._require_fints_account()

            # Through _get_transactions_raw, not the library's public call:
            # that one dies on the camt unpacking as soon as the bank answers
            # with a TAN challenge, which is the bug this app already fixed
            # once. No reason to reintroduce it here.
            booked = self._get_transactions_raw(
                account, start_date, end_date, include_pending=False)
            both = self._get_transactions_raw(
                account, start_date, end_date, include_pending=True)

        if isinstance(booked, NeedRetryResponse) \
                or isinstance(both, NeedRetryResponse):
            # A TAN request here is not worth interrupting the fetch for: the
            # booked transactions are the point, and they already went through.
            return []

        booked_keys = {self._pending_key(t) for t in (booked or [])}
        return [t for t in (both or [])
                if self._pending_key(t) not in booked_keys]

    @staticmethod
    def _pending_key(entry):
        """Identify an entry well enough to tell booked from pending apart."""
        data = entry if isinstance(entry, dict) else getattr(entry, "data", None)
        if not isinstance(data, dict):
            try:
                data = dict(entry)
            except Exception:
                return repr(entry)
        return "|".join(str(data.get(k, "")) for k in
                        ("date", "amount", "purpose", "applicant_name"))

    def get_fints_holdings(self):
        """Fetch securities holdings (Depot / Wertpapiere) for the account.

        :return: list of plain dicts (isin, security_name, quantity, price,
            market_value, currency, valuation_date, securities_account)
        """
        with self.client_session():
            account = self._require_fints_account()
            holdings = self.fints_connection.get_holdings(account)
            result = []
            for h in holdings or []:
                result.append({
                    "isin": getattr(h, "isin", None),
                    "security_name": getattr(h, "name", None),
                    "quantity": getattr(h, "pieces", None),
                    "price": getattr(h, "market_value", None),
                    "market_value": getattr(h, "total_value", None),
                    "currency": getattr(h, "value_symbol", None),
                    "valuation_date": getattr(h, "valuation_date", None),
                    "securities_account": self.kefiya_login.account_iban,
                })
            return result

    @staticmethod
    def _amount_from(amount_field):
        """Read the numeric value of an HISAL Amount1 field group (or None)."""
        if not amount_field:
            return None
        try:
            return amount_field.amount
        except Exception:
            return None

    def get_fints_balance(self):
        """Fetch the current account balance INCLUDING the credit line
        (Kreditlinie) and the available amount.

        python-fints' high-level ``get_balance()`` only surfaces the booked
        balance; the credit line and available amount live on the HISAL response
        segment (fields ``line_of_credit`` / ``available_amount``). We therefore
        send HKSAL ourselves -- mirroring python-fints' own ``get_balance``
        internals -- and read those extra fields. TAN handling is delegated to
        the client's ``_send_with_possible_retry`` just like a statement fetch.

        :return: list of dicts, one per HISAL segment:
            {iban, currency, balance, balance_date, line_of_credit,
             available_amount}
        """
        from fints.segments.saldo import HKSAL5, HKSAL6, HKSAL7

        conn = self.fints_connection
        iban = self.kefiya_login.account_iban

        def _extract(command_seg, response):
            rows = []
            for hisal in response.response_segments(command_seg, "HISAL"):
                balance = None
                # The booked balance carries the date it refers to. Without it
                # a running balance on the bookings has no anchor: "the balance
                # after this transaction" is only true if we know which
                # transactions the bank had already counted.
                balance_date = None
                try:
                    booked = hisal.balance_booked.as_mt940_Balance()
                    balance = booked.amount.amount
                    balance_date = getattr(booked, "date", None)
                except Exception:
                    balance = None
                rows.append({
                    "iban": iban,
                    "currency": getattr(hisal, "currency", None),
                    "balance": balance,
                    "balance_date": balance_date,
                    "line_of_credit": self._amount_from(
                        getattr(hisal, "line_of_credit", None)),
                    "available_amount": self._amount_from(
                        getattr(hisal, "available_amount", None)),
                })
            return rows

        with self.client_session():
            account = self._require_fints_account(iban)
            with conn._get_dialog() as dialog:
                hksal = conn._find_highest_supported_command(
                    HKSAL5, HKSAL6, HKSAL7)
                seg = hksal(
                    account=hksal._fields["account"].type.from_sepa_account(
                        account),
                    all_accounts=False,
                )
                return conn._send_with_possible_retry(dialog, seg, _extract)

    def get_fints_limits(self):
        """What the bank allows this account to transfer, and over what period.

        The limit sits on HIUPD, the account information the bank sends at
        logon -- once as a limit for the account as a whole, and again per
        business transaction (HKCCS, HKCCM, ...), which is the tighter and more
        relevant one for a transfer. python-fints parses both but drops them:
        get_information() never puts them in its result, so they are read off
        the segment here.

        FinTS Limitart: E per single order, T per day, W per week, M per month,
        Z per period of ``days`` days.

        :return: list of {"scope", "transaction", "limit_type", "amount",
            "currency", "days"} -- empty when the bank names no limit, which
            means unknown, NOT unlimited.
        """
        iban = (self.kefiya_login.account_iban or "").replace(" ", "").upper()

        def _amount(value):
            if value is None:
                return None, None
            return (self._amount_from(getattr(value, "amount", None)),
                    getattr(value, "currency", None))

        rows = []
        with self.client_session() as conn:
            upd = getattr(conn, "upd", None)
            if not upd or not getattr(upd, "segments", None):
                return rows

            for seg in upd.find_segments("HIUPD"):
                seg_iban = (getattr(seg, "iban", "") or "").replace(
                    " ", "").upper()
                # A login often carries several accounts; only this one's
                # limit may be used to decide what this login may send.
                if iban and seg_iban and seg_iban != iban:
                    continue

                account_limit = getattr(seg, "account_limit", None)
                if account_limit is not None and getattr(
                        account_limit, "limit_type", None):
                    amount, currency = _amount(
                        getattr(account_limit, "limit_amount", None))
                    rows.append({
                        "scope": "account",
                        "transaction": None,
                        "limit_type": str(account_limit.limit_type),
                        "amount": amount,
                        "currency": currency,
                        "days": getattr(account_limit, "limit_days", None),
                    })

                for allowed in (getattr(seg, "allowed_transactions", None)
                                or []):
                    if not getattr(allowed, "limit_type", None):
                        continue
                    amount, currency = _amount(
                        getattr(allowed, "limit_amount", None))
                    rows.append({
                        "scope": "transaction",
                        "transaction": str(
                            getattr(allowed, "transaction", "") or ""),
                        "limit_type": str(allowed.limit_type),
                        "amount": amount,
                        "currency": currency,
                        "days": getattr(allowed, "limit_days", None),
                    })

        return rows

    def get_fints_account_capabilities(self):
        """Which business transactions the bank allows, per account.

        The same HIUPD segment the limits are read from, asked the other way
        round: not "what is capped" but "what is offered at all". Two accounts
        of one customer differ routinely -- a savings account takes no
        transfer, a guarantee line takes nothing -- and the bank states that
        here, at logon, before anything is attempted.

        Deliberately NOT filtered to this login's own account: what the bank
        says holds for the account, and one login usually covers several. The
        caller matches them to their Bank Account records.

        python-fints exposes a narrower version of this through
        get_information(), but only as booleans for the operations the library
        itself implements -- which leaves out the dated transfer and the
        standing orders, the two this app added segments for. So it is read
        off the segment here.

        :return: list of {"iban", "account_number", "subaccount", "segments"};
            empty where the bank sent no HIUPD, which means unknown.
        """
        from kefiya.utils import account_capabilities

        with self.client_session() as conn:
            return account_capabilities.read_from_connection(conn)

    def get_fints_information(self):
        """Bank + account capabilities, limits and supported operations
        (FinTS get_information). Returns a nested dict."""
        with self.client_session():
            return self.fints_connection.get_information()

    def get_fints_scheduled_debits(self, multiple=False):
        """The bestand of scheduled SEPA direct debits (HKDBS).

        Money we collect once on a date -- NOT standing orders. Those are a
        different business transaction and have their own method below.

        The library's own get_scheduled_debits() filters the bank's answer by
        the name of the request instead of the answer, so it returns nothing
        at every bank. The single case is issued here with the right response
        type; the collective one (HKDMB) is left to the library, which gets
        that one right.
        """
        from kefiya.utils import standing_orders

        with self.client_session():
            account = self._require_fints_account()
            if multiple:
                return self.fints_connection.get_scheduled_debits(
                    account, multiple)
            return standing_orders.fetch_scheduled_debits(
                self.fints_connection, account)

    def get_fints_standing_orders(self):
        """The bestand of SEPA standing orders (HKCDB).

        Money we pay again and again on a cycle. The library ships no segment
        for this at all, so the query is defined in kefiya.utils.fints_segments
        and the answer read without a declared response segment.
        """
        from kefiya.utils import standing_orders

        with self.client_session():
            account = self._require_fints_account()
            return standing_orders.fetch_standing_orders(
                self.fints_connection, account)

    def get_fints_statements(self):
        """List available electronic account statements
        (elektronische Kontoauszuege / Dokumente)."""
        with self.client_session():
            account = self._require_fints_account()
            return self.fints_connection.get_statements(account)

    def get_fints_statement(self, number=None, year=None, file_format=None):
        """Fetch one specific electronic account statement document (HIEKA).

        May return binary content (e.g. PDF); the caller decides how to store it.
        """
        with self.client_session():
            account = self._require_fints_account()
            return self.fints_connection.get_statement(
                account, number, year, file_format)

    def get_fints_credit_card_transactions(self, credit_card_number=None,
                                           start_date=None, end_date=None):
        """Credit card transactions (Kreditkartenumsaetze) for the account."""
        with self.client_session():
            account = self._require_fints_account()
            return self.fints_connection.get_credit_card_transactions(
                account, credit_card_number, start_date, end_date)

    def get_fints_transactions_xml(self, start_date=None, end_date=None):
        """Fetch transactions as camt XML (richer than MT940: full structured
        remittance info / references). Returns the raw camt document streams."""
        allowed_days = cint(self.kefiya_login.allowed_sync_days_in_past) or 90
        if start_date is None:
            start_date = now_datetime().date() - relativedelta(days=allowed_days)
        if end_date is None:
            end_date = now_datetime().date() - relativedelta(days=1)
        with self.client_session():
            account = self._require_fints_account()
            return self.fints_connection.get_transactions_xml(
                account, start_date, end_date)

    def get_fints_status_protocol(self):
        """Bank status protocol messages (Statusprotokoll / diagnostics)."""
        with self.client_session():
            return self.fints_connection.get_status_protocol()

    def get_fints_communication_endpoints(self):
        """Available bank communication endpoints (diagnostics)."""
        with self.client_session():
            return self.fints_connection.get_communication_endpoints()

    def submit_sepa_debit(self, pain008_xml):
        """Collect a SEPA direct debit (Lastschrift, pain.008) via FinTS.

        Mirrors submit_sepa_transfer: requires a TAN, never pulls money without
        the user's TAN. The caller must supply a valid pain.008 message backed by
        a SEPA mandate. INTENTIONALLY NOT whitelisted -- money collection must be
        driven by a guarded flow (explicit confirmation + write permission +
        mandate check), analogous to submit_payment_request_via_fints. VoP
        (Verification of Payee) mismatches must be resolved by a human, never
        auto-approved.
        """
        self._refuse_inside_fetch_session()

        with self.fints_connection:
            account = self._require_fints_account()
            response = self.fints_connection.sepa_debit(account, pain008_xml)
            response, pending = self._settle_tan(response)
            if pending is not None:
                return pending

            # A refusal is a refusal, whatever the order was. This path
            # logged that the bank had said something and reported success.
            #
            # NOT the unsigned guard: it works out which capability applies
            # from a transfer's shape (single or collective, dated, instant),
            # and a direct debit is none of those. Asking it here would ask
            # the wrong question of the stored capabilities, and a wrong
            # refusal on a debit run is its own kind of damage. Worth having
            # its own guard; it does not have one yet, and pretending
            # otherwise would be worse than saying so.
            self._refuse_a_refused_order(response)
            return {"status": "submitted"}

    def _vop_answer(self, response, pain_xml=None):
        """What the bank said about the payee, in the four fields that matter.

        Or None where this is not a Verification-of-Payee response at all --
        python-fints releases without VoP support have no NeedVOPResponse, so
        the transfer then proceeds exactly as it did before.

        This used to hand back _to_jsonable(hivpp), which for a parsed segment
        means json.dumps(..., default=str) -- the repr, as a string. It was
        stored that way, read back that way, and rendered to the person
        deciding whether to release the money::

            "fints.segments.auth.HIVPP1(header=..., polling_id=b'587d...')"

        Nobody can compare an invoice against that. The segment itself belongs
        in the Error Log, where it still is.
        """
        try:
            from fints.client import NeedVOPResponse
        except Exception:
            return None
        if not isinstance(response, NeedVOPResponse):
            return None

        # Read out of the message that was actually sent, and only here: an
        # ordinary transfer never parses its own pain.001 again.
        payee = pain_payee.the_only_payee(pain_xml)
        name, iban = payee if payee else ("", "")
        vop_result = getattr(response, "vop_result", None)
        return {
            "result": vop_rule.result_from(vop_result),
            "bank_name": vop_rule.bank_name_from(vop_result),
            "payee_name": name,
            "iban": iban,
        }

    def _may_confirm_without_asking(self, answer):
        """May this payee check be confirmed without a person looking?

        Two cases, and only two: the bank confirmed the match, or a person
        already approved this exact payee at this exact IBAN. The rule is in
        vop_rule and says why.

        A collective order always answers False: the bank reports a batch in a
        payment status report rather than in the single result vop_rule reads,
        so there is no per-payment answer to go on. Somebody looks at it --
        which is where a batch stood before this existed.

        Only decides. The sending is the caller's, so the decision stays
        separable from the bank call it leads to.
        """
        result = answer.get("result") or ""
        remembered = bool(answer.get("iban") and answer.get("payee_name")) \
            and accepted_payee.was_accepted(
                answer["iban"], answer["payee_name"])
        if vop_rule.needs_a_human(result, remembered):
            return False

        # Said out loud, because this is money leaving without anybody being
        # asked: the reason has to be in the log next to the order.
        frappe.logger("kefiya").info(
            "VoP confirmed without asking: login=%s result=%s remembered=%s",
            self.kefiya_login.name, result or "?", remembered,
        )
        return True

    def _confirm_or_park_vop(self, response, pain_xml, payment_reference):
        """What happens when the bank wants the payee check confirmed.

        Answers a pair: the response to carry on with, and the result to hand
        back to the caller instead. Exactly one of them is set.

            (response, None)   confirmed -- the order takes the ordinary path
                               from here, to its TAN or to a refusal
            (None, result)     parked for a reviewer, and nothing was sent

        Its own method so submit_sepa_transfer keeps one line for a decision
        that has three inputs and two outcomes.
        """
        answer = self._vop_answer(response, pain_xml)
        if answer is None:
            return response, None

        if self._may_confirm_without_asking(answer):
            return self.fints_connection.approve_vop_response(response), None

        # A payee the bank could not confirm is exactly the signal a
        # diverted-invoice fraud produces, so money does not move until
        # somebody has compared it against the document, deliberately, via
        # approve_pending_vop().
        self._persist_vop_state(response, answer, payment_reference)
        return None, {
            "status": "vop_mismatch",
            "docname": self.kefiya_login.name,
            "vop_result": answer,
        }

    def _persist_vop_state(self, response, answer, payment_reference=None):
        """Park a Verification-of-Payee challenge for later human release.

        NeedVOPResponse is a NeedRetryResponse, so it survives a round trip
        through the database exactly like a pending TAN: store the challenge
        plus the paused dialog, and the reviewer can resume and approve it in
        a later request.

        ``answer`` is what _vop_answer built and is stored as it stands --
        result, the name the bank holds, and who the order pays. Everything
        downstream reads that one record: the dialog that shows the reviewer
        the decision, and approve_pending_vop, which files it against the
        right payee once they have made it.
        """
        self.kefiya_login.stored_vop_blob = response.get_data()
        self.kefiya_login.stored_vop_dialog_blob = \
            self.fints_connection.pause_dialog()
        self.kefiya_login.vop_reference = payment_reference
        self.kefiya_login.vop_result = json.dumps(answer)[:2000]
        self.kefiya_login.save()

    def approve_pending_vop(self, instant_payment=False):
        """Release a parked VoP mismatch after a human reviewed the payee.

        This is the deliberate counterpart to refusing the transfer in
        submit_sepa_transfer: the bank could not confirm that payee name and
        IBAN belong together, a reviewer checked it against the invoice, and
        only now is the order approved. Callers must gate this on an explicit
        confirmation -- it is the step that lets the money leave.

        :return: {"status": "submitted" | "tan_required" | "error", ...}
        """
        blob = self.kefiya_login.stored_vop_blob
        dialog_blob = self.kefiya_login.stored_vop_dialog_blob
        if not (blob and dialog_blob):
            return {
                "status": "error",
                "message": _("No pending Verification of Payee for this login."),
            }

        self._refuse_inside_fetch_session()

        # Read before the state is cleared: this is the payee the reviewer is
        # about to approve, and clear_vop_state drops it.
        parked = vop_rule.parked_answer(self.kefiya_login.vop_result)

        with self.fints_connection.resume_dialog(dialog_blob):
            challenge = NeedRetryResponse.from_data(blob)
            response = self.fints_connection.approve_vop_response(challenge)

            # The challenge is spent either way -- approved orders must never be
            # replayable, and a failure needs a fresh transfer, not a retry of a
            # stale dialog.
            self.kefiya_login.clear_vop_state()
            self.kefiya_login.save()

            # The reviewer has decided. Written down so the next order to the
            # same payee at the same IBAN goes through without asking them the
            # same question again -- which is all this remembers: their answer,
            # for that pair, and nothing wider.
            if parked.get("iban") and parked.get("payee_name"):
                accepted_payee.remember(
                    parked["iban"], parked["payee_name"],
                    vop_result=parked.get("result"),
                    bank_name=parked.get("bank_name"),
                    kefiya_login=self.kefiya_login.name)

            response, pending = self._settle_tan(response)
            if pending is not None:
                return pending

            # The same two questions the ordinary send asks. This path had
            # neither: it logged "the bank did not request a TAN" and reported
            # success -- for the release of an order the bank had just been
            # asked to reconsider, which is the last place to guess.
            verdict = self._refuse_a_refused_order(response)
            self._refuse_unsigned(False, False, bool(cint(instant_payment)),
                                  verdict=verdict)
            return {"status": "submitted"}

    def submit_sepa_transfer(self, pain_xml, instant_payment=False,
                             payment_reference=None, multiple=False,
                             control_sum=None, scheduled=False):
        """Submit a SEPA credit transfer (pain.001 XML) via FinTS.

        Requires a TAN: if the bank asks for one, the request is persisted and
        the UI is prompted (the user later calls send_transfer_tan). Never sends
        money without the user's TAN.

        :param pain_xml: pain.001 credit-transfer message
        :param instant_payment: if truthy, send as SEPA Instant / real-time
            credit transfer (Echtzeitueberweisung, FinTS HKIPZ) instead of a
            regular transfer (HKCCS). The debtor bank + account must support
            instant payments, otherwise the bank rejects the order.
        :param multiple: send as a collective order (HKCCM) -- one order
            carrying many payments, authorised by a single TAN. Without this a
            payment run needs one TAN per payment.
        :param control_sum: total of all payments, which the bank checks a
            collective order against. Required by the standard whenever
            ``multiple`` is set.
        :param scheduled: hand the order to the BANK as a dated transfer
            (HKCSE / HKCME) instead of sending it now. The date comes from the
            pain message's ReqdExctnDt. Use this for "let the bank manage the
            due date"; when we manage it ourselves the payment simply waits in
            the outbox and is sent on the day as a normal transfer.
        :return: {"status": "submitted" | "tan_required", ...}; a scheduled
            order additionally carries the bank's order identifier as
            ``task_id`` where the bank supplies one.
        """
        instant_payment = bool(cint(instant_payment))
        multiple = bool(cint(multiple))
        scheduled = bool(cint(scheduled))
        if scheduled and instant_payment:
            # A real-time transfer is executed within seconds; a date on it is
            # a contradiction, and no bank offers the combination.
            frappe.throw(_(
                "An instant payment cannot be scheduled for a later date."
            ))
        if multiple and control_sum is None:
            frappe.throw(_(
                "A collective transfer requires a control sum."
            ))

        kwargs = {"instant_payment": instant_payment}
        if multiple:
            # Only pass these for a collective order: banks reject a control
            # sum on a single transfer.
            kwargs["multiple"] = True
            kwargs["control_sum"] = control_sum

        self._refuse_inside_fetch_session()

        with self.fints_connection:
            account = self._require_fints_account()
            try:
                if scheduled:
                    response = self._send_scheduled_transfer(
                        account, pain_xml, multiple=multiple,
                        control_sum=control_sum)
                else:
                    response = self.fints_connection.sepa_transfer(
                        account, pain_xml, **kwargs)
            except Exception as exc:
                # A stored state whose dialog cannot be built. The next attempt
                # would restore the same one and fail identically, so it is
                # dropped here -- the following attempt starts from a fresh
                # handshake.
                #
                # Dropped, not retried. The segments were already on the wire
                # when this surfaced, so whether the bank saw the payment is
                # exactly what nobody knows -- and a second send is the one
                # mistake that costs real money. The user is told to look
                # before they send again, the same way the unsigned-order guard
                # tells them.
                if not _is_missing_bank_parameters(exc):
                    raise
                self._forget_client_state()
                frappe.throw(_(
                    "The bank connection for {0} could not be rebuilt from its"
                    " stored state, so this order was NOT sent as far as this"
                    " app can tell. The stored state has been discarded and the"
                    " next attempt will start a fresh connection."
                    "\n\nBefore sending again, look in your online banking:"
                    " if the transfer is there after all, cancel this order"
                    " instead of repeating it."
                ).format(self.kefiya_login.name),
                    title=_("Connection could not be rebuilt"))
            response, parked = self._confirm_or_park_vop(
                response, pain_xml, payment_reference)
            if parked is not None:
                return parked

            response, pending = self._settle_tan(response)
            if pending is not None:
                return pending

            # What the bank itself said. Asked BEFORE concluding anything from
            # the absence of a TAN, because a refusal explains that absence:
            # an order the bank turned down is not an order it wants signed.
            #
            # This was the missing question. The old code asked only "VoP?"
            # and "TAN?" and read everything else as success, so a bank that
            # refused an order in plain words produced "the bank did not
            # request a TAN" in the log and "Unbekannter Fehler" on screen.
            verdict = self._refuse_a_refused_order(response)

            # The dialog ended without a TAN and without a refusal. That is
            # either a bank that asks for no signature, or an order that was
            # never signed.
            self._refuse_unsigned(multiple, scheduled, instant_payment,
                                  verdict=verdict)

            # Nothing left to object to: the bank asks for no signature on this
            # transaction, so an unsigned dialog is what success looks like.
            # Still recorded, because a credit transfer without strong
            # authentication is unusual enough to be worth a trail -- and with
            # what the bank said, so the trail is readable.
            frappe.log_error(
                title="Kefiya SEPA transfer completed without TAN challenge",
                message="login={0}: the bank did not request a TAN\n{1}".format(
                    self.kefiya_login.name,
                    fints_response.as_text(verdict) or "(the bank said nothing)"
                ),
            )
            result = {"status": "submitted"}
            if scheduled:
                # The identifier under which the bank now holds the dated
                # order. Without it the order can never be changed or called
                # back through us again, only in the banking app.
                result["task_id"] = (
                    getattr(response, "data", None) or {}).get("task_id")
            return result

    def _refuse_unsigned(self, multiple, scheduled, instant_payment,
                         verdict=None):
        # (see _advice_block below for how the bank's codes are explained)
        """Refuse to call an unsigned order sent, when the bank demands a
        signature for it.

        What this is for, in one case. An order of 70,40 EUR went out, the
        bank asked for no TAN, and the app wrote "Sent". In the online banking
        the transfer did not exist -- not sent, not received. HIUPD had said
        all along what the account requires:

            HKCCS Ueberweisung             erlaubt, required_signatures 1
            HKIPZ Echtzeitueberweisung     erlaubt, required_signatures 1

        One signature. None was given. An order that needs a signature and has
        none is not executed, so "the dialog ended without a TAN" cannot mean
        the same thing for this account as for one that requires none -- and
        the old code could not tell the two apart, because it never asked.

        It asks now, from data already stored at logon, and refuses rather
        than guesses. Refusing costs a repeated send; guessing costs an
        invoice that everyone believes is paid.
        """
        from kefiya.utils import account_capabilities as capabilities

        bank_account = getattr(self.kefiya_login, "bank_account", None)
        capability = capabilities.required_capability(
            payment_count=2 if multiple else 1,
            scheduled=bool(scheduled), instant=bool(instant_payment))
        needed = capabilities.required_signatures(bank_account, capability)
        if not needed:
            # None required, or nothing stored to go on. Unknown is not a
            # reason to refuse a send that may well have worked -- it is a
            # reason to keep the loud log below.
            return

        said = fints_response.as_text(verdict)
        frappe.log_error(
            title="Kefiya: transfer ended without the signature the bank"
                  " requires",
            message="login={0} capability={1} required_signatures={2}\n{3}"
                    .format(self.kefiya_login.name, capability, needed,
                            said or "(the bank said nothing)"),
        )
        frappe.throw(
            _(
                "The bank asked for no TAN, but it requires {0} signature(s)"
                " for \"{1}\" on this account. An order without the signature"
                " it needs is not executed, so it has NOT been marked as sent."
                "\n\nPlease check the online banking before sending again: if"
                " the transfer is there after all, cancel this order instead"
                " of repeating it."
            ).format(
                needed,
                _(capabilities.LABEL_BY_KEY.get(capability, capability)),
            ) + (("\n\n" + _("What the bank said:") + "\n" + said)
                 if said else "")
              + _advice_block(verdict),
            title=_("Not sent — no signature"),
        )

    def _send_scheduled_transfer(self, account, pain_xml, multiple=False,
                                 control_sum=None, currency="EUR"):
        """Hand a dated transfer to the bank (HKCSE / HKCME).

        python-fints stops at the immediate transfer, so this mirrors its
        sepa_transfer internals with the segments defined in fints_segments --
        the same approach get_fints_balance takes for HKSAL. TAN and
        Verification of Payee are handled by the library's own send path, so a
        dated order is authorised exactly like an immediate one.

        The execution date is not passed here: it lives inside the pain.001 as
        ReqdExctnDt, which is what makes the message a dated order at all.

        Raises FinTSUnsupportedOperation when the account does not offer dated
        transfers -- the caller turns that into "your bank will not hold this
        one, so we will".
        """
        from kefiya.utils.fints_segments import HKCME1, HKCSE1, read_task_id

        conn = self.fints_connection
        pain_descriptor = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"

        with conn._get_dialog() as dialog:
            command_class = HKCME1 if multiple else HKCSE1
            # Deliberately without return_parameter_segment: reading
            # parameter.sum_amount_required would need a typed HICMES class we
            # do not define. The standard requires the control sum on every
            # collective order anyway, and the caller already refuses to build
            # one without it.
            command = conn._find_highest_supported_command(command_class)

            seg = command(
                account=command._fields["account"].type.from_sepa_account(
                    account),
                sepa_descriptor=pain_descriptor,
                sepa_pain_message=pain_xml.encode()
                if isinstance(pain_xml, str) else pain_xml,
            )
            if multiple:
                seg.sum_amount.amount = control_sum
                seg.sum_amount.currency = currency
                # request_single_booking is left at its default, exactly as the
                # immediate collective order does it. Asking for it without
                # being able to read single_booking_allowed from the bank's
                # parameters is how a whole payment run gets rejected.

            def _resume(command_seg, response):
                # The library's own status mapping, so a dated order reports
                # success and failure like an immediate one; only the order
                # identifier is added on top.
                result = conn._continue_sepa_transfer(command_seg, response)
                task_id = read_task_id(response)
                if task_id:
                    result.data["task_id"] = task_id
                return result

            return conn._send_pay_with_possible_retry(dialog, seg, _resume)

    def import_fints_transactions(self, kefiya_import):
        """Create payment entries by FinTS transactions.

        :param kefiya_import: kefiya_import doc name
        :type kefiya_import: str
        :return: List of max 10 transactions and all new payment entries
        """
        try:
            self.interactive.show_progress_realtime(
                _("Start transaction import"), 40, reload=False
            )
            curr_doc = frappe.get_doc("Kefiya Import", kefiya_import)
            new_bank_transactions = None

            # Incremental fetch: when no start date was entered, begin at the
            # date of the most recently imported transaction for this account
            # (clamped to the FinTS 90-day window) and fetch up to today.
            if not curr_doc.from_date:
                curr_doc.from_date = resolve_incremental_from_date(
                    self.kefiya_login.bank_account,
                    self.kefiya_login.allowed_sync_days_in_past,
                )
                curr_doc.to_date = now_datetime().date()

            tansactions = self.get_fints_transactions(
                curr_doc.from_date,
                curr_doc.to_date
            )

            # normalise: some banks (e.g. Atruvia/VR) return a list of
            # statements (list of lists); others (Finanz Informatik) return a
            # flat list of transaction dicts. Flatten one nesting level so the
            # downstream code (start/end date, old_kefiya_import) always sees a
            # flat list of transactions.
            flat_transactions = []
            for statement in tansactions:
                if isinstance(statement, list):
                    flat_transactions.extend(statement)
                else:
                    flat_transactions.append(statement)
            tansactions = flat_transactions
            
            if(len(tansactions) == 0):
                frappe.msgprint(_("No transaction found"))
            else:
                try:
                    save_file(
                        kefiya_import + ".json",
                        json.dumps(
                            tansactions, ensure_ascii=False
                        ).replace(",", ",\n").encode('utf8'),
                        'Kefiya Import',
                        kefiya_import,
                        folder='Home/Attachments/FinTS',
                        decode=False,
                        is_private=1,
                        df=None
                    )
                except Exception as e:
                    frappe.throw(_("Failed to attach file"), e)

                curr_doc.start_date = tansactions[0]["date"]
                curr_doc.end_date = tansactions[-1]["date"]

                importer = ImportBankTransaction(self.kefiya_login, self.interactive)
                # Banks differ in transaction format: Atruvia/VR deliver CAMT
                # (ISO 20022, recognisable by the CreditDebitIndicator field),
                # Finanz Informatik (Sparkasse, LBBW) deliver MT940. Route each
                # set to the matching parser.
                if tansactions and isinstance(tansactions[0], dict) and "CreditDebitIndicator" in tansactions[0]:
                    importer.kefiya_import(tansactions)
                else:
                    importer.old_kefiya_import(tansactions)

                if len(importer.bank_transactions) == 0:
                    frappe.msgprint(_("No new payments found"))
                else:
                    # Save payment entries
                    frappe.db.commit()

                    frappe.msgprint(_(
                        "Found a total of '{0}' payments"
                    ).format(
                        len(importer.bank_transactions)
                    ))
                new_bank_transactions = importer.bank_transactions

            curr_doc.submit()
            self.interactive.show_progress_realtime(
                _("Bank Transaction import completed"), 100, reload=False
            )

            # Make the import durable before reconciliation runs, so a rollback
            # inside reconciliation can never revert the submitted import.
            frappe.db.commit()

            # Optional automatic reconciliation of the freshly imported window
            # (guarded so it can never break the import).
            try:
                run_after_import(
                    self.kefiya_login.name, curr_doc.from_date, curr_doc.to_date
                )
            except Exception:
                frappe.log_error(
                    title="Kefiya auto-reconcile entrypoint failed",
                    message=frappe.get_traceback(),
                )

            # auto_assignment = AssignmentController().auto_assign_payments()
            return {
                "transactions": tansactions,
                "payments": new_bank_transactions,
                # "assignment": auto_assignment
            }
        except TanInteractionRequired:
            # Not a parsing error: the bank wants the session authenticated
            # first. Wrapping it in a ValidationError hid that from every
            # caller -- fetch_all's "tan_required" branch never matched, so the
            # request failed and Frappe rolled back the challenge that had just
            # been parked on the login. The user then confirmed a release in
            # the banking app that no longer had anything to release. Let it
            # through unchanged.
            raise
        except Exception as e:
            frappe.throw(_(
                "Error parsing transactions<br>{0}"
            ).format(str(e)), frappe.get_traceback())




def _to_jsonable(value):
    """Best-effort convert a FinTS-lib result into JSON-serialisable data.

    FinTS objects (accounts, statements, standing orders, ...) vary per bank and
    library version and are not always JSON-serialisable; unknown objects are
    stringified so the caller can at least inspect the payload.
    """
    return json.loads(json.dumps(value, default=str))


def _require_login_read(kefiya_login):
    """Ensure the caller may read this Kefiya Login before any bank data is
    fetched. These endpoints expose sensitive account data (balances,
    statements, ...), so a logged-in user must never read a login they cannot
    access. Raises frappe.PermissionError otherwise."""
    frappe.has_permission(
        "Kefiya Login", ptype="read", doc=kefiya_login, throw=True)


@frappe.whitelist()
def get_account_balance(kefiya_login):
    """Fetch the current balance, credit line (Kreditlinie) and available amount
    for a Kefiya Login's account via FinTS (HKSAL).

    Returns a list of dicts (iban, currency, balance, line_of_credit,
    available_amount); persisting/displaying the values is left to the caller.
    Like every FinTS call this may trigger a TAN interaction, which the
    controller handles the same way as a statement fetch.
    """
    _require_login_read(kefiya_login)
    return FinTSController(kefiya_login).get_fints_balance()


@frappe.whitelist()
def get_bank_information(kefiya_login):
    """FinTS get_information: bank name, supported operations, accounts, limits."""
    _require_login_read(kefiya_login)
    return _to_jsonable(FinTSController(kefiya_login).get_fints_information())


@frappe.whitelist()
def get_scheduled_debits(kefiya_login):
    """Standing orders / scheduled debits (Dauerauftraege / Termin-Ueberweisungen)."""
    _require_login_read(kefiya_login)
    return _to_jsonable(
        FinTSController(kefiya_login).get_fints_scheduled_debits())


@frappe.whitelist()
def get_statements(kefiya_login):
    """List of available electronic account statements (Kontoauszuege / Dokumente)."""
    _require_login_read(kefiya_login)
    return _to_jsonable(FinTSController(kefiya_login).get_fints_statements())


@frappe.whitelist()
def get_statement(kefiya_login, number=None, year=None, file_format=None):
    """Fetch one electronic account statement document (HIEKA). May be binary."""
    _require_login_read(kefiya_login)
    return _to_jsonable(
        FinTSController(kefiya_login).get_fints_statement(
            number, year, file_format))


@frappe.whitelist()
def get_credit_card_transactions(kefiya_login, credit_card_number=None):
    """Credit card transactions (Kreditkartenumsaetze)."""
    _require_login_read(kefiya_login)
    return _to_jsonable(
        FinTSController(kefiya_login).get_fints_credit_card_transactions(
            credit_card_number))


@frappe.whitelist()
def get_transactions_camt_xml(kefiya_login, start_date=None, end_date=None):
    """Transactions as camt XML (richer than MT940)."""
    _require_login_read(kefiya_login)
    return _to_jsonable(
        FinTSController(kefiya_login).get_fints_transactions_xml(
            start_date, end_date))


@frappe.whitelist()
def get_status_protocol(kefiya_login):
    """Bank status protocol messages (diagnostics)."""
    _require_login_read(kefiya_login)
    return _to_jsonable(
        FinTSController(kefiya_login).get_fints_status_protocol())


@frappe.whitelist()
def get_communication_endpoints(kefiya_login):
    """Available bank communication endpoints (diagnostics)."""
    _require_login_read(kefiya_login)
    return _to_jsonable(
        FinTSController(kefiya_login).get_fints_communication_endpoints())
