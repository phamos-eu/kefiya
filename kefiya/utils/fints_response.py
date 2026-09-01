# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What the bank actually said about an order.

Its own module, without frappe in it, for the reason the dialog predicates and
the duplicate rule have theirs: this decides whether a payment is reported as
sent, and a rule that can only be exercised against a live bank is a rule
nobody exercises.

Why it exists at all. The send path asked the response exactly two questions --
"is this a VoP mismatch" and "is a TAN wanted" -- and treated everything else
as success. A comment in it said the quiet part out loud:

    The dialog ended without a TAN. That is either a bank that asks for none,
    or an order that was never signed -- and those two look identical from
    here.

They do not look identical. A FinTS response carries HIRMS segments, and every
line in them has a return code whose first digit is the answer:

    0xxx   accepted           0010 "Nachricht entgegengenommen"
    3xxx   accepted, with a   3050 "Verwendungszweck gekuerzt"
           remark
    9xxx   REFUSED            9010 "Verarbeitung nicht moeglich"
                              9210 "Ungueltige Auftragsdaten"
                              9941 "TAN ungueltig"

python-fints already collects them: TransactionResponse.status is the worst of
them and .responses is every line. Both were ignored, so a bank that refused an
order in plain words produced "the bank did not request a TAN" in our log and
"Unbekannter Fehler" on the screen -- and the person sending was left to guess
at what an institute had told them exactly.

Nothing here decides anything on its own. It reads a response and says what is
in it; the controller decides what to do about it.
"""

#: The worst thing found in the response, weakest first. The names match
#: python-fints' ResponseStatus so the two can be compared without a table.
UNKNOWN = "unknown"
SUCCESS = "success"
WARNING = "warning"
ERROR = "error"

_RANK = {UNKNOWN: 0, SUCCESS: 1, WARNING: 2, ERROR: 3}

#: First digit of a FinTS return code -> what it means.
_BY_FIRST_DIGIT = {"0": SUCCESS, "3": WARNING, "9": ERROR}


def verdict_of(response):
    """Everything the bank said, as plain data.

    Defensive by design: a NeedTANResponse carries none of this, and a future
    library version may name it differently. An answer this cannot read is
    UNKNOWN, which the caller treats exactly as it treated every response
    before this module existed -- it blocks nothing.

    :return: {"status", "lines": [{"code", "text"}]}
    """
    lines = []
    for entry in (getattr(response, "responses", None) or []):
        code = _text(getattr(entry, "code", None))
        text = _text(getattr(entry, "text", None))
        # A HIRMS line is code / reference_element / text / parameters, and
        # the parameters are where an institute puts the part that actually
        # helps -- which field it objected to, which limit was exceeded. The
        # text alone is often only "Ungueltige Auftragsdaten".
        detail = _text(getattr(entry, "parameters", None))
        if code or text:
            lines.append({"code": code, "text": text, "detail": detail})

    return {"status": _worst(response, lines), "lines": lines}


def _worst(response, lines):
    """The worst status in the response.

    Read from the lines rather than from response.status, and then raised to
    whatever the library itself concluded. The lines are the source; the
    library's own verdict is a second opinion that can only make this stricter,
    never milder.
    """
    worst = UNKNOWN
    for line in lines:
        first = (line["code"] or " ")[0]
        found = _BY_FIRST_DIGIT.get(first, UNKNOWN)
        if _RANK[found] > _RANK[worst]:
            worst = found

    theirs = _text(getattr(getattr(response, "status", None), "name", None))
    theirs = {"SUCCESS": SUCCESS, "WARNING": WARNING,
              "ERROR": ERROR}.get(theirs.upper(), UNKNOWN)
    if _RANK[theirs] > _RANK[worst]:
        worst = theirs
    return worst


def refused(verdict):
    """Did the bank turn this order down?

    Only ERROR. A warning is an accepted order with a remark -- "reference
    shortened" is the commonest thing a German bank says about a transfer, and
    refusing on it would block half the payments in the country.
    """
    return (verdict or {}).get("status") == ERROR


def complaints(verdict):
    """The lines that say something went wrong, for a message to a person."""
    return [line for line in (verdict or {}).get("lines", [])
            if (line["code"] or " ")[0] in ("3", "9")]


def as_text(verdict, limit=6):
    """The bank's own words, on one line each. Empty when it said nothing.

    The bank's wording is used rather than a translation of it: what a person
    has to be able to do with this is read it out to their bank, and "9010"
    plus the institute's own sentence is what an adviser recognises.
    """
    out = []
    for line in (verdict or {}).get("lines", [])[:limit]:
        code, text = line["code"], line["text"]
        said = "{0} {1}".format(code, text).strip() if code else text
        detail = line.get("detail")
        if detail and detail not in said:
            said = "{0} ({1})".format(said, detail)
        out.append(said)
    return "\n".join(o for o in out if o)


#: Return codes we have actually met, and what a person can do about them.
#:
#: Deliberately short. A table of all 300 FinTS codes would be a table nobody
#: maintains and everybody half-trusts; these are the ones this instance has
#: seen, each with the next step rather than a restatement of the code.
KNOWN_CODES = {
    # "Fetch the account once, then send again" stood here, and it was wrong.
    # It was a guess -- that the cached bank parameters were too old to
    # advertise VoP -- and reading them settled it: HIVPPS is present, it lists
    # HKIPZ and HKCCS among the orders it covers, and the state was hours old.
    #
    # What actually happens: python-fints sends HKVPP and the bank does
    # check, but it then asks for an explicit confirmation, and the library
    # only turns a VoP answer into something this app can act on when the
    # check came back not-applicable, no-match or close-match. A bank that
    # checked successfully and still wants the confirmation fell through --
    # its own docstring calls that case "seems related to something not
    # implemented right now" -- and the order stopped here.
    #
    # fints_vop adds that branch, so in the ordinary case this code no longer
    # reaches a user at all: the confirmation is given, or a reviewer is
    # asked.
    #
    # There are now three ways to still land here, and the advice must not
    # pick one of them: the branch is not active (another library release, or
    # a shape that moved), the follow-up query failed, or the bank simply did
    # not finish checking in the time we waited. Each of the three writes its
    # own line to the Error Log; the advice points there rather than naming a
    # cause nobody established. It named one for a while, and it was wrong.
    "3945": (
        "The bank checked the payee for this order and will not release it"
        " until that check is confirmed. The order was NOT sent. Normally this"
        " app confirms the check itself and asks you only when the bank could"
        " not confirm the name. Why it did not get that far this time is in"
        " the Error Log -- most often the bank had not finished checking."
        " Send this transfer from your online banking and report this message."
    ),
    "9010": (
        "The bank could not process the message at all. Nothing was sent."
    ),
    "9210": (
        "The bank rejected the order data. The detail in brackets usually"
        " names the field it objected to."
    ),
}


def advice(verdict):
    """What to do about it, for the codes we know. Empty for the rest.

    Only the codes this instance has actually run into, and each says the next
    step rather than restating the number. An explanation invented for a code
    nobody here has seen is a guess wearing the clothes of documentation.
    """
    seen = []
    for line in (verdict or {}).get("lines", []):
        hint = KNOWN_CODES.get(line.get("code"))
        if hint and hint not in seen:
            seen.append(hint)
    return seen


def _text(value):
    return "" if value is None else str(value).strip()
