# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""How one payment is paid in parts when one account's limit is too small.

Without frappe, so the rule that decides what leaves which account on which
day runs in a test on real dates.

The rule, as agreed on 01.11.2026:

* Backwards to the due date. The last part is paid ON the date the transfer
  carries, earlier parts on the banking days before it -- never before today.
  Paying the whole amount by its date is the point; paying it earlier only
  because the limit is small would be the planner's decision, not the user's.
* Accounts before days. On each banking day the accounts are used in the
  order given -- the transfer's own account first, then the ones the user
  ticked -- before the plan moves to the day before. More accounts mean the
  money stays on them longer, fewer parts mean fewer TANs.
* All or nothing. When the amount does not fit between today and the due
  date, nothing is planned: a payment half placed is a payment that looks
  made and is not. The caller says how much did not fit.

``room_on(account, day, planned)`` answers what the bank still lets
``account`` send on ``day``, given the parts already ``planned`` in this run.
The window logic (per day, week, month, period) lives with the caller, which
knows the limit type; a window that spans several days sees the earlier
parts through ``planned``.
"""

import datetime

from kefiya.utils.standing_order_schedule import is_banking_day

#: A part below this is not worth a transfer and a TAN of its own.
CENT = 0.004


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def plan_parts(amount, due, today, accounts, room_on):
    """Spread ``amount`` over ``accounts`` and banking days, ending on ``due``.

    :param accounts: account keys in order of preference; the first is the
        transfer's own account. Duplicates are ignored.
    :return: (parts, rest) -- parts as [(day, account, amount)] in date order,
        and within a day in account order; rest is what did not fit. A rest
        above zero means: place nothing.
    """
    due, today = _as_date(due), _as_date(today)
    order = []
    for account in accounts or []:
        if account and account not in order:
            order.append(account)

    remaining = round(float(amount or 0), 2)
    planned = []
    day = due
    while remaining > CENT and day >= today and order:
        if is_banking_day(day):
            for account in order:
                if remaining <= CENT:
                    break
                room = round(float(room_on(account, day, planned) or 0), 2)
                if room > CENT:
                    part = round(min(room, remaining), 2)
                    planned.append((day, account, part))
                    remaining = round(remaining - part, 2)
        day = day - datetime.timedelta(days=1)

    rank = {account: index for index, account in enumerate(order)}
    parts = sorted(planned, key=lambda p: (p[0], rank[p[1]]))
    return parts, max(remaining, 0.0)


def part_purpose(purpose, index, count, limit=140):
    """The purpose of part ``index`` of ``count``, within the SEPA limit.

    A single part keeps the purpose as it is. Otherwise the suffix comes off
    the purpose, not the other way round: "(Teil 2/3)" is what tells the
    recipient why the money arrives in pieces.
    """
    purpose = purpose or ""
    if count <= 1:
        return purpose[:limit]
    suffix = " (Teil {0}/{1})".format(index, count)
    return purpose[:max(limit - len(suffix), 0)] + suffix


def part_end_to_end_id(base, index, count, limit=35):
    """The end-to-end id of a part, or empty when the transfer had none.

    An empty id is filled from the document name on approval, which is right
    for a part. A given id (a standing order's KSO-..., an invoice's) is kept
    with the part number, so whoever matches on it still finds every part --
    and no two parts carry the same id, which banks use to spot a payment
    they have already seen.
    """
    base = (base or "").strip()
    if not base:
        return ""
    if count <= 1:
        return base[:limit]
    suffix = "-{0}".format(index)
    return base[:max(limit - len(suffix), 0)] + suffix
