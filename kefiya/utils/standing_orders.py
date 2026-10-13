# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Reading what the bank already holds: standing orders and scheduled debits.

Two business transactions that are easy to confuse and were confused here.

  HKDBS  Bestand terminierter SEPA-Einzellastschriften -- money we COLLECT,
         once, on a date. python-fints ships it.
  HKCDB  Bestand SEPA-Dauerauftrag -- money we PAY, again and again, on a
         cycle. python-fints ships nothing for it at all.

The collective log called the first one "Daueraufträge" and never asked for the
second. So the line that claimed to report standing orders reported a different
business transaction entirely -- and reported it as empty, for a reason worth
writing down.

**The library's query cannot return anything.** get_scheduled_debits() asks
response_segments() to filter the bank's answer by ``"HKDBS"`` -- the name of
the *request*. An answer never contains that; it contains HIDBS. The filter
matches nothing, every time, at every bank::

    command_classes = (HKDBS1, HKDBS2)
    response_type = "HKDBS"          # <- HIDBS

So the query is issued here rather than through the library, with the response
type the bank actually sends. Nothing is patched: the library keeps its
behaviour, this module simply does not use the broken path.

For HKCDB the answer is read WITHOUT a declared response segment. The schedule
it carries -- cycle, rhythm, day, first and last run -- is the one part of this
business transaction that could not be checked against anything the library
ships, and a guessed field order that happens to parse is worse than one that
fails: it would put the day of execution where the rhythm belongs and look
entirely plausible. An undeclared segment keeps every element as the bank sent
it, so the certain part is read by position and the rest is kept raw. That raw
part is the evidence that will settle the order -- and only then is it safe to
write a standing order rather than read one.
"""

import re

import frappe
from frappe.utils import flt

#: What the bank calls a cycle. Kept as the raw code as well, because the
#: mapping is only worth as much as the position it was read from.
TIME_UNIT = {"M": "monthly", "W": "weekly"}


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")
        except Exception:
            return ""
    return str(value)


def _pain_summary(payload):
    """Amount, recipient and purpose out of a pain.001 message.

    Deliberately by regular expression and not by an XML parser: the message
    arrives as raw bytes from a bank, the shape varies by pain version, and a
    strict parse that raises would cost the whole fetch. What is missing stays
    empty, which reads as "the bank did not say" -- the truthful answer.
    """
    xml = _text(payload)
    if not xml:
        return {}

    def one(pattern):
        match = re.search(pattern, xml, re.S)
        return (match.group(1).strip() if match else "")

    amount = one(r"<InstdAmt[^>]*>([^<]+)</InstdAmt>")
    return {
        "amount": flt(amount) if amount else 0.0,
        "currency": one(r'<InstdAmt[^>]*Ccy="([^"]+)"') or None,
        "recipient_name": one(r"<Cdtr>.*?<Nm>([^<]+)</Nm>"),
        "recipient_iban": one(r"<CdtrAcct>.*?<IBAN>([^<]+)</IBAN>"),
        "purpose": one(r"<RmtInf>.*?<Ustrd>([^<]+)</Ustrd>"),
        "end_to_end_id": one(r"<EndToEndId>([^<]+)</EndToEndId>"),
    }


def _schedule_from(raw):
    """Best reading of the standing order's schedule -- clearly marked as such.

    The group is reported raw as well. Where the reading below turns out wrong
    against a real bank, the raw values are what says so.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        return {"raw": raw}

    parts = [_text(x) for x in raw]
    out = {"raw": parts, "confirmed": False}
    if parts and parts[0] in TIME_UNIT:
        out["time_unit"] = parts[0]
        out["cycle"] = TIME_UNIT[parts[0]]
    for index, key in ((1, "rhythm"), (2, "execution_day")):
        if len(parts) > index and parts[index].isdigit():
            out[key] = int(parts[index])
    for index, key in ((3, "first_execution"), (4, "last_execution")):
        if len(parts) > index and re.match(r"^\d{8}$", parts[index] or ""):
            value = parts[index]
            out[key] = "{0}-{1}-{2}".format(value[:4], value[4:6], value[6:])
    return out


def parse_standing_order(segment):
    """One HICDB segment into a readable row.

    Only the first three elements are read by position -- account, descriptor,
    pain message -- because only those are certain. Everything after them is
    kept as the bank sent it.
    """
    data = list(getattr(segment, "_additional_data", None) or [])
    row = {"raw_elements": len(data)}

    if len(data) > 2:
        row.update(_pain_summary(data[2]))
    if len(data) > 1:
        row["sepa_descriptor"] = _text(data[1])

    # The identifier and the two flags follow the pain message in every SEPA
    # bestand segment the library ships. Read defensively all the same: a
    # bank that orders them differently must not turn into a wrong schedule.
    tail = data[3:]
    for value in tail:
        if isinstance(value, (list, tuple)):
            row["schedule"] = _schedule_from(value)
            break
    text_tail = [_text(v) for v in tail if not isinstance(v, (list, tuple))]
    if text_tail:
        row["task_id"] = text_tail[0]
    row["flags"] = [v for v in text_tail[1:] if v in ("J", "N")]
    row["cancelable"] = "J" in row["flags"][:1]
    row["changeable"] = "J" in row["flags"][1:2]
    row["tail_raw"] = text_tail
    return row


def fetch_standing_orders(connection, account):
    """Ask the bank which standing orders it holds for this account.

    :return: list of rows from parse_standing_order()
    :raises FinTSUnsupportedOperation: where the bank does not offer HKCDB
    """
    from kefiya.utils.fints_segments import HKCDB1

    def _extract(command_seg, response):
        rows = []
        for segment in response.find_segments("HICDB"):
            try:
                rows.append(parse_standing_order(segment))
            except Exception:
                # One unreadable order must not lose the others.
                frappe.log_error(
                    title="Kefiya: could not read a standing order",
                    message=frappe.get_traceback())
        return rows

    with connection._get_dialog() as dialog:
        hkcdb = connection._find_highest_supported_command(HKCDB1)
        seg = hkcdb(
            account=hkcdb._fields["account"].type.from_sepa_account(account),
        )
        return connection._send_with_possible_retry(dialog, seg, _extract)


def fetch_scheduled_debits(connection, account):
    """The bestand of scheduled direct debits -- HKDBS, asked correctly.

    Same query the library offers, with the response filtered by what a bank
    actually sends. See the module docstring for why the library's own path
    returns nothing.

    :return: list of HIDBS segments
    :raises FinTSUnsupportedOperation: where the bank does not offer HKDBS
    """
    from fints.segments.debit import HKDBS1, HKDBS2

    with connection._get_dialog() as dialog:
        hkdbs = connection._find_highest_supported_command(HKDBS1, HKDBS2)
        return connection._fetch_with_touchdowns(
            dialog,
            lambda touchdown: hkdbs(
                account=hkdbs._fields["account"].type.from_sepa_account(
                    account),
                touchdown_point=touchdown,
            ),
            lambda responses: responses,
            # HIDBS, not HKDBS: the bank answers with its own segment, not
            # with a copy of the request.
            "HIDBS",
        )
