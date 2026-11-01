# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A camt.053 statement FILE, read into the entries every other file becomes.

Not the FinTS path. A bank that answers HKCAZ hands python-fints camt
documents, and fints_controller unpacks those with the library's own parser.
This module is for the statement that arrives as a file -- downloaded from an
online banking portal or delivered by a bank that is not reachable over FinTS
at all, which is every bank outside Germany. ISO 20022 is the one format such
a bank and this app have in common.

Pure, like statement_formats: no frappe, no fints, only the standard library.
What a statement says has to be checkable without a site.

Read, not guessed. In particular:

* **Only booked entries.** ``Sts`` BOOK is a booking; PDNG and INFO are not,
  and a pending entry booked as final is the duplicate of the real one that
  follows next week. They are counted, not read.
* **The booking day, not the value date.** ``BookgDt``, as everywhere else in
  this app (see CLAUDE.md). An entry without one is unreadable -- the value
  date is a different day often enough that taking it would move bookings
  between months.
* **The sign is the indicator's.** ``CdtDbtInd`` says which way the money
  went, and for a reversal (``RvslInd``) it already says the reversed way: a
  returned debit is a CRDT. The amount itself is unsigned.
* **The account is the statement's own.** ``Acct/Id/IBAN``, checksum and all.
  An IBAN that fails its check digits addresses nothing.
* **The bank's reference is the identity.** ``AcctSvcRef`` is the bank's
  unique reference of the entry; a second import of the same statement --
  or of an overlapping one -- recognises the entry by it.
* **The currency is stated, not assumed.** Every entry carries the currency
  of its amount; whether the Bank Account books in that currency is decided
  where the Bank Account is known (statement_import).
* **An exchange rate is taken only where it is unambiguous.** See
  ``exchange_rate_of``.

