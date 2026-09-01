# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Die eine Klasse, die einer fremden Bibliothek hinterherlaufen muss.

Hier steht eine Kopie von drei Methoden aus python-fints, mit Aenderungen.
Das ist der einzige Teil dieser App, der bei jedem Versionssprung der
Bibliothek gelesen werden muss -- und deshalb steht er allein.

Vorher lag er zwischen den Regeln, dem Traegertyp und den Protokoll-
Formatierern in einer Datei mit 683 Zeilen. Wer nach einem Versionssprung
wissen wollte, was er nachziehen muss, musste vier Themen auseinanderhalten.
Jetzt ist die Frage "was haengt an der Bibliothek" dieselbe wie "was steht in
dieser Datei".

Die Regeln selbst -- was ein Pruefergebnis bedeutet, wann eine Freigabe
moeglich ist, wie eine VoP-ID ueber das Parken kommt -- stehen in
fints_vop.py und brauchen keine Bibliothek und keine Site.

WHY THIS CANNOT SEND TWICE, which is the only question that matters here:

    3945 heisst, die Bank hat den Auftrag NICHT freigegeben. Der ergaenzte
    Zweig macht aus einer Ablehnung eine geparkte Anforderung -- kefiya
    beantwortet die mit vop_pending = 1 und markiert nichts als gesendet.
    Geld bewegt sich erst, wenn ein Mensch den Empfaenger absichtlich
    bestaetigt, und genau das ist der Sinn der Empfaengerpruefung.
