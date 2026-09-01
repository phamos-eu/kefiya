# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Is this the payee we think it is? Asked when the order is entered.

The bank's own Verification of Payee happens at the bank, on submission, and
cannot happen earlier: in FinTS the check is part of the transfer message, so
asking it means sending the order. That is too late for the way the work is
divided here -- somebody enters the order, and somebody else, who did not see
the invoice, sends it. By then the data has to be checked already.

So this is the check that CAN be made at entry, and it answers the question
that actually matters for the person signing off:

    Have we paid this IBAN before, and did it belong to this name?

That is not the bank's question, and it is deliberately not sold as one. It is
the question that catches invoice manipulation -- the letterhead is right, the
name is right, the IBAN has been swapped -- which is the fraud a managing
director is being asked to authorise, and the one the bank's name/IBAN match
would not flag at all, because the swapped IBAN really does belong to somebody
with a plausible name.

Four answers, and the order of severity is not the order you would guess:

    known           this IBAN was paid before under this name.
    other_iban      we know this payee, and we have always paid a DIFFERENT
                    IBAN. The loudest of the four: the invoice was changed.
    name_differs    this IBAN is known, under another name. Also loud -- it is
                    the same swap seen from the other side.
    new             neither the IBAN nor the name has been seen. Not an alarm,
                    a fact: every payee is new once, and it says a second pair
                    of eyes is worth having.

