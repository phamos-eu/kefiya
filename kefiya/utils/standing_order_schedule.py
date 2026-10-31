# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""When a standing order runs next. Without frappe, so it runs in a test.

Moved from the instance's before-save script on 30.10.2026, with its rule
unchanged: the next execution always lies ON the schedule and never in the
past. It is the first scheduled date on or after the latest of: the start of
validity, today, and the day after the last date already executed. A date
that was missed is never created backdated -- paying it is a person's
decision, not a scheduler's.

The schedule is anchored on valid_from's month: a quarterly order valid from
February runs in February, May, August and November. Where a month has no
such day (the 31st in April), its last day stands in.
"""

import calendar
import datetime

#: Months between two executions.
STEP = {"Monthly": 1, "Quarterly": 3, "Half-Yearly": 6, "Yearly": 12}

#: How far ahead a date is searched for. 400 steps of a month: decades.
MAX_STEPS = 400


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _shift(year, month, months):
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def day_in(year, month, day_of_month):
    """The execution day in that month: the day itself, or its last day."""
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(int(day_of_month), last))


def on_schedule(candidate, valid_from, day_of_month, frequency):
    """Does ``candidate`` fall on the order's schedule?"""
    candidate, start = _as_date(candidate), _as_date(valid_from)
    if not candidate or not start:
        return False
    step = STEP.get(frequency or "Monthly") or 1
    months = ((candidate.year - start.year) * 12
              + (candidate.month - start.month))
    return (months >= 0 and months % step == 0
            and candidate == day_in(candidate.year, candidate.month,
                                    day_of_month))


def next_execution(valid_from, day_of_month, frequency, today,
                   last_executed_for=None, current=None, valid_until=None):
    """The next date the order runs, or None when its validity is over.

    ``current`` is kept when it is still on the schedule and not before the
    floor -- so a date somebody set by hand on the schedule survives a save.
    """
    start = _as_date(valid_from)
    if not start or not day_of_month:
        return None
    floor = max(start, _as_date(today))
    last = _as_date(last_executed_for)
    if last:
        floor = max(floor, last + datetime.timedelta(days=1))

    found = None
    held = _as_date(current)
    if held and held >= floor and on_schedule(held, start, day_of_month,
                                              frequency):
        found = held
    else:
        step = STEP.get(frequency or "Monthly") or 1
        for k in range(MAX_STEPS):
            year, month = _shift(start.year, start.month, k * step)
            candidate = day_in(year, month, day_of_month)
            if candidate >= floor:
                found = candidate
                break

    until = _as_date(valid_until)
    if found and until and found > until:
        return None
    return found


def iban_is_plausible(iban):
    """Shape only: country letters, check digits, a sensible length."""
    iban = (iban or "").replace(" ", "").upper()
    return (15 <= len(iban) <= 34 and iban[:2].isalpha()
            and iban[2:4].isdigit())


#: TARGET2 closing days that fall on a fixed date, as (month, day). The
#: Easter-based ones are not covered; on those the bank simply executes on
#: the next banking day.
FIXED_HOLIDAYS = ((1, 1), (5, 1), (12, 25), (12, 26))


def is_banking_day(day):
    day = _as_date(day)
    return day.weekday() < 5 and (day.month, day.day) not in FIXED_HOLIDAYS


def split_backwards(amount, due, today, room_on):
    """Spread ``amount`` over banking days, ending on ``due``.

    Walks back from the due date, never before today, taking on each banking
    day what ``room_on(day)`` says is still free under the transfer limit.

    :return: (parts, rest) -- parts as [(day, amount)] in date order, and
        what did not fit. A rest above zero means: place nothing.
    """
    due, today = _as_date(due), _as_date(today)
    remaining = round(float(amount), 2)
    parts = []
    day = due
    while remaining > 0.004 and day >= today:
        if is_banking_day(day):
            room = round(float(room_on(day) or 0), 2)
            if room > 0.004:
                part = round(min(room, remaining), 2)
                parts.append((day, part))
                remaining = round(remaining - part, 2)
        day = day - datetime.timedelta(days=1)
    return sorted(parts), max(remaining, 0.0)
