# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What made the bank ask for a release -- written down every time.

"Why do I have to release every Volksbank account every day?" had no answer
in the data. The nightly run fetched the bookings of fourteen Volksbank
accounts on 16., 17. and 18.11.2026 without anybody releasing anything; the
fetch by hand later the same days asked for a release per account. Nothing
recorded WHICH request the bank wanted released, so the cause could only be
guessed: the dialog opening, the account list, the bookings themselves -- or
the second access of the day, where the first one went through.

From now on every request for a release leaves one line: which login, which
business transaction the bank attached the request to (HKIDN for the dialog
opening, HKKAZ/HKCAZ for bookings, HKSPA for the account list, ...), whether
somebody was there to answer it, how often the login had already been fetched
that day, and the bank's own challenge text.

No frappe, like tan_challenge: what is read off the bank's answer must be
checkable without a bench.
"""

#: What the segment types mean, in the words a person reads the log in.
#: Only those a fetch or an order sends; anything else is shown as it is.
KNOWN = {
    "HKIDN": "dialog opening",
    "HKSPA": "account list",
    "HKKAZ": "bookings (MT940)",
    "HKCAZ": "bookings (camt)",
    "HKSAL": "balance",
    "HKTAB": "TAN media list",
    "HKCDB": "standing orders",
    "HKCSB": "dated transfers held by the bank",
    "HKDBS": "dated direct debits",
    "HKEKA": "account statements",
    "HKEKP": "account statements (PDF)",
    "HKKAU": "statement list",
    "HKWPD": "securities holdings",
    "DKKKU": "credit card transactions",
    "HKCCS": "transfer",
    "HKCCM": "collective transfer",
    "HKIPZ": "instant transfer",
    "HKIPM": "collective instant transfer",
    "HKCSE": "dated transfer",
    "HKCME": "collective dated transfer",
}


def segment_of(response):
    """The type of the request the bank wants released, or "".

    python-fints keeps it on the NeedTANResponse as ``command_seg``; the
    dialog opening carries no command segment of its own there.
    """
    seg = getattr(response, "command_seg", None)
    header = getattr(seg, "header", None)
    kind = getattr(header, "type", None)
    if isinstance(kind, str) and kind:
        return kind
    # A segment class without an instance header still names itself.
    name = type(seg).__name__ if seg is not None else ""
    return name[:5] if name[:2] in ("HK", "DK") else ""


def describe(response, *, login, attended, earlier_today, challenge_text=""):
    """One readable entry for the log.

    :param attended: True when somebody is there to give the release
    :param earlier_today: how many fetches of this login had already run
        that day -- the number that says whether only the first one is free
    """
    segment = segment_of(response)
    what = KNOWN.get(segment, "")
    lines = [
        "login={0}".format(login),
        "request={0}{1}".format(segment or "(dialog opening or unknown)",
                                " -- " + what if what else ""),
        "decoupled={0}".format(
            1 if getattr(response, "decoupled", False) else 0),
        "attended={0}".format(1 if attended else 0),
        "fetches of this login earlier today={0}".format(earlier_today),
    ]
    if challenge_text:
        lines.append("bank: " + challenge_text[:300])
    return "\n".join(lines)
