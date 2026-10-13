# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""When a Verification of Payee needs a person, and when it does not.

Verification of Payee exists to catch a payee name that does not belong to the
IBAN it is being sent to. Two of the cases it produces do not need anybody:

    the bank confirms the match   there is nothing for a person to check, and
                                  a click that can only ever say yes teaches
                                  people to click without reading -- which is
                                  the failure mode the whole check exists to
                                  prevent

    somebody already decided      the same payee, the same IBAN, and a person
                                  looked at it and said yes. Asking again
                                  every month is the same rubber stamp with
                                  extra steps

Everything else stops and waits for a human. That is the point of the check
and this module does not soften it.

No frappe in here. This decides whether money moves without somebody looking
at it, so it has to be readable and testable on its own -- the same reason
duplicate_rule and fints_response stand apart from the code that queries for
them.
"""

#: What the bank can answer, from python-fints' own notes on HIVPP.
FULL_MATCH = "RCVC"          #: name and IBAN belong together
CLOSE_MATCH = "RVMC"         #: nearly -- the bank returns the name it holds
NO_MATCH = "RVNM"            #: they do not
NOT_AVAILABLE = "RVNA"       #: the bank could not check
PENDING = "PDNG"             #: the bank has not finished checking

#: Answers that mean a person has to look. NOT_AVAILABLE is deliberately in
#: here: "I could not check" is not "it is fine", and treating it as fine
#: would turn every bank outage into an unchecked payment.
NEEDS_A_LOOK = (CLOSE_MATCH, NO_MATCH, NOT_AVAILABLE, PENDING)


def normalise(text):
    """A payee name in the form two spellings of it can be compared in.

    Case, extra spaces and the punctuation that companies decorate themselves
    with. NOT an attempt to guess that two different names are the same
    company -- this is the key a person's decision is filed under, and a key
    that matches too eagerly files the decision against a payee nobody looked
    at.

    Deliberately NOT payee_check.normalise_name, which drops legal forms so
    that "ACME GmbH" and "ACME KG" compare equal. That is right for warning
    somebody at entry, and wrong here: those are two companies, and a decision
    about one of them is not a decision about the other.
    """
    if not text:
        return ""
    kept = []
    for ch in str(text).lower().strip():
        if ch.isalnum():
            kept.append(ch)
        elif ch.isspace() or ch in "-_.,&/+":
            kept.append(" ")
    return " ".join("".join(kept).split())


def payee_key(iban, payee_name):
    """What a remembered decision is filed under.

    Both halves, always. The IBAN alone would carry a decision over to a
    different name at the same account, and the name alone to a different
    account. Either would be a decision nobody made.
    """
    return "{0}|{1}".format(
        (iban or "").replace(" ", "").upper(), normalise(payee_name))


def needs_a_human(result, remembered=False):
    """Does this Verification of Payee answer have to stop and wait?

    :param result: what the bank answered -- RCVC, RVMC, RVNM, RVNA, PDNG
    :param remembered: True when this exact payee and IBAN were approved
        before by a person
    :return: bool
    """
    if result == FULL_MATCH:
        return False
    if remembered:
        return False
    # Including an answer this does not recognise. A code nobody here has seen
    # is a reason to ask, not a reason to assume.
    return True


#: Where the answer about a single payment actually sits. The HIVPP segment
#: carries the VoP-ID and the polling fields; the result and the name the bank
#: holds are one level down, in its EVPE ("Ergebnis VOP-Pruefung
#: Einzeltransaktion"). Reading the segment alone finds nothing -- which would
#: leave every check looking unanswerable, so this is not a detail.
INNER = "vop_single_result"


def _one_field(holder, name):
    """One named field off an object or a mapping, or ""."""
    try:
        if hasattr(holder, "get") and not hasattr(holder, name):
            value = holder.get(name)
        else:
            value = getattr(holder, name, None)
    except Exception:
        return ""
    return str(value) if value else ""


def _field(vop_result, name):
    """One field of the bank's answer, wherever the answer keeps it.

    Three shapes turn up: the HIVPP segment the library hands over, the EVPE
    inside it, and a mapping -- the parked answer has been through the
    database. All three are read, in that order.

    Anything unreadable is simply nothing. This reaches into a beta library's
    parsed segments, and a reviewer seeing the bare result code beats a crash
    on the payment path.
    """
    value = _one_field(vop_result, name)
    if value:
        return value
    inner = None
    try:
        inner = (vop_result.get(INNER) if hasattr(vop_result, "get")
                 and not hasattr(vop_result, INNER)
                 else getattr(vop_result, INNER, None))
    except Exception:
        inner = None
    return _one_field(inner, name) if inner is not None else ""


def bank_name_from(vop_result):
    """The name the bank holds for that IBAN, where it says one.

    Only the close-match answer carries one; the others have nothing to show a
    reviewer.
    """
    for field in ("close_match_name", "other_identification"):
        value = _field(vop_result, field)
        if value:
            return value
    return ""


def result_from(vop_result):
    """The answer code, or "" where it cannot be read."""
    return _field(vop_result, "result")


def parked_answer(stored):
    """What was written down about a parked check, read back.

    The counterpart to storing it: one JSON record with result, the name the
    bank holds and who the order pays. Two readers -- the dialog that shows a
    reviewer the decision, and the code that files it once they have made it.

    {} for anything unreadable, so a record from an older release simply says
    nothing rather than raising on a payment path.
    """
    if not stored:
        return {}
    try:
        import json

        value = json.loads(stored)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}
