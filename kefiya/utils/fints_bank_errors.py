# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What the bank said when it refused, in words a user can act on.

Came from the original (phamos-eu/kefiya): clear messages for a wrong PIN or
a locked access, the bank's own 9xxx reasons instead of python-fints'
"Bad status code 400", and the bank traffic in the Error Log.

Its own module because both controllers use it. It stood in
fints_controller.py, and the legacy controller imported it from there -- a
controller importing the other controller for its error messages. Like
fints_errors.py, it imports neither controller, so either can import it.
fints_controller passes the names on, so existing imports keep working.
"""

import contextlib
import json
import logging
import threading

import frappe
import mt940
from fints.client import NeedTANResponse
from fints.exceptions import (
    FinTSClientPINError,
    FinTSClientTemporaryAuthError,
    FinTSSCARequiredError,
)
from frappe import _

from kefiya.utils import fints_response
from kefiya.utils.fints_errors import TanInteractionRequired


class FetchFailed(frappe.ValidationError):
    """The transaction fetch failed with a message that is already clear for the user."""


class FetchTanRequired(FetchFailed):
    """The bank asked for a TAN on the transaction fetch, which isn't supported yet."""


class FetchRefused(FetchFailed):
    """The bank refused the transaction fetch with a 9xxx response."""


class LoginRejected(frappe.ValidationError):
    """The bank did not accept the login name and PIN, or has locked the access."""


# fints raises these when the bank rejects the login; it then also blocks the
# PIN for the rest of the request
LOGIN_ERRORS = (FinTSClientPINError, FinTSClientTemporaryAuthError)


def throw_login_rejected(kefiya_login_name, error, error_log=None):
    """Explain a wrong PIN or a locked access instead of showing fints' exception.

    :param kefiya_login_name: Name of the Kefiya Login that was used
    :param error: The FinTSClientPINError or FinTSClientTemporaryAuthError
    :param error_log: What log_bank_traffic_on_error yielded, if anything
    """
    if isinstance(error, FinTSClientTemporaryAuthError):
        intro = _(
            "Your bank has temporarily locked the online banking access of Kefiya"
            " Login {0}, usually after several wrong PINs. Unlock it in your bank's"
            " online banking or ask your bank, then try again."
        ).format(frappe.bold(kefiya_login_name))
    else:
        intro = _(
            "Your bank did not accept the login of Kefiya Login {0}. Check the"
            " FinTS login name and the PIN, then try again. Careful: after several"
            " wrong PINs in a row the bank locks the online banking access."
        ).format(frappe.bold(kefiya_login_name))

    if error_log and error_log.bank_errors:
        message = bank_refusal_message(intro + "<br><br>" + _("The bank's answer:"), error_log)
    else:
        message = intro
        if error_log and error_log.name:
            message += error_log_link(error_log.name)

    frappe.throw(message, exc=LoginRejected, title=_("Login Failed"))


@contextlib.contextmanager
def explain_login_rejection(kefiya_login_name):
    """Turn a rejected login anywhere in the block into a clear message."""
    try:
        yield
    except LOGIN_ERRORS as e:
        throw_login_rejected(kefiya_login_name, e)


def throw_fetch_tan_required():
    """Stop the import because the bank wants a TAN for the fetch itself.

    fints signals this either by returning a NeedTANResponse or, on bank
    code 9075, by raising FinTSSCARequiredError. It happens mostly for
    history older than 90 days (PSD2). Asking for the TAN is phamos/kefiya#45.
    """
    frappe.throw(
        _(
            "Your bank requires a TAN to fetch transactions from this date"
            " range. Choose a From Date within the last 90 days."
        ),
        exc=FetchTanRequired,
        title=_("TAN Required"),
    )

class _BankTrafficRecorder(logging.Handler):
    """Keep the messages fints logs while talking to the bank (fints masks the PIN),
    and the bank's 9xxx error responses on their own.

    Only records from the thread that created it: a threaded web worker can
    talk to other banks for other users at the same time.
    """

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.thread = threading.get_ident()
        self.messages = []
        self.bank_errors = []

    def emit(self, record):
        if record.thread != self.thread:
            return
        self.messages.append(record.getMessage())
        code = getattr(record, "fints_response_code", None)
        if code and code.startswith("9"):
            error = (code, getattr(record, "fints_response_text", ""))
            if error not in self.bank_errors:
                self.bank_errors.append(error)


_fints_debug = {"lock": threading.Lock(), "users": 0, "previous_level": logging.NOTSET}


