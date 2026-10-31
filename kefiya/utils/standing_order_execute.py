# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Places the transfers of standing orders kept here (execution_mode System).

Daily. On its date -- lead_days earlier, so it is seen in time -- an active
order's transfer goes into the outgoing payments, APPROVED, waiting there
until its date (manage_due_date). Nothing is sent: a credit transfer needs a
TAN, and that is a person's. Moved from the instance's scheduler script on
30.10.2026; it used to place drafts, and a draft is a second approval of
something already approved when the order was activated.

Approved in whose name. Activating the order approved all its executions,
under the four-eyes rule (see KefiyaStandingOrder.approve_if_activated). So
each transfer is entered in the name of whoever entered the order and
approved in the name of whoever activated it -- the same two people, and the
transfer's own four-eyes check holds them apart again. An order activated
before that rule existed carries no approver and is not executed; it says so.

Never touched: execution_mode Bank. The bank executes those itself, and a
second transfer would pay twice.

Transfer limit, per account and period (Kefiya Login transfer_limit_amount /
transfer_limit_days). An amount above what is free is split into parts on
banking days ending on the due date; transfers already planned on the
account use the limit up first. No limit known -> nothing placed, said in
the Error Log: the limit must be kept, never guessed.

Safety
  - end-to-end id KSO-<name>-<due>[-<part>]: an occurrence with any part
    already placed is only moved on, never placed twice (also repairs a crash
    between placing and moving on)
  - a due date in the past is never placed backdated
  - MAX_PER_RUN caps the transfers per run
  - Kefiya Settings "Pause standing orders" stops it entirely
