# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A transfer may not take more than the account has: balance plus line.

Checked when an order is sent, next to the bank's transfer limit. What counts
against what is available:

  * the orders being sent now, and
  * what has already left the account since the balance was read: orders
    sent after it, and dated orders the bank executes after that day and up
    to today.

The balance must be recent: read by a fetch within MAX_AGE_DAYS. A balance
older than that -- or none at all -- blocks the order until somebody fetches.
That is deliberate: "probably enough" is the guess this check exists to stop.
A site whose bank accounts carry no fetch stamp at all (the field is an
instance's Custom Field) cannot say how old a balance is; there the check
does not apply rather than block every payment.

Not checked:

  * a dated order handed to the bank (Terminüberweisung) -- the bank holds it
    and refuses it itself on the day, if the money is not there;
  * a transfer placed by a standing order -- the same: an order on a cycle
    is the bank's to refuse, and holding it back here would skip a payment
    somebody arranged to recur.

The rule is in verdict(), without frappe; check() reads the figures.
"""

import datetime

#: Days a balance may be old before it no longer counts as known.
MAX_AGE_DAYS = 2

#: Rounding, not leniency.
EPSILON = 0.005

#: The Bank Account field stamped by every fetch the bank answered.
STAMP_FIELD = "custom_last_successful_fetch"


def _as_datetime(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time())
    try:
        return datetime.datetime.fromisoformat(str(value)[:26])
    except ValueError:
        return None


def verdict(total, balance, credit_line, fetched_at, now, in_flight=0.0,
            max_age_days=MAX_AGE_DAYS):
    """Does the account cover this?

    :return: {"ok": bool, "why": None | "unknown" | "stale" | "short",
              "available": float|None, "left": float|None}
    """
    fetched_at = _as_datetime(fetched_at)
    now = _as_datetime(now)
    if balance is None or fetched_at is None:
        return {"ok": False, "why": "unknown", "available": None,
                "left": None}
    if (now - fetched_at) > datetime.timedelta(days=max_age_days):
        return {"ok": False, "why": "stale", "available": None, "left": None}
    available = float(balance) + float(credit_line or 0)
    left = available - float(in_flight or 0)
    if float(total) > left + EPSILON:
        return {"ok": False, "why": "short", "available": available,
                "left": left}
    return {"ok": True, "why": None, "available": available, "left": left}


def exempt(docs, scheduled):
    """The orders this check leaves to the bank, and the rest."""
    if scheduled:
        return list(docs), []
    by_us = [d for d in docs if not getattr(d, "standing_order", None)]
    left_alone = [d for d in docs if getattr(d, "standing_order", None)]
    return left_alone, by_us


def check(docs, scheduled=False):
    """Stop an order the account cannot carry.

    :return: an error dict for the caller, or None when it passes
    """
    import frappe
    from frappe import _
    from frappe.utils import flt, fmt_money, getdate, now_datetime

    from kefiya.utils.balance_visibility import may_see_balances

    _exempted, judged = exempt([d for d in (docs or []) if d], scheduled)
    if not judged:
        return None

    login = judged[0].kefiya_login
    bank_account = frappe.db.get_value("Kefiya Login", login, "bank_account")
    if not bank_account:
        return None
    meta = frappe.get_meta("Bank Account")
    if not meta.has_field(STAMP_FIELD) or \
            not meta.has_field("custom_account_balance"):
        return None

    account = frappe.db.get_value(
        "Bank Account", bank_account,
        ["custom_account_balance", "custom_credit_line", STAMP_FIELD],
        as_dict=True) or {}
    fetched_at = account.get(STAMP_FIELD)
    balance = account.get("custom_account_balance")
    now = now_datetime()

    in_flight = 0.0
    if fetched_at:
        names = [d.name for d in judged]
        for row in frappe.get_all(
                "Kefiya Transfer",
                filters={"kefiya_login": login, "docstatus": 1,
                         "status": "Sent", "modified": (">", fetched_at),
                         "name": ("not in", names)},
                fields=["total_amount"], limit_page_length=0):
            in_flight += flt(row.total_amount)
        for row in frappe.get_all(
                "Kefiya Transfer",
                filters={"kefiya_login": login, "docstatus": 1,
                         "status": "Scheduled at Bank",
                         "execution_date": ("between", [
                             getdate(fetched_at), getdate(now)]),
                         "name": ("not in", names)},
                fields=["total_amount", "execution_date"],
                limit_page_length=0):
            if getdate(row.execution_date) > getdate(fetched_at):
                in_flight += flt(row.total_amount)

    total = sum(flt(d.total_amount) for d in judged)
    answer = verdict(total, balance, account.get("custom_credit_line"),
                     fetched_at, now, in_flight)
    if answer["ok"]:
        return None

    seen = may_see_balances()
    if answer["why"] in ("unknown", "stale"):
        message = _(
            "No recent balance is known for {0}, so it cannot be checked"
            " whether the account covers this transfer. Fetch the account,"
            " then send again.").format(bank_account)
    elif seen:
        message = _(
            "{0} would leave the account below what is available: {1}"
            " available (balance and credit line), {2} already on its way,"
            " {3} left.").format(
                fmt_money(total), fmt_money(answer["available"]),
                fmt_money(in_flight), fmt_money(answer["left"]))
    else:
        # Not this reader's to see -- the refusal must not tell them the
        # balance it refused against.
        message = _("The account does not cover this transfer.")
    # What the way out of this refusal needs (funds_way_out): the orders, and
    # whether one of them can be split. "left" only for whoever may see
    # balances -- it would tell the others the balance it was refused against.
    return {"status": "error", "over_funds": True, "why": answer["why"],
            "message": message,
            "names": [d.name for d in judged], "kefiya_login": login,
            "total": total,
            "left": answer["left"] if seen else None,
            "can_split": 1 if (len(judged) == 1
                               and getattr(judged[0], "docstatus", 1) == 0
                               and len(getattr(judged[0], "items", None)
                                       or []) == 1) else 0}
