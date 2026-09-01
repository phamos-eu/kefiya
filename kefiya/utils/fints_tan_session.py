# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der Weg einer TAN, von der Frage der Bank bis zur Antwort des Nutzers.

Neun Methoden, ein Gegenstand: die Anforderung, die eine Bank offen haelt,
waehrend jemand ein Telefon entsperrt. Sie lagen ueber neunhundert Zeilen
verteilt in fints_controller.py, zwischen Kontoabruf, Wertpapieren und
Lastschriften, und die Reihenfolge, in der sie aufgerufen werden muessen --
erst festhalten, dann fragen -- war nirgends zu sehen. Genau diese
Reihenfolge hat einen Auftrag gekostet.

Hier stehen sie in der Reihenfolge des Lebenszyklus:

    _settle_tan                 was mit einer verlangten TAN geschieht
    ask_for_tan                 festhalten und fragen, in dieser Reihenfolge
    _park_tan_challenge         festhalten -- und melden, ob es geklappt hat
    _publish_tan_prompt         fragen; ruehrt keinen Dialog an
    _cannot_note_the_challenge  die eine Antwort auf ein misslungenes
                                Festhalten, fuer alle drei Aufrufer
    _await_release              auf eine Freigabe warten (nur beim Abruf)
    _decoupled_parameters       was die Bank ueber das Warten sagt
    _tell_the_browser…          das Freigabefenster schliessen
    _resume_and_answer…         die geparkte Anforderung beantworten

