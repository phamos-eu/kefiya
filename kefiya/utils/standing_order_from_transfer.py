# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A transfer just entered, repeated as a standing order kept here.

The transfer is the first execution. The standing order starts on its date,
is marked as executed for it, and places the ones after it -- once it is
active. Activating approves every later transfer, so the person who entered
it activates it only where Kefiya Settings let them approve alone; otherwise
it waits as "Proposed" for a second person.
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

ORDER = "Kefiya Standing Order"


@frappe.whitelist()
def create(transfer_name, frequency="Monthly", valid_until=None):
    """Create the standing order behind ``transfer_name``.

    :return: {"name", "status", "next_execution_date"}
    """
    frappe.has_permission(ORDER, ptype="create", throw=True)
    transfer = frappe.get_doc("Kefiya Transfer", transfer_name)
    transfer.check_permission("read")

    if len(transfer.items or []) != 1:
        frappe.throw(_("Only a single transfer can be repeated as a standing"
                       " order, not a collective one."))
    row = transfer.items[0]
    bank_account = frappe.db.get_value("Kefiya Login", transfer.kefiya_login,
                                       "bank_account")
    first = getdate(transfer.execution_date or nowdate())

    order = frappe.get_doc({
        "doctype": ORDER,
        "status": "Proposed",
        "execution_mode": "System",
        "company": transfer.company,
        "bank_account": bank_account,
        "amount": row.amount,
        "frequency": frequency or "Monthly",
        "day_of_month": first.day,
        "counterparty_name": row.recipient_name,
        "counterparty_iban": row.recipient_iban,
        "counterparty_bic": row.recipient_bic,
        "purpose": row.purpose,
        "valid_from": str(first),
        "valid_until": valid_until or None,
        # The transfer just entered is this date's execution.
        "last_executed_for": str(first),
        "last_transfer": transfer.name,
    })
    order.insert()
    transfer.db_set("standing_order", order.name)

    # Active at once only where one person may approve it alone. Anything
    # else is a second person's decision, and the order waits for it.
    if not order.needs_second_person():
        order.status = "Active"
        order.save()

    return {"name": order.name, "status": order.status,
            "next_execution_date": str(order.next_execution_date or "")}
