# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Who is being paid, read back out of the order that was built.

The Verification of Payee answer comes back from the bank while the pain.001
message is the only thing at hand that says who the money is for. That is what
this reads: the payee name and IBAN, out of the very message that was sent, so
a remembered decision is filed against what the bank was actually asked about
and not against a document somebody edited in the meantime.

Deliberately narrow:

    payees_in(xml)      every payee in the message, in order
    the_only_payee(xml) that payee, and only where there is exactly one

A collective order carries many payees. One decision cannot stand for all of
them, so the_only_payee answers None there and the reviewer is asked -- which
is the same answer this module would give if it could not read the message at
all.

No frappe in here: it is parsing, and it is on the payment path, so it has to
be testable without a site.
"""

import xml.etree.ElementTree as ElementTree


def _tag(element):
    """The tag without its namespace.

    pain.001 comes in several schema versions, each with its own namespace URI,
    and the sending bank decides which. Matching on the local name is what
    makes this read all of them.
    """
    tag = element.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def _first(element, path):
    """Follow a chain of local names, or answer None."""
    current = element
    for name in path:
        found = None
        for child in list(current):
            if _tag(child) == name:
                found = child
                break
        if found is None:
            return None
        current = found
    return current


def _text(element):
    return (element.text or "").strip() if element is not None else ""


def payees_in(pain_xml):
    """Every payee in the message as (name, iban), in document order.

    Answers an empty list for anything unparseable. A payment that cannot be
    read is a payment nobody remembers a decision about, which leaves the
    reviewer being asked -- the safe direction.
    """
    if not pain_xml:
        return []
    try:
        root = ElementTree.fromstring(
            pain_xml.encode("utf-8") if isinstance(pain_xml, str)
            else pain_xml)
    except Exception:
        return []

    payees = []
    for element in root.iter():
        if _tag(element) != "CdtTrfTxInf":
            continue
        name = _text(_first(element, ("Cdtr", "Nm")))
        iban = _text(_first(element, ("CdtrAcct", "Id", "IBAN")))
        payees.append((name, iban))
    return payees


def the_only_payee(pain_xml):
    """(name, iban) where the message pays exactly one payee, else None.

    Both halves have to be there. Half a payee is not something a decision can
    be filed under.
    """
    payees = payees_in(pain_xml)
    if len(payees) != 1:
        return None
    name, iban = payees[0]
    if not (name and iban):
        return None
    return name, iban
