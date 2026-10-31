# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The dated transfers a bank holds, read out of its answer to HKCSB.

Without frappe and without a bank, like sepa_descriptor: this decides what
ends up in the forecast, and a reader that only runs against a bank is
checked by nobody.

Why it exists. The forecast of bank-held orders was fed by HKDBS -- the dated
DIRECT DEBITS, money an account collects. A payer has none, so the forecast
was empty at every bank, while StarMoney showed the same accounts' dated
transfers without trouble. Those come from HKCSB, which kefiya never asked.

Everything a forecast row needs travels inside the pain.001 the bank sends
back, where the schema fixes its place: the execution date (ReqdExctnDt --
a bare date in .03, wrapped in <Dt> in .09), and per payment the amount,
the recipient, the IBAN and the purpose. One message may carry several
payments, and a collective order may carry several dates.
"""

import re

KIND = "Scheduled Transfer"


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _tag(name):
    """A tag with or without a namespace prefix."""
    return r"(?:\w+:)?" + name


def _blocks(xml, name):
    return re.findall(r"<{0}\b[^>]*>(.*?)</{0}>".format(_tag(name)), xml,
                      re.S)


def _one(xml, *path):
    """The text of the first element along a path of tag names, or ""."""
    part = xml
    for name in path[:-1]:
        found = _blocks(part, name)
        if not found:
            return ""
        part = found[0]
    match = re.search(r"<{0}\b[^>]*>([^<]*)</{0}>".format(_tag(path[-1])),
                      part, re.S)
    return match.group(1).strip() if match else ""


def _date(payment_info):
    """ReqdExctnDt as YYYY-MM-DD: bare in pain.001.001.03, in <Dt> in .09."""
    found = _blocks(payment_info, "ReqdExctnDt")
    if not found:
        return ""
    inner = found[0]
    dated = _one(inner, "Dt") if "Dt" in inner else ""
    value = (dated or inner).strip()
    return value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", value) else ""


def _amount(transaction):
    match = re.search(r"<{0}\b([^>]*)>([^<]+)</{0}>".format(
        _tag("InstdAmt")), transaction)
    if not match:
        return None, None
    try:
        amount = float(match.group(2).strip())
    except ValueError:
        return None, None
    currency = re.search(r'Ccy="([^"]+)"', match.group(1))
    return amount, (currency.group(1) if currency else None)


def payments_in(pain):
    """Every payment in one pain.001, with the date of its payment block.

    :return: list of dicts; a payment without a readable date or amount is
        left out rather than guessed -- see skipped_in()
    """
    xml = _text(pain)
    out = []
    for info in _blocks(xml, "PmtInf"):
        day = _date(info)
        for tx in _blocks(info, "CdtTrfTxInf"):
            amount, currency = _amount(tx)
            out.append({
                "planned_date": day,
                "amount": amount,
                "currency": currency,
                "counterparty_name": _one(tx, "Cdtr", "Nm"),
                "counterparty_iban": _one(tx, "CdtrAcct", "IBAN"),
                "purpose": _one(tx, "RmtInf", "Ustrd"),
                "end_to_end_id": _one(tx, "EndToEndId"),
            })
    return out


def items_from(segments):
    """HICSB segments into forecast items. Skips, never guesses.

    :return: {"items": [...], "skipped": n} in the shape
        refresh_planned_payments takes
    """
    items = []
    skipped = 0
    for segment in segments or []:
        task_id = _text(getattr(segment, "task_id", None)) or None
        for payment in payments_in(getattr(segment, "sepa_pain_message", None)):
            if not payment["planned_date"] or payment["amount"] is None:
                skipped += 1
                continue
            items.append({
                "planned_date": payment["planned_date"],
                "amount": abs(payment["amount"]),
                "direction": "Outgoing",
                "payment_kind": KIND,
                "counterparty_name": payment["counterparty_name"],
                "counterparty_iban": payment["counterparty_iban"],
                "purpose": payment["purpose"],
                "task_id": task_id,
            })
    return {"items": items, "skipped": skipped}
