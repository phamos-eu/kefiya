# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The one branch python-fints 5.0.0b1 is missing for Verification of Payee.

The Volksbank answers an instant transfer with::

    3040  Es liegen weitere Informationen vor.  (['staticscrollref'])
    3945  Freigabe ohne VOP-Bestaetigung nicht moeglich.

It checked the payee and now wants that check confirmed before it will release
the order. The library sends the check (HKVPP) and reads the answer, but only
turns it into something an application can act on when the check came back
not-applicable, no-match or close-match::

    if vop_result.result in ('RVNA', 'RVNM', 'RVMC'):
        return NeedVOPResponse(...)

A check that SUCCEEDED and still asks to be confirmed falls straight through.
The library's own docstring names that case and calls it "seems related to
something not implemented right now". The order stops there, and nothing
downstream can pick it up: approve_vop_response needs the HIVPP segment AND
the command segment that was sent, and both are local to the method above.

So this subclasses that one method and adds that one branch. Everything else
is the library's, line for line, including the parts that look redundant.

And a second one, which the first live transfer found: at this bank the check
is ASYNCHRONOUS. The first answer carries no verdict at all, only a polling id
and "ask again in two seconds"::

    HIVPP1(header=..., polling_id=b'587d03b8-...', wait_for_seconds=2)

HKVPP carries that polling id for exactly this follow-up, and the library
never sends it. Without the follow-up there is nothing to decide on -- and the
challenge that got parked instead could never be released, because the
approval segment is HKVPA(vop_id=...) and this answer has no VoP-ID. So the
method now waits for the verdict before it decides anything, and parks nothing
when the verdict never comes.

WHY THIS CANNOT SEND TWICE, which is the only question that matters here:

    3945 means the bank did NOT release the order. The added branch turns a
    refusal into a parked challenge -- kefiya answers that with
    vop_pending = 1 and marks nothing as sent. The money moves only when a
    person then approves the payee deliberately, which is the whole point of
    Verification of Payee and the reason it is not automated.

The version guard is not decoration. This mirrors a private method of a beta
library; if that library's shape moves under us, falling back to its own
implementation is strictly better than running a stale copy against a bank.
"""

# frappe is imported where it is used, not here. The rule below -- did the
# bank refuse to release the order without the payee check being confirmed --
# decides what happens to a payment, and it has no business needing a site to
# be exercised. The same reason duplicate_rule and fints_response stand apart
# from the code that queries for them.

# The result code is read by vop_rule.result_from -- the same nesting, the
# same defensiveness, and it already handles the mapping shape a parked answer
# comes back in. A second reader of one field is a second thing to keep right.
from kefiya.utils.vop_rule import result_from

#: The bank checked the payee and wants the check confirmed before releasing.
CONFIRMATION_DEMANDED = "3945"

#: How long to keep asking while the bank is still checking. The bank names
#: the pause between two questions itself (wait_for_seconds); this only bounds
#: the total, because somebody is sitting in front of a spinner.
#:
#: Sixty, not thirty. Thirty was a guess, and at two seconds a poll it gave
#: the bank fifteen chances to finish a check that is run against a foreign
#: institution -- the answer travels the SEPA network, not the bank's own
#: database. A minute is still short enough to sit through and long enough
#: that "it never answered" means something.
POLL_LIMIT_SECONDS = 60
#: What to wait when the bank names nothing.
DEFAULT_POLL_SECONDS = 2


def demands_confirmation(response, tan_seg):
    """Did the bank say it will not release this without the confirmation?

    Its own function so the rule can be read and tested without a bank.
    Defensive: a response this cannot read answers False, and the order takes
    exactly the path it took before this module existed.
    """
    try:
        for resp in response.responses(tan_seg):
            if str(getattr(resp, "code", "")) == CONFIRMATION_DEMANDED:
                return True
    except Exception:
        return False
    return False


def can_be_approved(hivpp):
    """Is there enough in this answer to release the order with?

    Exactly one thing decides it: the VoP-ID. The approval segment is
    HKVPA(vop_id=...) and nothing else, so an answer without one cannot be
    released no matter what else it carries -- and an answer WITH one can,
    whether or not we can read the verdict.

    This used to ask "is the bank still checking", which read the verdict
    fields. That was the wrong question at this bank, and measurably so::

        HIVPPS1(parameter=ParameterVoP(..., report_complete='V',
                supported_report_formats='urn:iso:std:iso:20022:tech:xsd:pain.002.001.10',
                payment_order_segment=['HKCCS', ..., 'HKIPZ', ...]))

    report_complete='V' means this bank answers with the COMPLETE payment
    status report -- a pain.002 document in HIVPP.payment_status_report -- and
    that field is mutually exclusive with vop_single_result, which is where
    every verdict reader in this codebase and in python-fints looks.
    payment_status_report appears zero times in the library's client.py. So
    the verdict is never readable here, "still checking" was permanently true,
    and the order fell through to the bank's refusal every time.

    Asking for the VoP-ID instead makes the release possible at a bank whose
    answer we cannot read -- which is the honest position: the person
    approving is then the whole check, and the dialog says so.
    """
    return bool(getattr(hivpp, "vop_id", None)) if hivpp is not None else False


class CarriedVopId:
    """The one field of a payee check that a parked challenge has to keep.

    Not the segment: rebuilding an HIVPP1 out of storage would mean claiming
    the rest of it is right too, and everything downstream needs exactly one
    field. This carries that field and answers to can_be_approved() like the
    real thing.
    """

    def __init__(self, vop_id):
        self.vop_id = vop_id

    def __repr__(self):
        return "CarriedVopId(vop_id={0!r})".format(self.vop_id)


def vop_id_of(challenge):
    """The VoP-ID a challenge carries, or None. Never raises."""
    try:
        return getattr(getattr(challenge, "vop_result", None), "vop_id", None)
    except Exception:
        return None


def carry_vop_id(challenge, vop_id):
    """Put a stored VoP-ID back on a challenge that was read out of storage.

    Leaves a challenge that already carries a payee check alone -- a live one
    knows better than the database does. Does nothing without an ID, so a
    login that never met a payee check behaves exactly as before.

    :return: True if the challenge now carries an ID
    """
    if challenge is None:
        return False
    if can_be_approved(getattr(challenge, "vop_result", None)):
        return True
    if not vop_id:
        return False
    try:
        challenge.vop_result = CarriedVopId(vop_id)
    except Exception:
        return False
    return True


def readable_verdict(hivpp):
    """The verdict where the bank puts one in the field anybody reads.

    Empty at a bank that answers with a payment status report. Not an error --
    a reason to ask a person rather than to decide for them.
    """
    return result_from(hivpp)


def poll_pause(hivpp):
    """How long to wait before asking again, within reason.

    The bank names it. Bounded on both sides so a missing or absurd value
    cannot turn into a busy loop or a request nobody waits out.
    """
    try:
        said = float(getattr(hivpp, "wait_for_seconds", 0) or 0)
    except Exception:
        said = 0.0
    if said <= 0:
        said = DEFAULT_POLL_SECONDS
    return max(1.0, min(said, 10.0))