The history is our own: IBANs the bank accepted from us before, and the
counterparties of transactions that were actually booked. No third party is
asked, nothing leaves the house.
"""

import re

import frappe
from frappe.utils import cint

#: Legal forms and decorations that say nothing about identity. Two invoices
#: from one company write the name three ways; a check that trips over "GmbH"
#: versus "GmbH & Co. KG" is a check nobody reads after the first week.
NOISE = {
    "GMBH", "AG", "KG", "CO", "OHG", "GBR", "UG", "EK", "EV", "SE", "MBH",
    "COKG", "GMBHCOKG", "HAFTUNGSBESCHRAENKT", "UND", "AND", "THE",
    "HERR", "FRAU", "DR", "PROF", "DIPLING",
}

_NOT_A_LETTER = re.compile(r"[^A-Z0-9ÄÖÜß]+")

VERDICT_KNOWN = "known"
VERDICT_OTHER_IBAN = "other_iban"
VERDICT_NAME_DIFFERS = "name_differs"
VERDICT_NEW = "new"

#: The ones a human has to look at before the money leaves.
LOUD = (VERDICT_OTHER_IBAN, VERDICT_NAME_DIFFERS)


def normalise_name(value):
    """A name reduced to what identifies it.

    Case, punctuation and legal forms come off; what is left is compared. The
    umlauts stay as they are -- "Müller" and "Mueller" are a real question, and
    folding them together here would answer it silently in the wrong direction.
    """
    text = _NOT_A_LETTER.sub(" ", str(value or "").upper())
    return tuple(w for w in text.split() if w and w not in NOISE)


def names_match(left, right):
    """How two payee names relate: "exact", "close" or "different".

    "close" is for the case that fills the history: one invoice says
    "Sofienstraße GmbH & Co. KG", the next "Sofienstrasse GmbH", the bank
    statement "SOFIENSTRASSE GMBH CO KG". All three are the same payee, and a
    check that calls that a mismatch is a check that gets switched off.

    A subset counts as close; an overlap of one word out of five does not.
    """
    a, b = set(normalise_name(left)), set(normalise_name(right))
    if not a or not b:
        return "different"
    if a == b:
        return "exact"
    if a <= b or b <= a:
        return "close"
    return "different"


def clean_iban(value):
    return str(value or "").replace(" ", "").replace("-", "").upper()


def paid_before(iban, limit=200):
    """Every name this IBAN has been paid under, out of our own history.

    Two sources, and both are evidence of a different kind. A transfer WE sent
    is an IBAN the bank accepted from us; a booked Bank Transaction is money
    that actually arrived somewhere. Neither is a bank's confirmation of the
    account holder -- and this module does not pretend otherwise.

    :return: list of {"name", "source", "on"}
    """
    iban = clean_iban(iban)
    if not iban:
        return []

    seen = []
    for row in frappe.get_all(
            "Kefiya Transfer Item",
            filters={"recipient_iban": iban, "docstatus": 1},
            fields=["recipient_name", "parent", "modified"],
            order_by="modified desc", limit_page_length=limit):
        seen.append({"name": row.get("recipient_name"), "source": "transfer",
                     "reference": row.get("parent"), "on": row.get("modified")})

    for row in frappe.get_all(
            "Bank Transaction",
            filters={"bank_party_iban": iban, "withdrawal": [">", 0]},
            fields=["bank_party_name", "name", "date"],
            order_by="date desc", limit_page_length=limit):
        seen.append({"name": row.get("bank_party_name"), "source": "booking",
                     "reference": row.get("name"), "on": row.get("date")})

    return seen


def ibans_of(name, limit=200):
    """Every IBAN we have paid a payee of this name, newest first."""
    words = normalise_name(name)
    if not words:
        return []

    # A LIKE on the longest word, then matched properly in Python: the
    # database cannot do the name comparison this module defines, and asking
    # it to do half of it in SQL is how the two rules drift apart.
    longest = max(words, key=len)
    found = {}
    for row in frappe.get_all(
            "Kefiya Transfer Item",
            filters={"recipient_name": ["like", "%" + longest + "%"],
                     "docstatus": 1},
            fields=["recipient_name", "recipient_iban", "modified"],
            order_by="modified desc", limit_page_length=limit):
        if names_match(name, row.get("recipient_name")) != "different":
            found.setdefault(clean_iban(row.get("recipient_iban")),
                             {"iban": clean_iban(row.get("recipient_iban")),
                              "name": row.get("recipient_name"),
                              "source": "transfer", "on": row.get("modified")})

    for row in frappe.get_all(
            "Bank Transaction",
            filters={"bank_party_name": ["like", "%" + longest + "%"],
                     "withdrawal": [">", 0],
                     "bank_party_iban": ["is", "set"]},
            fields=["bank_party_name", "bank_party_iban", "date"],
            order_by="date desc", limit_page_length=limit):
        if names_match(name, row.get("bank_party_name")) != "different":
            found.setdefault(clean_iban(row.get("bank_party_iban")),
                             {"iban": clean_iban(row.get("bank_party_iban")),
                              "name": row.get("bank_party_name"),
                              "source": "booking", "on": row.get("date")})

    return [v for v in found.values() if v["iban"]]


def verdict_for(name, iban, history=None, other=None):
    """The verdict, out of the two histories. No database access.

    Split out so the rule can be tested for what it decides rather than for
    what it queries -- the deciding is the part that has to be right.

    :param history: what paid_before(iban) returned
    :param other: what ibans_of(name) returned
    """
    iban = clean_iban(iban)
    history = history or []
    other = [o for o in (other or []) if o.get("iban") != iban]

    if history:
        best = "different"
        for entry in history:
            relation = names_match(name, entry.get("name"))
            if relation == "exact":
                best = "exact"
                break
            if relation == "close":
                best = "close"
        if best in ("exact", "close"):
            return VERDICT_KNOWN
        return VERDICT_NAME_DIFFERS

    # The IBAN is new. Whether that is ordinary or alarming depends entirely
    # on whether we know the payee under another one.
    if other:
        return VERDICT_OTHER_IBAN
    return VERDICT_NEW


def check(name, iban):
    """The payee check for one recipient.

    :return: {"verdict", "known_as": [names], "other_ibans": [ibans]}
    """
    history = paid_before(iban)
    other = ibans_of(name)
    return {
        "verdict": verdict_for(name, iban, history, other),
        "iban": clean_iban(iban),
        "name": name,
        "known_as": _distinct(entry.get("name") for entry in history),
        "other_ibans": [o["iban"] for o in other
                        if o["iban"] != clean_iban(iban)][:5],
    }


def _distinct(values):
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out[:5]


@frappe.whitelist()
def check_payee(name, iban):
    """Check one payee against our own payment history.

    Gated on the right to create a transfer: the answer names IBANs and payees
    this company has paid, which is not something a reader without that right
    should be able to enumerate one guess at a time.
    """
    frappe.has_permission("Kefiya Transfer", ptype="create", throw=True)
    return check(name, iban)


#: How many rows are scanned per source. Not how many payees come back: the
#: answer is deduplicated names, and the newest 400 withdrawals of this
#: instance turned out to be 127 payees -- a suggestion list that stops at the
#: last few months is one people give up on. Two indexed reads of a few
#: thousand rows, once per page.
PAYEE_SCAN = 2000


@frappe.whitelist()
def known_payees(limit=PAYEE_SCAN):
    """Everyone we have paid, with the IBANs we paid them at.

    For the suggestion list while a transfer is typed. It exists here, in the
    app, because the entry form used to ask a Server Script stored on one site
    for this -- so the form worked on that site and nowhere else, and nothing
    in the app's own tests could see the dependency.

    Same two sources as the check itself, so what is suggested and what is
    later verified cannot disagree: IBANs the bank accepted from us, and
    counterparties of transactions that were actually booked.

    :param limit: rows scanned per source, not payees returned
    :return: [{"name", "ibans": [{"iban", "source"}]}] newest payee first
    """
    frappe.has_permission("Kefiya Transfer", ptype="create", throw=True)

    # Whitelisted, so `limit` arrives from the browser and may be anything.
    # cint turns "abc" into 0 rather than into a 500, and the cap keeps a
    # crafted limit=100000000 from turning a suggestion list into two
    # unbounded ordered scans on demand.
    limit = min(cint(limit) or PAYEE_SCAN, PAYEE_SCAN)
    payees = {}

    def remember(name, iban, source):
        iban = clean_iban(iban)
        if not name or not iban:
            return
        key = normalise_name(name)
        if not key:
            return
        entry = payees.setdefault(key, {"name": name, "ibans": []})
        if not any(i["iban"] == iban for i in entry["ibans"]):
            entry["ibans"].append({"iban": iban, "source": source})

    for row in frappe.get_all(
            "Kefiya Transfer Item", filters={"docstatus": 1},
            fields=["recipient_name", "recipient_iban"],
            order_by="modified desc", limit_page_length=limit):
        remember(row.get("recipient_name"), row.get("recipient_iban"),
                 "transfer")

    for row in frappe.get_all(
            "Bank Transaction",
            filters={"withdrawal": [">", 0], "bank_party_iban": ["is", "set"]},
            fields=["bank_party_name", "bank_party_iban"],
            order_by="date desc", limit_page_length=limit):
        remember(row.get("bank_party_name"), row.get("bank_party_iban"),
                 "booking")

    return list(payees.values())