"""

from kefiya.utils.fints_vop import (CONFIRMATION_DEMANDED,
                                    POLL_LIMIT_SECONDS, can_be_approved,
                                    demands_confirmation, poll_pause,
                                    readable_verdict, result_from)

#: The releases this copy was checked against. Any other one gets the
#: library's own method, unchanged -- a stale copy of a payment path is worse
#: than a missing feature.
#:
#: 5.0.0 final is on the list because it was diffed against 5.0.0b1, not
#: because it is newer. The whole difference in client.py is two hunks: an
#: early return when _get_transactions_xml needs a TAN, and the RCVC branch
#: this class was written to add. send_tan is byte for byte the same in both
#: -- including the two faults corrected below -- so the copy is as valid
#: against one as against the other. Anything after that has to be diffed
#: again before it goes on this list.
KNOWN_GOOD = ("5.0.0b1", "5.0.0")

def installed_version():
    """The python-fints release actually installed, or None."""
    try:
        from importlib.metadata import version

        return version("fints")
    except Exception:
        return None


def _pieces():
    """Everything the copied method touches, or None if the shape has moved."""
    try:
        from fints.client import (FinTS3PinTanClient, FinTSClientError,
                                  NeedTANResponse, NeedVOPResponse)
        # PSRD1 sits in segments.auth, not in formals. Guessing formals from
        # the way client.py uses the bare name cost nothing only because the
        # check below was run against the installed library before shipping:
        # the import would have failed, _pieces() would have answered None,
        # and the branch would have been dead code that reported itself as
        # installed.
        from fints.segments.auth import HIVPP1, HKVPA1, HKVPP1, PSRD1
    except Exception:
        return None

    needed = ("_find_vop_format_for_segment", "_need_twostep_tan_for_segment",
              "_get_tan_segment", "is_challenge_structured",
              "_send_pay_with_possible_retry", "send_tan", "_get_dialog",
              "_process_response")
    for name in needed:
        if not hasattr(FinTS3PinTanClient, name):
            return None

    return {"base": FinTS3PinTanClient, "NeedTANResponse": NeedTANResponse,
            "NeedVOPResponse": NeedVOPResponse, "PSRD1": PSRD1,
            "HKVPP1": HKVPP1, "HIVPP1": HIVPP1, "HKVPA1": HKVPA1,
            "FinTSClientError": FinTSClientError}


def client_class():
    """The client class to build a bank connection with.

    The subclass where this is the library it was written against and every
    piece it needs is where it expects it; the library's own class otherwise.
    Never raises -- a bank connection must not fail over an optional branch.
    """
    parts = _pieces()
    if parts is None:
        _note("python-fints does not have the pieces this needs where it"
              " expects them")
        return None

    if installed_version() not in KNOWN_GOOD:
        _note("python-fints {0} is installed, and this was checked against"
              " {1}".format(installed_version(), " / ".join(KNOWN_GOOD)))
        return parts["base"]

    try:
        return _build(parts)
    except Exception:
        import frappe

        frappe.log_error(
            title="Kefiya: could not build the VoP-aware client",
            message=frappe.get_traceback())
        return parts["base"]


def _build(parts):
    base = parts["base"]
    NeedTANResponse = parts["NeedTANResponse"]
    NeedVOPResponse = parts["NeedVOPResponse"]
    PSRD1 = parts["PSRD1"]
    HKVPP1 = parts["HKVPP1"]
    HIVPP1 = parts["HIVPP1"]
    HKVPA1 = parts["HKVPA1"]
    FinTSClientError = parts["FinTSClientError"]

    class VopAwarePinTanClient(base):
        """A client that can also be told "confirm the payee first"."""

        #: What the bank last said, so a failure can quote it.
        last_said = ()

        def _process_response(self, dialog, segment, response):
            """Remember the bank's words, then let the library decide.

            Changes nothing. It exists because python-fints throws away what
            the bank actually said and puts its own sentence in its place --
            and that sentence is wrong outside the one case it was written
            for::

                if response.code == '9010':
                    raise FinTSClientError("Error during dialog initialization,
                        could not fetch BPD. Please check that you passed the
                        correct bank identifier to the HBCI URL ...")

            The check is unconditional, so a 9010 answering a TAN status query
            mid-dialog -- which is what the Sparkasse sent, and what stopped
            KEF-TRF-2026-00007 -- is reported as a wrong bank URL. The URL was
            right. Nobody could have got from that message to the cause, and
            the whole run had to be reconstructed from the outside.

            So every response is noted here first, and the caller quotes them
            when something fails. Guarded: a client that cannot remember what
            it heard must still work exactly as before.
            """
            try:
                said = (str(getattr(response, "code", "")),
                        str(getattr(response, "text", "")))
                self.last_said = (self.last_said + (said,))[-12:]
            except Exception:
                pass
            return super()._process_response(dialog, segment, response)

        def send_tan(self, challenge, tan):
            """Answer the TAN -- and keep saying the payee was confirmed.

            THIS is why no transfer has gone out at the Volksbank. The
            library attaches the payee approval to the TAN only here::

                if challenge.vop_result and \
                        challenge.vop_result.vop_single_result.result == 'RCVC':
                    vop_seg = [HKVPA1(vop_id=challenge.vop_result.vop_id)]

            It asks the verdict. At a bank that answers with a complete
            payment status report -- report_complete='V', which is what this
            one does -- vop_single_result is an empty EVPE() and the verdict
            is None. Never 'RCVC'. So HKVPA1 is never attached, the TAN
            arrives without the confirmation the bank demanded, and the bank
            answers what the user has been reading all along::

                3945  Freigabe ohne VOP-Bestaetigung nicht moeglich.

            The order carries a valid VoP-ID the whole time. It simply is
            never sent, because the condition asks about a field this bank
            does not fill.

            Same correction as can_be_approved(), one step further down the
            path: the VoP-ID is what the approval segment needs, so the
            VoP-ID is what decides whether to send it. Where the verdict IS
            readable this behaves as before -- an RCVC answer has a VoP-ID
            too.

            The second fix is in the decoupled branch. When the bank says
            3956 ("not yet released") the library builds a fresh challenge to
            poll with and drops vop_result on the floor -- it passes five
            arguments where the constructor takes six. So even at a bank
            whose verdict IS readable, the approval goes out with the first
            poll and with none of the ones after it. Carried through here.
            """
            with self._get_dialog() as dialog:
                if challenge.decoupled:
                    tan_seg = self._get_tan_segment(
                        challenge.command_seg, 'S', challenge.tan_request)
                else:
                    tan_seg = self._get_tan_segment(
                        challenge.command_seg, '2', challenge.tan_request)
                    self._pending_tan = tan

                vop_seg = []
                if can_be_approved(getattr(challenge, "vop_result", None)):
                    vop_seg = [HKVPA1(vop_id=challenge.vop_result.vop_id)]
                response = dialog.send(*(vop_seg + [tan_seg]))

                if challenge.decoupled:
                    # TAN process = S
                    status_segment = response.find_segment_first('HITAN')
                    if not status_segment:
                        raise FinTSClientError("No TAN status received.")
                    for resp in response.responses(tan_seg):
                        if resp.code == '3956':
                            return NeedTANResponse(
                                challenge.command_seg,
                                challenge.tan_request,
                                challenge.resume_method,
                                challenge.tan_request_structured,
                                challenge.decoupled,
                                challenge.vop_result,
                            )

                resume_func = getattr(self, challenge.resume_method)
                return resume_func(challenge.command_seg, response)

        def _await_vop_result(self, dialog, vop_standard, hivpp,
                              response=None, asked=None):
            """Keep asking until the bank has finished checking the payee.

            The check is asynchronous at some banks: the first answer is a
            polling id and "ask again in n seconds". HKVPP carries that
            polling id for exactly this, and the library never sends it.

            It also carries an Aufsetzpunkt, and that one matters just as
            much here. The Volksbank answers the order with::

                3040 Es liegen weitere Informationen vor. (['staticscrollref'])
                3945 Freigabe ohne VOP-Bestaetigung nicht moeglich.

            3040 is the bank saying "there is more, ask again with this
            reference" -- python-fints uses exactly this code and parameter
            to fetch the rest of a statement. The follow-up here was sending
            the polling id and nothing else, so the bank was asked again
            without the reference it had just handed over.

            :param response: the answer the order came back in, for the first
                scroll reference
            :param asked: the HKVPP segment that answer refers to

            Asked until there is a VoP-ID to release with. Bounded, and
            honest when it runs out: the caller gets the answer as it stands,
            can_be_approved() is still false, and the order ends unsent with
            the bank's own words. A parked challenge without a VoP-ID would be
            a dead end wearing the clothes of one that can be released.
            """
            import time

            waited = 0.0
            scroll = touchdown_in(response, asked) if response is not None \
                else None
            self.vop_poll_trail = [_poll_note(0, 0.0, hivpp, scroll)]
            while not can_be_approved(hivpp) and waited < POLL_LIMIT_SECONDS:
                pause = poll_pause(hivpp)
                time.sleep(pause)
                waited += pause
                query = HKVPP1(
                    supported_reports=PSRD1(psrd=[vop_standard]),
                    polling_id=hivpp.polling_id,
                    aufsetzpunkt=scroll)
                try:
                    again = dialog.send(query)
                    nxt = again.find_segment_first(HIVPP1)
                except Exception:
                    # The bank would not answer the follow-up. Whatever the
                    # reason, this order is not going out on a guess -- but it
                    # is said out loud. This used to break silently, and the
                    # order then ended with "the payee confirmation is not
                    # working", which is a cause nobody had established.
                    self.vop_poll_trail.append(
                        "  ask {0} after {1:.0f}s: the follow-up did not get"
                        " through".format(len(self.vop_poll_trail), waited))
                    _log_poll_failure()
                    break
                scroll = touchdown_in(again, query) or scroll
                if nxt is None:
                    # The bank answered, but with no payee-check segment in
                    # it. Worth its own line: it is a different thing from a
                    # bank that keeps saying "still checking", and the two
                    # used to be indistinguishable in the log -- both ended
                    # as "asked for 30 seconds", which for this one is not
                    # true. It was asked once.
                    self.vop_poll_trail.append(
                        "  ask {0} after {1:.0f}s: answered, but with no HIVPP"
                        " segment -- {2}".format(
                            len(self.vop_poll_trail), waited,
                            _codes_in(again, query)))
                    break
                hivpp = nxt
                self.vop_poll_trail.append(
                    _poll_note(len(self.vop_poll_trail), waited, hivpp,
                               scroll))
            return hivpp

        def _send_pay_with_possible_retry(self, dialog, command_seg,
                                          resume_func):
            vop_seg = []
            vop_standard = self._find_vop_format_for_segment(command_seg)
            if vop_standard:
                vop_seg = [HKVPP1(supported_reports=PSRD1(psrd=[vop_standard]))]

            with dialog:
                if self._need_twostep_tan_for_segment(command_seg):
                    tan_seg = self._get_tan_segment(command_seg, '4')
                    segments = vop_seg + [command_seg, tan_seg]

                    response = dialog.send(*segments)

                    if vop_standard:
                        hivpp = response.find_segment_first(HIVPP1, throw=True)
                        # Wait for the verdict before deciding anything. The
                        # first answer is often only "still checking, ask
                        # again", and everything below needs a result.
                        hivpp = self._await_vop_result(
                            dialog, vop_standard, hivpp,
                            response=response,
                            asked=vop_seg[0] if vop_seg else None)

                        # Not applicable, no match, close match -- the
                        # library's own three, unchanged.
                        #
                        # The fourth case is this class's whole reason for
                        # existing: the check went through and the bank still
                        # wants it confirmed. Same parked challenge, so the
                        # same deliberate approval releases it.
                        #
                        # A check that never finished parks nothing: without a
                        # VoP-ID the approval segment is empty and the release
                        # could not go through, so the order falls through to
                        # the bank's own refusal instead of to a button that
                        # cannot work.
                        if not can_be_approved(hivpp):
                            _log_no_vop_id(
                                hivpp, getattr(self, "vop_poll_trail", None))
                        # Park whenever there is something to release with and
                        # a reason to. The reason is either a verdict that
                        # wants looking at, or the bank saying outright that it
                        # will not release without the confirmation (3945).
                        #
                        # Not "only when we could read the verdict": at a bank
                        # that answers with a payment status report there is
                        # never a readable verdict, and requiring one meant no
                        # transfer could ever go out.
                        if can_be_approved(hivpp) and (
                                readable_verdict(hivpp) in ('RVNA', 'RVNM',
                                                            'RVMC')
                                or demands_confirmation(response, tan_seg)):
                            return NeedVOPResponse(
                                vop_result=hivpp,
                                command_seg=command_seg,
                                resume_method=resume_func,
                            )
                    else:
                        hivpp = None

                    for resp in response.responses(tan_seg):
                        if resp.code in ('0030', '3955'):
                            return NeedTANResponse(
                                command_seg,
                                response.find_segment_first('HITAN'),
                                resume_func,
                                self.is_challenge_structured(),
                                resp.code == '3955',
                                hivpp,
                            )
                        if resp.code.startswith('9'):
                            raise Exception(
                                "Error response: {!r}".format(response))
                else:
                    response = dialog.send(command_seg)

                return resume_func(command_seg, response)

    return VopAwarePinTanClient


def _note(why):
    """Say once, in the Error Log, that transfers lose the added branch."""
    import frappe

    frappe.log_error(
        title="Kefiya: sending without the VoP confirmation branch",
        message=(
            "{0}.\n\nA transfer to a bank that answers 3945 will stop with"
            " 'not sent' until this is looked at. Nothing else is affected."
        ).format(why))


def _codes_in(response, asked):
    """The bank's response codes and words, for a log line. Never raises.

    ``asked`` is the segment the codes answer -- responses() takes the
    segment it refers to, and calling it without one raises rather than
    returning everything, which would have made this log line read
    "unreadable" every single time.
    """
    try:
        said = ["{0} {1}".format(getattr(r, "code", "?"),
                                 getattr(r, "text", ""))
                for r in response.responses(asked)]
    except Exception:
        return "(response codes unreadable)"
    return "; ".join(said) if said else "(no response codes)"


def touchdown_in(response, asked):
    """The Aufsetzpunkt the bank attached to this answer, or None.

    3040 means "es liegen weitere Informationen vor" and carries a scroll
    reference as its first parameter -- python-fints uses exactly this to
    fetch the rest of a statement. The Volksbank sends it with the payee
    check::

        3040 Es liegen weitere Informationen vor. (['staticscrollref'])
        3945 Freigabe ohne VOP-Bestaetigung nicht moeglich.

    HKVPP has a field for it (aufsetzpunkt) and the follow-up was not
    sending one: the bank said "there is more, ask again with this" and was
    asked again without it. Never raises -- a missing scroll reference means
    the follow-up goes as it did before.
    """
    try:
        for said in response.responses(asked, "3040"):
            parameters = getattr(said, "parameters", None)
            if parameters:
                return parameters[0]
    except Exception:
        return None
    return None


def _poll_note(step, waited, hivpp, scroll=None):
    """One line about one answer to the payee check.

    The point of the trail: "asked for 30 seconds and got nothing" was said
    whether the bank had been asked fifteen times or once, and those are
    different faults with different fixes. Now the log says which.
    """
    return (
        "  ask {0} after {1:.0f}s: vop_id={2} result={3!r} polling_id={4}"
        " report={5} bytes wait={6} aufsetzpunkt={7!r}"
    ).format(
        step, waited,
        "yes" if getattr(hivpp, "vop_id", None) else "no",
        result_from(hivpp),
        getattr(hivpp, "polling_id", None),
        len(getattr(hivpp, "payment_status_report", b"") or b""),
        getattr(hivpp, "wait_for_seconds", None),
        scroll)


def _log_no_vop_id(hivpp, trail=None):
    """The bank never handed over a VoP-ID. Said, so it is not guessed at."""
    try:
        import frappe

        frappe.log_error(
            title="Kefiya: the bank gave no VoP-ID for the payee check",
            message=(
                "There was nothing to release the order with -- the approval"
                " segment is HKVPA(vop_id=...). The order was NOT sent."
                "\n\nWhat the bank said, ask by ask (ceiling {0}s):\n{1}"
                "\n\nLast answer in full:\npolling_id={2}"
                "\nwait_for_seconds={3}\npayment_status_report={4} bytes"
                "\nvop_single_result={5}"
            ).format(POLL_LIMIT_SECONDS,
                     "\n".join(trail or ["  (the loop kept no trail)"]),
                     getattr(hivpp, "polling_id", None),
                     getattr(hivpp, "wait_for_seconds", None),
                     len(getattr(hivpp, "payment_status_report", b"") or b""),
                     getattr(hivpp, "vop_single_result", None)))
    except Exception:
        pass


def _log_poll_failure():
    """Why the follow-up stopped, in the Error Log. Never raises."""
    try:
        import frappe

        frappe.log_error(
            title="Kefiya: asking the bank for the payee result failed",
            message=(
                "The payee check was still running and the follow-up query"
                " (HKVPP with the polling id) did not get through. The order"
                " was NOT sent.\n\n{0}"
            ).format(frappe.get_traceback()))
    except Exception:
        pass


def what_the_bank_said(connection):
    """The last response codes a connection heard, as readable lines.

    python-fints replaces the bank's words with its own sentence and that
    sentence is often about something else. This is the record kept
    alongside. Never raises; answers a note saying so when there is nothing.
    """
    said = getattr(connection, "last_said", None)
    if not said:
        return "(nothing recorded -- this connection keeps no record)"
    try:
        return "\n".join("  {0} {1}".format(code, text) for code, text in said)
    except Exception:
        return "(the record could not be read)"