"""

from __future__ import unicode_literals

import frappe
from frappe.utils import add_days, cint, flt, getdate, nowdate

from kefiya.utils import standing_order_schedule as schedule

#: At most this many transfers per run.
MAX_PER_RUN = 20

ORDER = "Kefiya Standing Order"
LOG_TITLE = "Kefiya: standing order not executed"


def place_due_transfers():
    """The daily job. Never raises; a failed order is said, not hidden."""
    if cint(frappe.db.get_single_value("Kefiya Settings",
                                       "pause_standing_orders")):
        return {"paused": True}

    today = getdate(nowdate())
    run = Run(today)
    for order in frappe.get_all(
            ORDER,
            filters={"status": "Active", "execution_mode": "System",
                     "next_execution_date": ["is", "set"]},
            fields=["name"], order_by="next_execution_date",
            limit_page_length=500):
        if run.placed >= MAX_PER_RUN:
            run.stats["capped"] += 1
            continue
        try:
            run.execute(frappe.get_doc(ORDER, order.name))
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            run.stats["errors"] += 1
            frappe.log_error(title=LOG_TITLE,
                             message="{0}\n\n{1}".format(
                                 order.name, frappe.get_traceback()),
                             reference_doctype=ORDER,
                             reference_name=order.name)
    frappe.logger("kefiya").info("Kefiya standing orders: %s", run.stats)
    return run.stats


class Run:
    def __init__(self, today):
        self.today = today
        self.placed = 0
        self.planned = {}
        self.stats = {"due": 0, "placed_orders": 0, "placed_parts": 0,
                      "already_placed": 0, "missed": 0, "no_limit": 0,
                      "does_not_fit": 0, "no_login": 0, "not_approved": 0,
                      "capped": 0, "errors": 0}

    def planned_on(self, login, day):
        key = "{0}|{1}".format(login, day)
        if key not in self.planned:
            rows = frappe.db.sql("""
                SELECT IFNULL(SUM(i.amount), 0)
                FROM `tabKefiya Transfer Item` i
                JOIN `tabKefiya Transfer` t ON t.name = i.parent
                WHERE t.kefiya_login = %(login)s
                  AND t.execution_date = %(day)s
                  AND t.docstatus < 2 AND IFNULL(t.status, '') <> 'Failed'
            """, {"login": login, "day": str(day)})
            self.planned[key] = flt(rows[0][0]) if rows else 0.0
        return self.planned[key]

    def free_limit(self, login, day, limit, window):
        """A transfer on ``day`` counts in every limit period containing it."""
        used = sum(self.planned_on(login, getdate(add_days(day, i)))
                   for i in range(-(window - 1), window))
        return limit - used

    def execute(self, order):
        due = getdate(order.next_execution_date)
        amount = flt(order.amount, 2)
        label = order.counterparty_name or order.name
        e2e_base = "KSO-{0}-{1}".format(order.name, due)

        login = frappe.db.get_value(
            "Kefiya Login", {"bank_account": order.bank_account},
            ["name", "transfer_limit_amount", "transfer_limit_days"],
            as_dict=True)
        limit = flt(login.transfer_limit_amount) if login else 0.0
        window = (cint(login.transfer_limit_days) if login else 0) or 1
        parts_needed = (cint((amount + limit - 0.01) // limit) or 1) \
            if limit > 0 else 1
        lead = cint(order.lead_days) + (parts_needed - 1) * 2
        if getdate(add_days(due, -lead)) > self.today:
            return
        self.stats["due"] += 1

        placed = frappe.db.get_value(
            "Kefiya Transfer Item",
            {"end_to_end_id": ["like", e2e_base + "%"]}, "parent")
        if placed:
            self.stats["already_placed"] += 1
            self.move_on(order, due, placed)
            return
        if due < self.today:
            self.stats["missed"] += 1
            self.say(order, (
                "The date {0} passed without a transfer being placed for {1}."
                " Nothing is backdated: pay it by hand if it is still owed."
                " Saving the standing order moves it to its next date."
            ).format(due, label))
            return
        if not order.approved_by:
            self.stats["not_approved"] += 1
            self.say(order, (
                "{0} is active but carries no approval, so its transfer for {1}"
                " was not placed. Set it to Paused and have a second person"
                " activate it again.").format(order.name, due))
            return
        if not login:
            self.stats["no_login"] += 1
            self.say(order, "There is no bank access for {0}.".format(
                order.bank_account))
            return
        if limit <= 0:
            self.stats["no_limit"] += 1
            self.say(order, (
                "No transfer limit is kept for {0} (bank access, Limit"
                " Amount). Without it, it cannot be checked whether the"
                " {1:.2f} for {2} must be split, so nothing was placed."
            ).format(order.bank_account, amount, due))
            return

        parts, rest = schedule.split_backwards(
            amount, due, self.today,
            lambda day: self.free_limit(login.name, day, limit, window))
        if rest > 0.004:
            self.stats["does_not_fit"] += 1
            self.say(order, (
                "{0:.2f} for {1} does not fit the transfer limit of {2:.2f}"
                " before its date; there is room for {3:.2f}. Nothing was"
                " placed: raise the lead days or pay it by hand."
            ).format(amount, due, limit, amount - rest))
            return
        if self.placed + len(parts) > MAX_PER_RUN:
            self.stats["capped"] += 1
            return

        first = None
        count = len(parts)
        for index, (day, part) in enumerate(parts, start=1):
            transfer = self.place(order, login.name, day, part,
                                  e2e_base, index, count)
            first = first or transfer
            key = "{0}|{1}".format(login.name, day)
            self.planned[key] = self.planned_on(login.name, day) + part
        self.placed += count
        self.stats["placed_orders"] += 1
        self.stats["placed_parts"] += count
        self.move_on(order, due, first)

    def place(self, order, login, day, part, e2e_base, index, count):
        """One transfer, entered as the order's creator, approved as its
        approver."""
        purpose = order.purpose or ""
        e2e = e2e_base
        if count > 1:
            suffix = " (Teil {0}/{1})".format(index, count)
            purpose = purpose[:140 - len(suffix)] + suffix
            e2e = "{0}-{1}".format(e2e_base, index)

        transfer = frappe.get_doc({
            "doctype": "Kefiya Transfer",
            "kefiya_login": login,
            "company": order.company,
            "execution_date": str(day),
            # Kept here until its date, then presented like any due order.
            "manage_due_date": 1,
            # An ordinary transfer, as a bank's standing order is.
            "instant_payment": 0,
            "standing_order": order.name,
            "items": [{
                "doctype": "Kefiya Transfer Item",
                "recipient_name": (order.counterparty_name or "")[:70],
                "recipient_iban": order.counterparty_iban,
                "recipient_bic": order.counterparty_bic or "",
                "amount": part,
                "purpose": purpose[:140],
                "end_to_end_id": e2e[:35],
            }],
        })
        transfer.owner = order.owner
        transfer.insert(ignore_permissions=True)

        acting = frappe.session.user
        try:
            frappe.set_user(order.approved_by)
            transfer.submit()
        finally:
            frappe.set_user(acting)
        return transfer.name

    def move_on(self, order, due, transfer):
        if transfer:
            order.last_transfer = transfer
        order.last_executed_for = str(due)
        order.save(ignore_permissions=True)

    def say(self, order, text):
        frappe.log_error(title=LOG_TITLE, message=text,
                         reference_doctype=ORDER, reference_name=order.name)
