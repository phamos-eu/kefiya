# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Asking a bank what it holds, and reading its standing orders' schedule.

Without frappe and without a bank, like scheduled_transfers: this decides
what is asked and what ends up in the forecast, and a reader that only runs
against a bank is checked by nobody.

Why it exists. On 31.10.2026 the first fetch with HKCSB and HKCDB ran over
every account, at a Sparkasse and at a Volksbank alike. The forecast stayed
empty, and the Error Log said nothing. The written-down answer to HKCDB said
why -- at both banks the same two codes::

    9110 Ungueltige Auftragsnachricht: Unbekannter Aufbau.
    9160 Pflichtfeld nicht gefunden

The mandatory field is "Unterstuetzte SEPA pain messages": the query
segments declare it, and nobody filled it. HKCSB was built the same way.
And a 9xxx answer to one segment is not an exception in python-fints -- the
library logs it and hands back the (empty) answer. So "the bank refused the
question" read as "the bank holds nothing", for every account, every run.

Two things follow, and both live here:

  * pain_messages() fills the field from the bank's own list (HISPAS);
  * refusals() reads the bank's verdict on the segment, so the caller can
    raise instead of reporting zero.
"""

import calendar
import datetime
import re

#: The forecast kind of a standing order the bank holds.
KIND = "Bank Standing Order"

#: What every German bank has listed in HISPAS since 2016. Only used when
#: the bank's own list cannot be read: an empty field is known to be refused.
DEFAULT_PAIN_001 = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"

#: The cycle codes of the standing-order details.
TIME_UNIT = {"M": "monthly", "W": "weekly"}

#: Execution days that count back from the end of the month.
ULTIMO = {97: 2, 98: 1, 99: 0}

#: Pages of one answer before the touchdown chain is cut off.
MAX_PAGES = 50

#: Occurrences searched per standing order. Weekly over a month is five.
MAX_STEPS = 400


class BankRefusedQuery(Exception):
    """The bank answered a query segment with a 9xxx code.

    Raised instead of reporting an empty bestand: "the bank refused the
    question" and "the bank holds nothing" must not read the same.
    """

    def __init__(self, segment_type, said):
        self.segment_type = segment_type
        self.said = list(said or [])
        super().__init__("{0} refused by the bank: {1}".format(
            segment_type, "; ".join(self.said)))


def pain_messages(supported, family="pain.001", default=DEFAULT_PAIN_001):
    """The pain formats to name in a bestand query: the bank's own, of the
    right family, in its order. Never empty.

    :param supported: format names as sepa_descriptor.formats_in() reads them
        from HISPAS
    :param family: "pain.001" for transfers, "pain.008" for debits
    """
    names = []
    for name in supported or []:
        name = str(name or "").strip()
        if family in name and name not in names:
            names.append(name)
    return names or [default]


def refusals(response, command_seg):
    """What the bank said against this segment: its 9xxx codes, with text.

    Only the answers that refer to the segment. Never raises -- a response
    that cannot be read refuses nothing.
    """
    try:
        answers = list(response.responses(command_seg))
    except Exception:
        return []
    said = []
    for answer in answers:
        code = str(getattr(answer, "code", "") or "")
        if code.startswith("9"):
            said.append("{0} {1}".format(
                code, getattr(answer, "text", "") or "").strip())
    return said


def touchdown_of(response, command_seg):
    """The bank's touchdown point (3040) for the next page, or None."""
    try:
        for answer in response.responses(command_seg, "3040"):
            parameters = getattr(answer, "parameters", None) or []
            return parameters[0] if parameters else None
    except Exception:
        return None
    return None


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _iso(value):
    return "{0}-{1}-{2}".format(value[:4], value[4:6], value[6:8])