Als Mixin, nicht als Mitspieler: Die neun greifen auf kefiya_login,
fints_connection und interactive zu, und jede davon durch ein
Rueckwaerts-Objekt zu fuehren waere mehr Rauschen als Klarheit. Was der
Umzug bringt, ist die Kohaesion und die Datei -- kein Aufrufer aendert sich,
keine Zeile Verhalten.
"""

import time

import frappe
from frappe import _

from fints.client import NeedTANResponse, NeedRetryResponse

from kefiya.utils import fints_vop
from kefiya.utils import fints_vop_client
from kefiya.utils import tan_challenge
from kefiya.utils.decoupled_budget import decoupled_wait, job_budget_seconds
from kefiya.utils.fints_errors import (
    InitFailedException,
    TanInteractionRequired,
)


class TanSession:
    """Die TAN-Strecke eines FinTSController."""

    #: Wie lange ein Lauf auf eine in der App gegebene Freigabe wartet. Die
    #: Bank nennt ihre eigenen Grenzen in HITANS und die gelten, wo sie
    #: lesbar sind -- das hier ist die Obergrenze, nicht der Fahrplan.
    DECOUPLED_MAX_WAIT_SECONDS = 120
    DECOUPLED_POLL_SECONDS = 2

    def _settle_tan(self, response):
        """What to do about a TAN the bank asked for.

        Answers a pair, exactly one of them set -- the same shape
        _confirm_or_park_vop uses two hundred lines below, for the same kind
        of question:

            (response, None)   carry on: no TAN was wanted, or a decoupled
                               release arrived while we waited
            (None, result)     hand this back to the caller: the challenge is
                               parked and somebody has to answer it

        A decoupled release is given in the banking app. This waited for one
        only on the statement-fetch path; a transfer that met a decoupled
        procedure -- which is what the Sparkasse uses -- parked its challenge
        and answered "tan_required" straight away, and nothing polled
        afterwards. When the banking app then reported an error there was no
        request in flight to report it to, and the user saw nothing at all.
        """
        if not isinstance(response, NeedTANResponse):
            return response, None

        if bool(getattr(response, "decoupled", False)):
            # Park it, then ask. NOT poll-then-park, which is what this did
            # and what cost the user the order.
            #
            # Measured, on KEF-TRF-2026-00007: the order reached the
            # Sparkasse, the bank asked for the release in the app, and the
            # first status query came back 9010 -- which python-fints reports
            # as "could not fetch BPD", a sentence about something else
            # entirely. The attempt to park afterwards then went down with
            # the broken dialog, the request failed, and the transaction
            # rolled back. Nothing was written. The user confirmed in the
            # app, and the release had nothing to unlock, because the
            # challenge it belonged to existed nowhere.
            #
            # And the polling itself is gone from this path. It never
            # belonged here: python-fints documents the decoupled release as
            # pause the dialog, store it, and resume it in a LATER request --
            # which is exactly what resolve_tan_interaction does, and what
            # the box in front of the user already drives with its OK button.
            # Polling in this request instead meant sending a status query on
            # the dialog that had just sent the order, and that is what the
            # bank refused. The fetch path keeps its poll: there it saves a
            # user from giving one release per account, and there it works.
            if not self._park_tan_challenge(response):
                self._cannot_note_the_challenge()
            self._publish_tan_prompt(response, decoupled=True)
        else:
            self.ask_for_tan(response)

        return None, {
            "status": "tan_required",
            "docname": self.kefiya_login.name,
            "decoupled": bool(getattr(response, "decoupled", False)),
        }

    def ask_for_tan(self, response, *, decoupled=None, possible_tan_modes=None, possible_tan_mediums=None):
        """Park the challenge and ask. The two halves, in that order.

        The order matters and the result of the first half matters too. A
        prompt whose challenge was not written down asks somebody for six
        digits that unlock nothing -- exactly the dead end the decoupled path
        was fixed for, left standing on the path where a TAN is typed.

        :return: True when the challenge is on disk and the prompt is up
        """
        if not self._park_tan_challenge(response):
            self._cannot_note_the_challenge()
        self._publish_tan_prompt(
            response, decoupled=decoupled,
            possible_tan_modes=possible_tan_modes,
            possible_tan_mediums=possible_tan_mediums)
        return True

    def _park_tan_challenge(self, response):
        """Store the challenge so a later request can finish the release.

        Pauses the dialog -- that is what get_data() and pause_dialog() do --
        so nothing may be sent on this connection afterwards. Which is why the
        decoupled path publishes its prompt first, waits on the live dialog,
        and only parks when it has run out of patience.

        A parked challenge is a fact about the outside world: the bank is now
        holding a dialog open and waiting. Whether that fact survives must not
        depend on the rest of this request finishing -- and it did. Everything
        here writes to the transaction and is committed only when the request
        returns cleanly, so a bank that stops answering afterwards
        (python-fints sends without a timeout, so that means the worker simply
        stops) took the parked challenge down with it. The user then confirmed
        in the app, the next attempt found no stored state, opened a fresh
        dialog and asked for a fresh release -- the same three steps over and
        over, with nothing in the Error Log to show for it.

        Committing here costs a partially finished fetch nothing: the only
        thing in flight at this point is the empty draft import, which the
        caller removes on this very path.

        :return: True when the challenge is on disk and committed
        """
        if not response:
            return False
        # Writing it down must not be what takes the request down. It did:
        # pausing the dialog threw after a failed status query, the exception
        # came out of here, the whole send failed, and the transaction rolled
        # back -- so the one thing this method exists to guarantee was the
        # thing that got lost. The caller now decides what an unparked
        # challenge means; here it is only reported.
        try:
            self._persist_fints_state(response)
            frappe.db.commit()
        except Exception:
            frappe.log_error(
                title="Kefiya: the TAN challenge could not be parked",
                message=(
                    "The bank is holding a dialog open and waiting for a"
                    " release, and this could not be written down -- so the"
                    " release will have nothing to unlock.\n\n{0}"
                ).format(frappe.get_traceback()),
                reference_doctype="Kefiya Login",
                reference_name=self.kefiya_login.name,
            )
            return False
        return True

    def _publish_tan_prompt(self, response, *, decoupled=None,
                            possible_tan_modes=None, possible_tan_mediums=None):
        """Put the question in front of the user. Touches no dialog.

        What the bank actually asks. comdirect sends a photoTAN -- a coloured
        mosaic the phone app reads -- and a Sparkasse sends chipTAN-QR the same
        way. Without it the prompt asks for a TAN and shows nothing to scan,
        which is not a question anybody can answer.
        """
        challenge = tan_challenge.challenge_of(response)

        if response.decoupled if decoupled is None else decoupled:
            self.interactive.request_mfa_confirmation(
                possible_tan_modes=possible_tan_modes,
                possible_tan_mediums=possible_tan_mediums,
                challenge=challenge)
        else:
            self.interactive.request_tan(
                possible_tan_modes=possible_tan_modes,
                possible_tan_mediums=possible_tan_mediums,
                challenge=challenge)

    def _cannot_note_the_challenge(self):
        """The bank is holding a dialog open and nothing wrote it down.

        One sentence, one place. Three callers used to answer this situation
        three different ways -- one raised with a specific message, one
        carried on and raised something generic, one ignored it entirely --
        and it is the same situation every time: the bank is waiting, the
        release cannot be answered here, and an order may already have
        reached it.
        """
        raise InitFailedException(_(
            "{0}: the bank is waiting for a release, but this could not be"
            " noted down here, so the release cannot be answered. Look in"
            " your online banking before sending again -- an order may have"
            " reached the bank."
        ).format(self.kefiya_login.name))

    def _await_release(self, challenge):
        """Wait for a decoupled release, then carry on where the bank stopped.

        The reason this exists: the release authorises the SESSION, and an
        access holds one dialog. Ending the run at the challenge meant the
        user confirmed in the app, came back to a finished run, and started
        another one -- and every account of that access asked again. Thirty
        accounts, thirty releases, which is what the user was actually
        living with.

        python-fints does the asking: send_tan on a decoupled challenge sends
        TAN process 'S', a status query. The bank answers 3956 for "not yet"
        and the library hands back a fresh NeedTANResponse to ask again with;
        anything else means it went through, and the library resumes the
        command that was waiting.

        Never raises. A poll that fails for any reason leaves the challenge
        parked and the caller behaves exactly as it did before this existed.

        :return: the resumed response, or None if it did not arrive in time
        """
        # Nobody is watching, so nobody is going to reach for a phone. The
        # scheduled import runs at six in the morning with no browser
        # attached: waiting five minutes for a release cannot produce one, it
        # can only spend the job's whole allowance and be killed mid-sleep --
        # which is exactly what it did, every morning. Park the challenge and
        # let the run end tidily; the user releases it when they next fetch
        # the access by hand.
        if not self.interactive.enabled:
            return None

        pause, total = decoupled_wait(
            self._decoupled_parameters(),
            self.DECOUPLED_POLL_SECONDS, self.DECOUPLED_MAX_WAIT_SECONDS,
            budget=job_budget_seconds())
        if total < pause:
            # No room left to ask even once.
            return None
        deadline = time.monotonic() + total
        limit = 1 + int(total / pause)

        for _attempt in range(limit):
            if time.monotonic() >= deadline:
                return None
            time.sleep(pause)
            try:
                answer = self.fints_connection.send_tan(challenge, "")
            except Exception:
                # With the bank's own words. python-fints puts its own
                # sentence in their place and that sentence is regularly
                # about something else -- a 9010 answering a status query is
                # reported as "could not fetch BPD ... check the bank
                # identifier", which sent the search in the wrong direction
                # for a day.
                frappe.log_error(
                    title="Kefiya: asking whether the release arrived failed",
                    message="What the bank said:\n{0}\n\n{1}".format(
                        fints_vop_client.what_the_bank_said(self.fints_connection),
                        frappe.get_traceback()),
                    reference_doctype="Kefiya Login",
                    reference_name=self.kefiya_login.name,
                )
                return None

            if not isinstance(answer, NeedTANResponse):
                # Released. The library has already resumed the command, so
                # this is the answer the caller was waiting for all along.
                self._persist_fints_state()
                self._tell_the_browser_it_can_stop_waiting()
                return answer

            # Still waiting. The bank hands back a new challenge to ask with.
            challenge = answer

        return None

    def _decoupled_parameters(self):
        """The bank's own numbers for a decoupled release, or None.

        Never raises: they live behind two lookups into a beta library, and a
        fetch must not fail because they could not be read.
        """
        try:
            mechanisms = self.fints_connection.get_tan_mechanisms()
            return mechanisms[self.fints_connection.get_current_tan_mechanism()]
        except Exception:
            return None

    def _tell_the_browser_it_can_stop_waiting(self):
        """Close the "confirm in your app" box, now that it has been.

        Best-effort: the box carries a Close button, and a realtime hiccup
        must not fail a fetch that has just succeeded.
        """
        try:
            frappe.publish_realtime(
                "kefiya_release_arrived",
                {"docname": self.kefiya_login.name},
                user=frappe.session.user)
        except Exception:
            pass

    def _resume_and_answer_the_parked_tan(self, tan):
        """Resume the parked dialog and answer the challenge waiting in it."""
        blob = self.kefiya_login.stored_dialog_blob
        with self.fints_connection.resume_dialog(blob):
            tan_request = NeedRetryResponse.from_data(
                self.kefiya_login.stored_tan_blob)

            # this decoupled setting is missing everywhere, so decoupled
            # requests (like pushTAN 2.0) cannot be handled without this
            tan_request.decoupled = \
                self.kefiya_login.stored_tan_state_decoupled
            # Nor is the VoP-ID, and without it the TAN goes out without the
            # payee approval the bank is waiting for.
            fints_vop.carry_vop_id(
                tan_request, self.kefiya_login.stored_vop_id_blob)
            self.fints_connection.init_tan_response = \
                self.fints_connection.send_tan(tan_request, tan)

            # on failure update status and skip
            if self.is_tan_required_and_requested(
                    self.fints_connection.init_tan_response):
                raise TanInteractionRequired()

            # What the bank said to the TAN, asked here and only here.
            #
            # Both endpoints that answer a parked challenge -- send_transfer_tan
            # and resolve_tan_interaction -- used to build a controller and
            # report success if that did not raise. Neither looked at the
            # answer, so a bank that refused the order got the same green
            # "authorised and submitted" as one that accepted it. Asking at the
            # point the TAN is actually answered means every caller inherits
            # it, and none of them has to reach into this object for it.
            self._refuse_a_refused_order()

            # on success clear stored tan state
            self._persist_fints_state()

