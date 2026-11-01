# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The standing orders, as the outgoing-payments page shows them.

Next to the orders that wait to be sent, because a standing order is the
same thing on a cycle: kept here (execution_mode System -- each execution
lands in the outgoing payments) or held by the bank (execution_mode Bank).
Seeing both in one place is what keeps one payment from being set up twice.
"""

import frappe
from frappe.utils import add_days, flt, nowdate

DOCTYPE = "Kefiya Standing Order"

#: What each list on the page holds.
SCOPES = {
    "current": ["Active", "Paused"],
    "proposals": ["Proposed"],
    "ended": ["Ended", "Rejected"],
}

#: Executions per month, to state what the active orders cost a month.
PER_MONTH = {"Monthly": 1.0, "Quarterly": 1 / 3.0, "Half-Yearly": 1 / 6.0,
             "Yearly": 1 / 12.0}

#: A bank order not listed by the bank for longer than this is worth a look:
#: the fetch reads the list every time it runs.
REPORT_STALE_DAYS = 10


def bank_check(row, today):
    """What the bank says about one order, in one word.

    :return: None for orders kept here; "reported", "stale" or "never"
    """
    if row.get("execution_mode") != "Bank":
        return None
    seen = row.get("bank_seen_on")
    if not seen:
        return "never"
    if str(seen) < str(add_days(today, -REPORT_STALE_DAYS)):
        return "stale"
    return "reported"


@frappe.whitelist()
def overview(scope="current"):
    """Everything the standing-orders section shows.

    :param scope: current | proposals | ended | all
    :return: {"rows", "counts", "monthly", "can"}
    """
    frappe.has_permission(DOCTYPE, ptype="read", throw=True)
    today = nowdate()

    fields = ["name", "status", "execution_mode", "company", "bank_account",
              "counterparty_name", "counterparty_iban", "purpose", "amount",
              "frequency", "day_of_month", "next_execution_date",
              "valid_until", "bank_seen_on", "detected_by"]
    # get_list, not get_all: an order somebody may not read is not counted
    # into a total on the page either.
    every = frappe.get_list(DOCTYPE, fields=fields,
                            order_by="next_execution_date asc, name asc",
                            limit_page_length=0)

    counts = {key: 0 for key in SCOPES}
    counts["all"] = len(every)
    monthly = 0.0
    for row in every:
        for key, statuses in SCOPES.items():
            if row.get("status") in statuses:
                counts[key] += 1
        if row.get("status") == "Active":
            monthly += flt(row.get("amount")) * PER_MONTH.get(
                row.get("frequency"), 1.0)

    wanted = SCOPES.get(scope)
    rows = []
    for row in every:
        if wanted and row.get("status") not in wanted:
            continue
        row = dict(row)
        row["bank_check"] = bank_check(row, today)
        for key in ("next_execution_date", "valid_until", "bank_seen_on"):
            row[key] = str(row[key]) if row.get(key) else None
        row["amount"] = flt(row.get("amount"))
        rows.append(row)

    return {
        "today": today,
        "scope": scope if scope in SCOPES or scope == "all" else "current",
        "rows": rows,
        "counts": counts,
        "monthly": round(monthly, 2),
        "can": {
            "create": 1 if frappe.has_permission(DOCTYPE, ptype="create")
            else 0,
            "write": 1 if frappe.has_permission(DOCTYPE, ptype="write")
            else 0,
        },
    }