@contextlib.contextmanager
def _fints_debug_logging():
    """Let fints log at DEBUG while at least one recorder listens.

    Counted, so overlapping requests in one process don't restore the level
    while another still records, or leave it at DEBUG for good.
    """
    fints_logger = logging.getLogger("fints")
    with _fints_debug["lock"]:
        if _fints_debug["users"] == 0:
            _fints_debug["previous_level"] = fints_logger.level
            fints_logger.setLevel(logging.DEBUG)
        _fints_debug["users"] += 1
    try:
        yield fints_logger
    finally:
        with _fints_debug["lock"]:
            _fints_debug["users"] -= 1
            if _fints_debug["users"] == 0:
                fints_logger.setLevel(_fints_debug["previous_level"])


@contextlib.contextmanager
def log_bank_traffic_on_error(client, title):
    """On an error, write the bank traffic and any HTTP error answer to the Error Log.

    fints only raises e.g. "Bad status code 400". Finding out why needs what
    was sent and what the bank's server answered, so record both.

    Yields a dict whose "name" is set to the Error Log once one was written,
    and whose "bank_errors" holds the bank's 9xxx responses as (code, text).
    """
    error_log = frappe._dict()
    recorder = _BankTrafficRecorder()
    http_errors = []

    def keep_http_error(response, *args, **kwargs):
        if not response.ok:
            http_errors.append("HTTP {0} from {1}:\n{2}".format(
                response.status_code, response.url, response.text[:5000]
            ))

    response_hooks = client.connection.session.hooks["response"]
    response_hooks.append(keep_http_error)
    try:
        with _fints_debug_logging() as fints_logger:
            fints_logger.addHandler(recorder)
            try:
                yield error_log
            finally:
                fints_logger.removeHandler(recorder)
    except (TanInteractionRequired, FetchFailed):
        raise
    except Exception:
        error_log.bank_errors = recorder.bank_errors
        # Error Log is a MyISAM table, so the entry stays even when Frappe
        # rolls back the request after frappe.throw
        error_log.name = frappe.log_error(
            title=title,
            message="\n\n".join([frappe.get_traceback(), *http_errors, *recorder.messages]),
        ).name
        raise
    finally:
        response_hooks.remove(keep_http_error)


def bank_refusal_message(intro, error_log, extra_hints=None):
    """The bank's 9xxx reasons, what the user can do about them and a link to the Error Log.

    :param intro: Translated sentence the reasons follow
    :param error_log: What log_bank_traffic_on_error yielded
    :param extra_hints: Hints for codes that depend on the request, by code.
        The general ones come from fints_response.KNOWN_CODES -- the one
        table of codes this app explains, shared with the transfer path.
    """
    message = intro + "<br>" + "<br>".join(
        "{0}: {1}".format(code, frappe.utils.escape_html(text))
        for code, text in error_log.bank_errors
    )
    verdict = {"lines": [{"code": code} for code, _text in error_log.bank_errors]}
    for hint in fints_response.advice(verdict, extra_hints):
        message += "<br><br>" + _(hint)
    if error_log.name:
        message += error_log_link(error_log.name)
    return message


def error_log_link(error_log_name):
    # relative link: get_link_to_form uses the site's host_name, which
    # can differ from the address the user opened the site with
    return "<br><br>" + _("The bank traffic was saved in the {0}.").format(
        '<a href="/app/error-log/{0}">{1}</a>'.format(error_log_name, _("Error Log"))
    )


def fetch_fints_transactions(client, account, start_date, end_date):
    """Fetch transactions and turn the bank's refusals into clear messages.

    Used by both controllers.

    :return: Transactions as json object list
    """
    error_log = frappe._dict()
    try:
        with log_bank_traffic_on_error(client, _("Kefiya: Could not fetch transactions")) as error_log, client:
            try:
                transactions = client.get_transactions(account, start_date, end_date)
            except FinTSSCARequiredError:
                throw_fetch_tan_required()
            if isinstance(transactions, NeedTANResponse):
                throw_fetch_tan_required()
            return json.loads(
                json.dumps(transactions, cls=mt940.JSONEncoder)
            )
    except FetchFailed:
        raise
    except Exception:
        if not error_log.bank_errors:
            raise
        # fints reports any 9010 as "could not fetch BPD ... bank identifier",
        # also when the login is fine and only this fetch is refused
        frappe.throw(
            bank_refusal_message(
                _("Your bank refused to send the transactions:"),
                error_log,
                {"9010": _(
                    "If the From Date is more than 90 days ago, choose a later one."
                )},
            ),
            exc=FetchRefused,
            title=_("Bank Refused Request"),
        )
