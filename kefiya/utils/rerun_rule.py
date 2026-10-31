# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Two identical rows on one account: a second pass, or a second payment?

statement_repair sorts out the same booking filed under several ACCOUNTS. It
refused this case, and said so in as many words::

    3,079 bookings in this run are doubled within one account, and nothing
    distinguishes the two rows. ... That is either an importer writing a line
    twice or a card charged twice in one day, and no field here tells them
    apart.

That was right at the time and it is no longer true. There is a field, and it
was simply never looked at: ``creation``.

The 21.05.2026 run wrote 173,178 drafts between 12:52 and 15:42, and the
duplicate pairs fall into two clearly separated populations:

    2,508 pairs   the two rows are 6 to 93 seconds apart, eight of them over
                  an hour. Rows written seconds apart in one pass are
                  CONSECUTIVE -- a gap of a minute across thousands of rows
                  means the file was read a second time.

     501 pairs    the two rows are 0 to 4 seconds apart. Both come out of one
                  pass, which means the source itself listed the booking
                  twice. That may be a duplicated line in the export, and it
                  may be a tenant who really did pay the same amount twice on
                  one day. Nothing here can tell, so nothing here decides.

The rule is therefore about the gap and nothing else, and it keeps the
earliest row -- the one the first pass wrote.

No frappe: this decides whether a payment is removed, and the module that
decides that is exercised without a site. The same reason duplicate_rule and
vop_rule stand apart from the code that queries for them.
"""

#: Below this many seconds apart, two rows came out of one pass and this
#: module has nothing to say about them. Chosen from the measured gap on the
#: live run -- one population ends at 4 seconds, the next begins at 6 -- and
#: deliberately at the cautious end of that gap.
SECONDS_APART = 5

FROM_A_RERUN = "rerun"
SAME_PASS = "same_pass"
UNDECIDED = "undecided"


def identity(row):
    """What makes two rows on one account the same booking.

    Drei Module beantworten diese eine Frage, und jedes fuer eine andere
    Lage. Damit niemand sie verwechselt, hier nebeneinander:

        duplicate_rule.fingerprint   ueber Konten hinweg -- das Konto ist
                                     das, was sich unterscheidet, also
                                     gehoert es NICHT hinein
        rerun_rule.identity          auf einem Konto -- das Konto gehoert
                                     hinein, und der ganze Verwendungszweck
                                     statt seiner ersten achtzig Zeichen:
                                     diese Regel loescht Zeilen, die
                                     nebeneinander stehen, und zwei
                                     Nachbarn, die sich im einundachtzigsten
                                     Zeichen unterscheiden, sind zwei
                                     Buchungen
        booking_fingerprint.canonical  beim Import, gegen die Bank -- ohne
                                     die formatabhaengigen Felder, dafuer
                                     mit IBAN statt Name

    Die drei sind nicht zusammenzulegen: Was eine Buchung ausmacht, haengt
    daran, wogegen verglichen wird. Was sie brauchen, ist ein gemeinsames
    Vokabular, und das ist dieser Absatz.
    """
    return (
        row.get("bank_account"),
        str(row.get("date") or ""),
        str(row.get("withdrawal") or 0),
        str(row.get("deposit") or 0),
        row.get("description") or "",
        row.get("bank_party_name") or "",
        row.get("bank_party_iban") or "",
    )


def seconds_between(earlier, later):
    """The gap between two creation stamps, or None if it cannot be read."""
    try:
        return abs((later - earlier).total_seconds())
    except Exception:
        return None


def surplus_of(copies, seconds_apart=SECONDS_APART):
    """Which of these rows a second pass wrote, and how sure that is.

    Keeps the earliest and offers the rest -- but only when EVERY row
    offered is further from the keeper than the threshold. A group holding
    both a same-pass twin and a later re-run copy is left alone entirely:
    picking the re-run copy out of it would leave a pair behind and would
    rest on an ordering this rule cannot see.

    :param copies: rows of one identity, each with a ``creation``
    :return: (rows to delete, one of FROM_A_RERUN / SAME_PASS / UNDECIDED)
    """
    if len(copies) < 2:
        return [], UNDECIDED

    try:
        ordered = sorted(copies, key=lambda row: row["creation"])
    except Exception:
        return [], UNDECIDED

    keeper, rest = ordered[0], ordered[1:]

    gaps = [seconds_between(keeper["creation"], row["creation"])
            for row in rest]
    if any(gap is None for gap in gaps):
        return [], UNDECIDED

    if all(gap >= seconds_apart for gap in gaps):
        return rest, FROM_A_RERUN

    if all(gap < seconds_apart for gap in gaps):
        # One pass wrote both. The source said so, and this module does not
        # argue with the source.
        return [], SAME_PASS

    # Mixed. Not this rule's to untangle.
    return [], UNDECIDED
