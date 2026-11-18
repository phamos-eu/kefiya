# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Splitting one payment over days and accounts within the bank's limit.

The rules behind limit_way_out, without frappe, so they run in a test on
real dates. See limit_way_out for why they exist and how they are used.

``room_on(login, day, planned)`` answers what the bank still lets ``login``
send on ``day``, given the parts already ``planned`` -- the same function
partial_payment_plan uses (transfer_limit._room_function). The window logic
(per day, week, month, period) stays with it.
"""

import datetime

from kefiya.utils.standing_order_schedule import is_banking_day

#: Rounding, not leniency.
CENT = 0.005

#: How far ahead a proposal may reach. A payment that needs more banking
#: days than this is not one to plan in a dialog.
MAX_DAYS = 15


def as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def cents_down(value):
    """Round down to the cent: a part must never exceed the room by 0,01."""
    return int(round(float(value or 0) * 100, 6)) / 100.0


def banking_days(today, count):
    """``today`` and the banking days after it, ``count`` in all.

    Today is always first, even on a weekend: what counts against the limit
    is the day an order is sent, and that is when the person sends it.
    """
    day = as_date(today)
    days = [day]
    while len(days) < count:
        day = day + datetime.timedelta(days=1)
        if is_banking_day(day):
            days.append(day)
    return days


def fill(total, logins, days, room_on):
    """Distribute ``total`` over accounts and days, earliest day first.

    Within a day the accounts are used in the order given -- the order's own
    account first. Only when every account is full for a day does the plan
    move to the next one.

    :return: (parts [(day, login, amount)], rest)
    """
    remaining = round(float(total or 0), 2)
    planned = []
    for day in days:
        for login in logins:
            if remaining <= CENT:
                break
            room = cents_down(room_on(login, day, planned))
            if room > CENT:
                part = round(min(room, remaining), 2)
                planned.append((day, login, part))
                remaining = round(remaining - part, 2)
        if remaining <= CENT:
            break
    return planned, max(remaining, 0.0)


def check_table(parts, total, today, allowed, room_on):
    """Is this table a valid way to pay ``total`` within every limit?

    :param parts: [{"date", "kefiya_login", "amount"}] as the person left it
    :param allowed: the accounts this order may be paid from
    :return: (problem or None, detail, clean [(day, login, amount)])
    """
    today = as_date(today)
    clean = []
    for part in parts or []:
        try:
            amount = round(float(part.get("amount") or 0), 2)
        except (TypeError, ValueError):
            return "amount", None, []
        day = as_date(part.get("date"))
        login = part.get("kefiya_login")
        if amount <= 0:
            return "amount", None, []
        if day is None or day < today:
            return "date", None, []
        if login not in allowed:
            return "account", login, []
        clean.append((day, login, amount))
    if len(clean) < 2:
        return "count", None, []
    if abs(sum(p[2] for p in clean) - round(float(total or 0), 2)) > CENT:
        return "sum", None, []

    # In date order, so a window spanning several days sees the earlier
    # parts -- the same way the bank adds them up.
    planned = []
    for part in sorted(clean, key=lambda p: p[0]):
        room = cents_down(room_on(part[1], part[0], planned))
        if part[2] > room + CENT:
            return "limit", (part[0], part[1], room), []
        planned.append(part)
    return None, None, clean
