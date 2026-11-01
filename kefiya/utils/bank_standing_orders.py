# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The standing orders the bank holds, kept as Kefiya Standing Order.

A standing order is either kept here (execution_mode System: kefiya places
each transfer) or held by the bank (execution_mode Bank: the bank executes
it). Until now the second kind existed here only if somebody typed it in, or
if the pattern detection guessed it from past bookings. The bank's own list
(HKCDB) is the better source, so after every successful read:

  * an order already on file -- same bank identifier, or same account, IBAN,
    amount and day -- is marked as reported (bank_seen_on, bank_task_id);
    a detection proposal the bank confirms becomes active;
  * an order not on file is created, active, execution_mode Bank,
    detected_by "bank";
  * an order the bank reported before and no longer lists is ended.

One case is not resolved here: the bank holding an order we also execute
ourselves. That pays twice. It is reported in the Error Log and nothing is
changed -- which of the two goes is a person's decision.

Nothing here talks to the bank or moves money. The bank executes its orders
whether or not they are on file; these records only make them visible and
put them into the forecast.
"""

import frappe
from frappe.utils import nowdate

from kefiya.utils import bank_held_orders

DOCTYPE = "Kefiya Standing Order"
LOG_TITLE = "Kefiya: standing order kept here AND at the bank"


def sync(kefiya_login, rows):
    """Bring the bank's standing orders for one login onto file.

    :param rows: what standing_orders.fetch_standing_orders() returned
    :return: {"created", "confirmed", "ended", "skipped": {reason: n},
              "conflicts": [...]}
    """
    bank_account, company = frappe.db.get_value(
        "Kefiya Login", kefiya_login, ["bank_account", "company"]) \
        or (None, None)
    if not bank_account:
        return {"created": 0, "confirmed": 0, "ended": 0, "skipped": {},
                "conflicts": []}
    company = frappe.db.get_value("Bank Account", bank_account,
                                  "company") or company
    today = nowdate()

    on_file = [dict(r) for r in frappe.get_all(
        DOCTYPE,
        filters={"bank_account": bank_account,
                 "status": ("not in", ["Ended", "Rejected"])},
        fields=["name", "status", "execution_mode", "amount",
                "counterparty_iban", "day_of_month", "bank_task_id",
                "bank_seen_on"],
        limit_page_length=0)]

    out = {"created": 0, "confirmed": 0, "ended": 0, "skipped": {},
           "conflicts": []}
    seen = set()
    for row in rows or []:
        values, why = bank_held_orders.standing_order_values(row)
        if values is None:
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
            continue

        match = [o for o in on_file
                 if bank_held_orders.same_order(values, o)]
        if match:
            order = match[0]
            seen.add(order["name"])
            if order["execution_mode"] != "Bank":
                out["conflicts"].append(order["name"])
                continue
            update = {"bank_seen_on": today}
            if values.get("bank_task_id"):
                update["bank_task_id"] = values["bank_task_id"]
            doc = frappe.get_doc(DOCTYPE, order["name"])
            doc.update(update)
            if doc.status == "Proposed":
                # The pattern detection guessed it; the bank confirms it.
                doc.status = "Active"
            doc.save(ignore_permissions=True)
            out["confirmed"] += 1
            continue

        doc = frappe.get_doc(dict(
            values,
            doctype=DOCTYPE,
            status="Active",
            execution_mode="Bank",
            company=company,
            bank_account=bank_account,
            detected_by="bank",
            bank_seen_on=today,
        ))
        doc.insert(ignore_permissions=True)
        seen.add(doc.name)
        on_file.append({"name": doc.name, "execution_mode": "Bank",
                        "status": "Active", "amount": values["amount"],
                        "counterparty_iban": values["counterparty_iban"],
                        "day_of_month": values["day_of_month"],
                        "bank_task_id": values.get("bank_task_id"),
                        "bank_seen_on": today})
        out["created"] += 1

    # Reported before, not any more: the order was deleted at the bank.
    for order in on_file:
        if order["name"] in seen or order["execution_mode"] != "Bank":
            continue
        if not order.get("bank_seen_on") or order["status"] != "Active":
            continue
        doc = frappe.get_doc(DOCTYPE, order["name"])
        doc.status = "Ended"
        doc.save(ignore_permissions=True)
        out["ended"] += 1

    if out["conflicts"]:
        frappe.log_error(
            title=LOG_TITLE,
            message=(
                "The bank holds a standing order that is also executed"
                " here (execution_mode System). Unless one of them is"
                " stopped, the payment is made twice.\n\nLogin: {0}\n"
                "Orders: {1}").format(kefiya_login,
                                      ", ".join(out["conflicts"])))
    return out