A batch entry -- one ``Ntry`` with several transaction details, as Swiss
banks book QR-bill credits -- becomes one booking per detail, so each can be
reconciled against its own invoice. Only where the details account for every
cent of the entry; otherwise it stays one booking, as the bank booked it,
with its details in the description.
"""

import re
import xml.etree.ElementTree as ElementTree

from kefiya.utils.statement_formats import iban_checksum_ok, parse_date

#: The message versions this reader is written and tested for. The structure
#: below is the same across them where it matters here; where it is not -- the
#: status became a code in .08, the party name moved under ``Pty`` -- both
#: forms are read. A version outside this set is refused, not attempted.
SUPPORTED_VERSIONS = ("02", "03", "04", "05", "06", "07", "08", "09", "10",
                      "11")

_NAMESPACE = re.compile(
    r"urn:iso:std:iso:20022:tech:xsd:camt\.(\d{3})\.001\.(\d{2})")

#: A statement with an internal DTD is not a bank statement but an attack on
#: the parser (entity expansion). No bank needs one.
_DTD = re.compile(br"<!\s*(DOCTYPE|ENTITY)", re.I)

#: What a rate read from the file may differ from the rate its own amounts
#: imply, before it is considered not to be about these amounts. Rounding to
#: the cent leaves far less than this; a rate quoted the wrong way round
#: leaves far more.
RATE_TOLERANCE = 0.01


class CamtError(ValueError):
    """The file is camt, but not one this reader will book from."""


# --------------------------------------------------------------------------
# Recognising the file
# --------------------------------------------------------------------------

def message_kind(head):
    """("053", "08") for a camt.053.001.08 document, else None.

    :param head: the beginning of the file, bytes or text
    """
    if isinstance(head, (bytes, bytearray)):
        head = bytes(head[:4000]).decode("utf-8", "replace")
    found = _NAMESPACE.search(str(head or "")[:4000])
    return found.groups() if found else None


def looks_like_camt(head):
    """Is this an ISO 20022 cash management message at all?"""
    return message_kind(head) is not None


# --------------------------------------------------------------------------
# Small readers -- namespace-blind, because the namespace is the version
# --------------------------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child(node, *path):
    """The first element down ``path``, ignoring namespaces, or None."""
    for name in path:
        if node is None:
            return None
        found = None
        for candidate in node:
            if _local(candidate.tag) == name:
                found = candidate
                break
        node = found
    return node


def _children(node, name):
    if node is None:
        return []
    return [c for c in node if _local(c.tag) == name]


def _text(node, *path):
    found = _child(node, *path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _amount(node):
    """(amount, currency) of an ``Amt`` element, or (None, None)."""
    if node is None or not (node.text or "").strip():
        return None, None
    try:
        value = float(node.text.strip())
    except ValueError:
        return None, None
    return value, (node.get("Ccy") or "").strip().upper() or None


def _party_name(party):
    """``Nm`` up to .07, ``Pty/Nm`` from .08 on."""
    return _text(party, "Nm") or _text(party, "Pty", "Nm")


def _iban(account):
    value = _text(account, "Id", "IBAN").replace(" ", "").upper()
    return value or None


# --------------------------------------------------------------------------
# Exchange rates
# --------------------------------------------------------------------------

def normalise_rate(source, target, unit, rate, amounts=None):
    """An exchange rate as ``{"from", "to", "rate"}``: 1 from = rate to.

    ISO 20022 states a rate with its unit currency -- ``UnitCcy`` GBP and
    rate r mean 1 GBP = r of the other one. Banks leave the unit out often
    enough, and then the same rate means one thing at one bank and its
    inverse at the next. So without a unit the entry's own amounts decide:
    the rate is taken in the direction in which it turns one amount into the
    other, within RATE_TOLERANCE. If neither direction does, there is no rate
    -- the system's own exchange rate applies, which is what would have
    happened without the file.

    :param amounts: {currency: amount} as stated in the same entry
    :return: dict, or None when the rate cannot be taken
    """
    source = (source or "").strip().upper()
    target = (target or "").strip().upper()
    unit = (unit or "").strip().upper()
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return None
    if not source or not target or source == target or rate <= 0:
        return None

    if unit == source:
        return {"from": source, "to": target, "rate": rate}
    if unit == target:
        return {"from": target, "to": source, "rate": rate}
    if unit:
        # A unit that is neither of the two: not a rate between these.
        return None

    amounts = amounts or {}
    in_source = abs(amounts.get(source) or 0)
    in_target = abs(amounts.get(target) or 0)
    if not in_source or not in_target:
        return None
    implied = in_target / in_source            # target per source
    if abs(rate - implied) <= RATE_TOLERANCE * implied:
        return {"from": source, "to": target, "rate": rate}
    if abs(1 / rate - implied) <= RATE_TOLERANCE * implied:
        return {"from": target, "to": source, "rate": rate}
    return None


def rate_between(fx, from_currency, to_currency):
    """The rate of ``fx`` for 1 from_currency in to_currency, or None.

    :param fx: a normalise_rate() result, or anything with the same keys
    """
    if not fx or not fx.get("rate"):
        return None
    try:
        rate = float(fx["rate"])
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    if fx.get("from") == from_currency and fx.get("to") == to_currency:
        return rate
    if fx.get("from") == to_currency and fx.get("to") == from_currency:
        return 1 / rate
    return None


def exchange_rate_of(amount_details):
    """The rate an ``AmtDtls`` element states, or None.

    The rate sits under one of the amounts (instructed, transaction, counter
    value); the amounts around it are what normalise_rate() checks a rate
    without a unit against. More than one differing rate is no rate.
    """
    if amount_details is None:
        return None
    amounts = {}
    exchanges = []
    for part in amount_details:
        value, currency = _amount(_child(part, "Amt"))
        if value is not None and currency:
            amounts.setdefault(currency, value)
        exchange = _child(part, "CcyXchg")
        if exchange is not None:
            exchanges.append(exchange)

    found = []
    for exchange in exchanges:
        fx = normalise_rate(_text(exchange, "SrcCcy"),
                            _text(exchange, "TrgtCcy"),
                            _text(exchange, "UnitCcy"),
                            _text(exchange, "XchgRate"), amounts)
        if fx and fx not in found:
            found.append(fx)
    return found[0] if len(found) == 1 else None


# --------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------

def _status(entry):
    """BOOK, PDNG, INFO -- plain text up to .07, a code from .08 on."""
    return (_text(entry, "Sts", "Cd") or _text(entry, "Sts")).upper()


def _booking_day(entry):
    return parse_date(_text(entry, "BookgDt", "Dt")
                      or _text(entry, "BookgDt", "DtTm")[:10])


def _details_text(details, credit):
    """Counterparty name, IBAN and purpose of one ``TxDtls``."""
    parties = _child(details, "RltdPties")
    first, second = ("Dbtr", "Cdtr") if credit else ("Cdtr", "Dbtr")
    name = _party_name(_child(parties, first))
    iban = _iban(_child(parties, first + "Acct"))
    if not name and not iban:
        # A reversal can name the parties of the original movement.
        name = _party_name(_child(parties, second))
        iban = _iban(_child(parties, second + "Acct"))

    remittance = _child(details, "RmtInf")
    purpose = [n.text.strip() for n in _children(remittance, "Ustrd")
               if n.text and n.text.strip()]
    for structured in _children(remittance, "Strd"):
        reference = _text(structured, "CdtrRefInf", "Ref")
        if reference:
            # QR reference or creditor reference: what a Swiss QR bill or a
            # SEPA payment is reconciled by.
            purpose.append("Ref " + reference)
    if not purpose and _text(details, "AddtlTxInf"):
        purpose.append(_text(details, "AddtlTxInf"))
    return name, iban, " ".join(purpose)


def _details_amount(details, currency):
    """The amount of one ``TxDtls`` in the account's currency, or None.

    ``TxDtls/Amt`` where the version has it, else the transaction amount
    under ``AmtDtls``. The instructed amount is not it: that is what the
    payer sent, possibly in another currency, before the bank converted it.
    """
    for path in (("Amt",), ("AmtDtls", "TxAmt", "Amt")):
        value, unit = _amount(_child(details, *path))
        if value is not None and (unit or currency) == currency:
            return value
    return None


def _split(details, signed, currency, credit):
    """The single bookings of a batch entry, or None where it cannot be split.

    Split only where the file accounts for every cent: each detail names its
    amount in the account's currency, and together they are exactly the
    entry. A detail without an amount, or a sum that does not come out, means
    the details are information about the entry, not its parts -- and a
    booking split on a guess is a balance that no longer matches the bank.

    :return: list of (signed amount, detail) or None
    """
    parts = []
    for one in details:
        value = _details_amount(one, currency)
        if value is None:
            return None
        indicator = _text(one, "CdtDbtInd").upper()
        if indicator == "CRDT":
            part = abs(value)
        elif indicator == "DBIT":
            part = -abs(value)
        else:
            # Older versions name the direction only on the entry.
            part = abs(value) if credit else -abs(value)
        parts.append((part, one))
    cents = sum(int(round(part * 100)) for part, _ in parts)
    if cents != int(round(signed * 100)):
        return None
    return parts


def _part_references(entry_reference, details):
    """A stable reference for each part of a split batch.

    The details' own ``AcctSvcRef`` where every one has one and they are
    distinct -- some banks copy the entry's reference onto every detail, and
    then it identifies nothing. Otherwise the entry's reference with the
    part's position, which is the same on every import of the same file.
    """
    own = [_text(one, "Refs", "AcctSvcRef") for one in details]
    if all(own) and len(set(own)) == len(own) \
            and entry_reference not in own:
        return own
    if entry_reference:
        return ["{0}#{1}".format(entry_reference, k)
                for k in range(1, len(details) + 1)]
    return [None] * len(details)


def _entry(node, own_iban, account_currency):
    """The canonical entries out of one ``Ntry``.

    One, as the bank booked it -- or, for a batch whose details account for
    every cent of it, one per detail (see _split).

    :return: (entries, None, split) or ([], reason, False) -- reason one of
        "pending", "unreadable"; split says whether a batch was taken apart
    """
    if _status(node) != "BOOK":
        return [], "pending", False

    amount, currency = _amount(_child(node, "Amt"))
    indicator = _text(node, "CdtDbtInd").upper()
    day = _booking_day(node)
    if amount is None or indicator not in ("CRDT", "DBIT") or day is None:
        return [], "unreadable", False
    if account_currency and currency and currency != account_currency:
        # Ntry/Amt is in the account's currency by definition. A file that
        # says otherwise is not read as if it agreed with itself.
        return [], "unreadable", False

    currency = currency or account_currency or None
    credit = indicator == "CRDT"
    signed = abs(amount) if credit else -abs(amount)
    posting_text = _text(node, "AddtlNtryInf")
    reference = _text(node, "AcctSvcRef")
    entry_fx = exchange_rate_of(_child(node, "AmtDtls"))

    details = []
    for block in _children(node, "NtryDtls"):
        details.extend(_children(block, "TxDtls"))

    def booking(part_amount, detail, part_reference, fx):
        counterparty, iban, purpose = ("", None, "")
        if detail is not None:
            counterparty, iban, purpose = _details_text(
                detail, part_amount > 0)
        return {
            "date": day,
            "amount": part_amount,
            "description": " ".join(p for p in (posting_text, purpose) if p),
            "counterparty": counterparty,
            "iban": iban,
            "own_iban": own_iban,
            "reference": part_reference or None,
            "currency": currency,
            "fx": fx,
        }

    if len(details) > 1:
        parts = _split(details, signed, currency, credit)
        if parts:
            references = _part_references(reference, details)
            return [booking(part, one, ref,
                            exchange_rate_of(_child(one, "AmtDtls"))
                            or entry_fx)
                    for (part, one), ref in zip(parts, references)], \
                None, True

        # Kept as one booking: its details go into the description.
        pieces = []
        for one in details:
            name, text = _details_text(one, credit)[0::2]
            value = _details_amount(one, currency)
            piece = " ".join(p for p in (
                name, text,
                "{0:.2f} {1}".format(value, currency or "") if value else "")
                if p)
            if piece:
                pieces.append(piece.strip())
        entry = booking(signed, None, reference, entry_fx)
        entry["description"] = " ".join(
            p for p in (posting_text, "; ".join(pieces)) if p)
        return [entry], None, False

    detail = details[0] if details else None
    if not reference and detail is not None:
        reference = _text(detail, "Refs", "AcctSvcRef")
    fx = entry_fx
    if fx is None and detail is not None:
        fx = exchange_rate_of(_child(detail, "AmtDtls"))
    return [booking(signed, detail, reference, fx)], None, False


def read_statement(raw):
    """Every booked entry of a camt.053 file.

    :param raw: the file's bytes (or text)
    :return: (entries, notes) -- notes counts what was not read and why:
        {"format", "version", "unreadable", "pending", "repeated",
         "accounts_without_iban", "unaddressable", "batches_split"}
    :raises CamtError: for a camt file this reader does not book from
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    raw = bytes(raw or b"")
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]

    kind = message_kind(raw)
    if kind is None:
        raise CamtError("not an ISO 20022 camt message")
    message, version = kind
    if message != "053":
        # camt.052 is an intraday report, camt.054 a notification: neither is
        # the statement the bank stands by, and both repeat what the 053
        # will say again.
        raise CamtError("camt.{0} is not a statement (camt.053)".format(
            message))
    if version not in SUPPORTED_VERSIONS:
        raise CamtError("camt.053.001.{0} is not a supported version".format(
            version))
    if _DTD.search(raw):
        raise CamtError("a statement with a document type definition is "
                        "not read")

    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise CamtError("not well-formed XML: {0}".format(error))

    notes = {"format": "camt053", "version": "camt.053.001." + version,
             "unreadable": 0, "pending": 0, "repeated": 0,
             "accounts_without_iban": 0, "unaddressable": 0,
             "batches_split": 0}
    statement_root = _child(root, "BkToCstmrStmt")
    if statement_root is None:
        raise CamtError("no BkToCstmrStmt in this document")

    entries = []
    seen = set()
    without_iban = set()
    for statement in _children(statement_root, "Stmt"):
        account = _child(statement, "Acct")
        own_iban = _iban(account)
        if own_iban and not iban_checksum_ok(own_iban):
            own_iban = None
        if not own_iban:
            # Without a valid IBAN the statement addresses no Bank Account
            # by itself. Its entries are still read; statement_import files
            # them only where the caller named the account -- and only if
            # it is the ONE such account in the file, see below.
            without_iban.add(_text(account, "Id", "Othr", "Id")
                             or _text(account, "Id", "IBAN") or "?")
        account_currency = _text(account, "Ccy").upper() or None

        for node in _children(statement, "Ntry"):
            found, reason, split = _entry(node, own_iban, account_currency)
            if reason:
                notes[reason] += 1
                continue
            if split:
                notes["batches_split"] += 1
            for entry in found:
                # The same entry in two overlapping statements of one file
                # is one booking. Only by the bank's reference: two entries
                # that merely look alike are two bookings (wiederholung).
                if entry["reference"]:
                    key = (own_iban, entry["reference"])
                    if key in seen:
                        notes["repeated"] += 1
                        continue
                    seen.add(key)
                entries.append(entry)

    notes["accounts_without_iban"] = len(without_iban)
    if len(without_iban) > 1:
        # Two accounts that name no IBAN would both land on the one account
        # chosen on the form. Neither is filed rather than both wrongly.
        kept = [e for e in entries if e["own_iban"]]
        notes["unaddressable"] = len(entries) - len(kept)
        entries = kept

    return entries, notes
