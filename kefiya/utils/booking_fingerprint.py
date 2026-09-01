# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What makes two rows the same booking — and what only looks different.

The import decides "have I seen this before" by hashing five fields. Two
things were wrong with that hash, and both were measured on live data.

**It said different where the bank meant the same.** A bank hands the same
booking over in two formats, and this app reads both: MT940 through
old_kefiya_import, CAMT through kefiya_import. The old hash used

    date, amount, applicant_name, posting_text, purpose

and three of those five come out differently depending on the route:

    posting_text     MT940 'posting_text' against CAMT
                     'AdditionalEntryInformation'. Different fields, often
                     different words for one booking.
    applicant_name   wrapped at a fixed width in MT940, so the live data has
                     "Alexander und Christina Fin keissen" next to
                     "Dr. Alexander Finkeissen und Christina Finkeissen".
    purpose          wrapped the same way -- "Datum 28.02.20 26" is one date
                     with a line break in the middle of the year.

So one booking fetched by both routes produced two hashes and two rows. On
this instance that is 57 pairs, and it is what an accountant reads as a
tenant paying twice.

**And it said same where the bank meant different.** The hash carried no
account. A booking of the same amount, on the same day, with the same purpose
on a SECOND account -- a transfer between two of the company's own accounts
is exactly that -- hashed identically to the first and was skipped. That one
does not show up as a duplicate; it shows up as a payment that is missing.

So: the account goes in, the two route-dependent fields come out, and what is
left is normalised before it is hashed.

WHAT MUST NOT HAPPEN when this changes, and the reason for legacy_forms():
a new hash for an old booking means the next fetch does not recognise it and
imports it again -- turning a fix for 57 duplicates into thousands. Every
booking is therefore looked up under the new hash AND under the old one,
and only written under the new.

No frappe import: this decides whether a payment is entered twice or not at
all, and it is exercised without a site.
"""

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")


def tidy(value):
    """Text as the bank meant it, not as its line width left it.

    Collapses runs of whitespace and folds case. That is the whole trick for
    the fixed-width wrapping: "Fin  keissen" and "Finkeissen" still differ,
    but "Datum 28.02.20 26" and "Datum 28.02.2026" no longer differ by a
    space, and the same purpose line read through two formats lands on the
    same text.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    return _WHITESPACE.sub(" ", str(value)).strip().casefold()


def as_day(value):
    """A date as YYYY-MM-DD, whatever shape it arrived in.

    MT940 hands over a date object, CAMT a string, and the old hash simply
    formatted whatever it got -- so "2026-03-30" and "2026-03-30 00:00:00"
    were two different bookings.
    """
    if value is None:
        return ""
    for attribute in ("isoformat",):
        reader = getattr(value, attribute, None)
        if callable(reader):
            try:
                return reader()[:10]
            except Exception:
                break
    text = str(value).strip()
    return text.replace(".", "-").replace("/", "-")[:10]


def as_money(value):
    """An amount as a plain two-decimal string, sign dropped.

    The direction is not part of the identity here -- it is carried by the
    deposit/withdrawal columns, and reading it out of a float's repr is how
    "50.0" and "50.00" became two bookings.
    """
    try:
        return "{0:.2f}".format(abs(float(value)))
    except (TypeError, ValueError):
        return ""


def canonical(bank_account, date, amount, iban, name, purpose):
    """The identity of one booking: which account, which day, how much,
    whose money, what for.

    The counterparty is the IBAN where the bank gave one and the name only
    where it did not -- an IBAN is the same through every format, a name is
    not.
    """
    counterparty = tidy(iban) or tidy(name)
    return _hash([
        tidy(bank_account),
        as_day(date),
        as_money(amount),
        counterparty,
        tidy(purpose),
    ])


def legacy(date, amount, name, posting_text, purpose):
    """The hash both importers wrote before this module existed.

    ONE function, not two. Both of them built the same string with the same
    format -- they differed only in which parser they took the five values
    from, and that difference lives at the call site, not here. Shipping it
    as legacy_camt() and legacy_mt940() made known_forms() hash the same
    input twice and dedupe the result, so it advertised three forms and
    produced two, and the test that was supposed to prove both were offered
    compared a value with itself.

    Reproduced exactly, formatting included: what it produced is in the
    database, and a booking is only recognised again if this matches to the
    character.
    """
    return _hash_raw("{0},{1},{2},{3},{4}".format(
        date, amount, name, posting_text, purpose))


#: How many hashes a booking can be filed under: the current one and the one
#: the old importers wrote. Asserted rather than described, because the whole
#: promise of this module -- that a fix does not re-import the history -- is
#: the claim that this list is complete.
FORMS = 2


def known_forms(bank_account, date, amount, iban, name, posting_text,
                purpose):
    """Every hash this booking may already be filed under.

    New first, because that is the one that will match from now on.

    :return: FORMS hex digests
    """
    return [
        canonical(bank_account, date, amount, iban, name, purpose),
        legacy(date, amount, name, posting_text, purpose),
    ]


def _hash(parts):
    return _hash_raw("|".join(parts))


def _hash_raw(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()