def schedule_from(raw):
    """The standing order's schedule, read by the shape of each value.

    The details group carries the first execution, the cycle (M/W), the
    rhythm, the execution day and an optional last execution. Their order
    could not be checked against anything the library ships, so nothing is
    read by position: a date is eight digits, a cycle is M or W, and of the
    small numbers after the cycle the first is the rhythm and the second the
    day. That holds whichever way round the dates and the cycle come.

    The group is kept raw as well, and marked unconfirmed. Where the reading
    turns out wrong against a real bank, the raw values are what says so.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        return {"raw": raw, "confirmed": False}

    parts = [_text(x).strip() for x in raw]
    out = {"raw": parts, "confirmed": False}

    dates = [p for p in parts if re.match(r"^\d{8}$", p)]
    if dates:
        out["first_execution"] = _iso(dates[0])
    if len(dates) > 1:
        out["last_execution"] = _iso(dates[1])

    units = [i for i, p in enumerate(parts) if p in TIME_UNIT]
    if units:
        unit = parts[units[0]]
        out["time_unit"] = unit
        out["cycle"] = TIME_UNIT[unit]
        small = [int(p) for p in parts[units[0] + 1:]
                 if p.isdigit() and len(p) <= 2]
        if small:
            out["rhythm"] = small[0]
        if len(small) > 1:
            out["execution_day"] = small[1]
    return out


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _shift(year, month, months):
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def _month_day(year, month, day):
    """The execution day in that month. 97/98/99 count back from its end;
    a day the month does not have becomes its last."""
    last = calendar.monthrange(year, month)[1]
    if day in ULTIMO:
        return datetime.date(year, month, last - ULTIMO[day])
    return datetime.date(year, month, min(day, last))


def horizon_end(today):
    """One month ahead -- the rolling preview the forecast holds."""
    year, month = _shift(today.year, today.month, 1)
    return _month_day(year, month, today.day)


def is_readable(schedule):
    """Is every part there that an execution date needs?

    A guessed date in a liquidity forecast is worse than a missing one.
    """
    schedule = schedule or {}
    unit = schedule.get("time_unit")
    rhythm = schedule.get("rhythm")
    day = schedule.get("execution_day")
    if not _as_date(schedule.get("first_execution")):
        return False
    if unit not in TIME_UNIT or not rhythm or not day:
        return False
    if unit == "M":
        return 1 <= day <= 31 or day in ULTIMO
    return 1 <= day <= 7


def occurrences(schedule, today):
    """The executions of a standing order from today up to one month ahead.

    :return: list of datetime.date; empty where the schedule is not
        readable, or where no execution falls into the month
    """
    if not is_readable(schedule):
        return []
    first = _as_date(schedule.get("first_execution"))
    last = _as_date(schedule.get("last_execution"))
    unit = schedule.get("time_unit")
    rhythm = schedule.get("rhythm")
    day = schedule.get("execution_day")

    today = _as_date(today)
    end = horizon_end(today)
    if last and last < end:
        end = last

    found = []
    if unit == "M":
        for step in range(MAX_STEPS):
            year, month = _shift(first.year, first.month, step * rhythm)
            current = _month_day(year, month, day)
            if current > end:
                break
            if current >= first and current >= today:
                found.append(current)
    else:
        current = first + datetime.timedelta(
            days=(day - first.isoweekday()) % 7)
        for _ in range(MAX_STEPS):
            if current > end:
                break
            if current >= today:
                found.append(current)
            current += datetime.timedelta(weeks=rhythm)
    return found


def items_from(rows, today):
    """Standing orders read from HICDB into forecast items. Skips what it
    cannot read, never guesses.

    :param rows: rows from standing_orders.parse_standing_order()
    :return: {"items": [...], "skipped": n} in the shape
        refresh_planned_payments takes
    """
    items = []
    skipped = 0
    for row in rows or []:
        row = row or {}
        amount = abs(float(row.get("amount") or 0))
        if not amount or not is_readable(row.get("schedule")):
            skipped += 1
            continue
        # A quarterly order outside this month is not skipped -- it simply
        # has nothing to show yet.
        for day in occurrences(row.get("schedule"), today):
            items.append({
                "planned_date": day.isoformat(),
                "amount": amount,
                "direction": "Outgoing",
                "payment_kind": KIND,
                "counterparty_name": row.get("recipient_name") or None,
                "counterparty_iban": row.get("recipient_iban") or None,
                "purpose": row.get("purpose") or None,
                "task_id": row.get("task_id") or None,
            })
    return {"items": items, "skipped": skipped}
