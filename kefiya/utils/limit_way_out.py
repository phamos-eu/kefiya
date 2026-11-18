# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What to do when an order is refused for the bank's transfer limit.

"Vom Banklimit (3.000,00) sind für den 18.11.2026 noch 2.347,48 frei, die
Auswahl umfasst 3.576,00" was the whole answer: the person who pressed send
had to work out parts, days and accounts by themselves and enter them by
hand. The refusal now comes with a table they can edit:

* **Proposals.** On the order's own account, day by day: what is left
  today, then the full limit on each following banking day. And, where the
  same company has other accounts with a known limit, across accounts:
  today on every account with room, only then the next day.
* **Editable.** Day, account and amount of every row can be changed, rows
  added or dropped. The server checks the table again -- the browser's
  arithmetic is a wish, not a permission: the parts add up to the order to
  the cent, no part before today, every account belongs to the same company
  and has a known limit, and no account goes over its limit on any day,
  counting what has already been sent on that day.
* **Approved orders too.** The amount is what an approval was about, so an
  approved order is cancelled and entered again as a draft -- only after
  the person confirmed exactly that. Every part is a draft afterwards,
  approved and sent like any order, each with its own TAN.

Every part is kept HERE until its day (manage_due_date). A dated order the
bank holds counts against the limit of the day it is SENT
(transfer_limit.limit_day), so handing all parts to the bank today would
hit today's limit with every one of them.

The rules themselves are in limit_split_plan, without frappe.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, nowdate

from kefiya.utils import limit_split_plan as plan


def _source(transfer_name):
    """The order to split, or an error message."""
    doc = frappe.get_doc("Kefiya Transfer", transfer_name)
    frappe.has_permission("Kefiya Transfer",
                          ptype="submit" if doc.docstatus == 1 else "write",
                          doc=doc, throw=True)
    if doc.docstatus == 2:
        return doc, _("{0} is cancelled.").format(doc.name)
    if doc.status in ("Sent", "Scheduled at Bank"):
        return doc, _("{0} has gone to the bank already.").format(doc.name)
    if len(doc.items or []) != 1:
        return doc, _(
            "Only an order with one payment can be split into parts here."
            " Select its payments one by one.")
    return doc, None


def _accounts(doc):
    """The order's own account first, then every other account of the same
    company with a known limit -- never the recipient's own account."""
    from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
        normalize_iban,
    )
    from kefiya.utils.transfer_limit import _limit_of, _paying_iban

    recipient = normalize_iban(doc.items[0].recipient_iban or "")
    out = []
    for login in frappe.get_all("Kefiya Login",
                                fields=["name", "bank_account"],
                                order_by="name", limit_page_length=500):
        if not login.bank_account:
            continue
        if frappe.db.get_value("Bank Account", login.bank_account,
                               "company") != doc.company:
            continue
        own = login.name == doc.kefiya_login
        if not own and _paying_iban(login.name) == recipient:
            continue
        limit = _limit_of(login.name)
        if not limit and not own:
            continue
        out.append({"kefiya_login": login.name,
                    "bank_account": login.bank_account,
                    "own": own,
                    "limit": limit[0] if limit else None,
                    "limit_type": limit[1] if limit else None})
    out.sort(key=lambda a: (not a["own"], a["kefiya_login"]))
    return out


def _rows(parts):
    return [{"date": str(day), "kefiya_login": login, "amount": amount}
            for day, login, amount in parts]


@frappe.whitelist()
def options(transfer_name):
    """Proposals and the room per account and day -- nothing is changed."""
    from kefiya.utils.transfer_limit import _room_function

    doc, error = _source(transfer_name)
    if error:
        return {"status": "error", "message": error}

    accounts = _accounts(doc)
    usable = [a["kefiya_login"] for a in accounts if a["limit"]]
    if doc.kefiya_login not in usable:
        return {"status": "error", "message": _(
            "No transfer limit is known for {0}, so no split can be planned."
        ).format(doc.kefiya_login)}

    room_on = _room_function(exclude=[doc.name])
    # Never earlier than the order asks: an order for the 9th split over
    # the next three days would pay three weeks early. The table still
    # accepts any day from today on -- that is the person's call.
    today = plan.as_date(nowdate())
    start = max(today, plan.as_date(doc.execution_date) or today)
    days = plan.banking_days(start, plan.MAX_DAYS)
    total = float(doc.total_amount or 0)

    proposals = []
    own, rest = plan.fill(total, [doc.kefiya_login], days, room_on)
    if rest <= plan.CENT:
        proposals.append({"key": "days",
                          "label": _("Own account, day by day"),
                          "parts": _rows(own)})
    if len(usable) > 1:
        spread, rest = plan.fill(total, usable, days, room_on)
        if rest <= plan.CENT and _rows(spread) != _rows(own):
            proposals.append({"key": "accounts",
                              "label": _("Several accounts, as early as"
                                         " possible"),
                              "parts": _rows(spread)})

    # What is free per account and day before anything in the table; the
    # dialog subtracts its own rows. Exact for a daily limit -- for a longer
    # window the check on applying is what counts.
    room = {login: {str(day): plan.cents_down(room_on(login, day, []))
                    for day in days} for login in usable}

    return {"status": "ok", "name": doc.name, "total": total,
            "approved": doc.docstatus == 1, "accounts": accounts,
            "proposals": proposals, "room": room, "today": str(today),
            "recipient": doc.items[0].recipient_name}


@frappe.whitelist()
def apply(transfer_name, parts, reopen=0):
    """Place the parts as the person left them in the table.

    All or nothing: everything is checked before anything is written, and an
    error while writing rolls the whole request back.

    :param reopen: an approved order is cancelled and entered again as a
        draft only when this is set -- after the person confirmed it.
    """
    from kefiya.utils.partial_payment_plan import part_purpose
    from kefiya.utils.transfer_limit import _clean_row, _room_function

    doc, error = _source(transfer_name)
    if error:
        return {"status": "error", "message": error}
    if doc.docstatus == 1 and not cint(reopen):
        return {"status": "error", "message": _(
            "{0} is approved. It has to be cancelled and entered again to be"
            " split -- confirm that first.").format(doc.name)}

    if isinstance(parts, str):
        parts = json.loads(parts)
    allowed = [a["kefiya_login"] for a in _accounts(doc) if a["limit"]]
    problem, detail, clean = plan.check_table(
        parts, doc.total_amount, nowdate(), allowed,
        _room_function(exclude=[doc.name]))
    if problem:
        return {"status": "error", "message": _problem_text(
            problem, detail, doc.total_amount)}

    original = None
    first = doc
    if doc.docstatus == 1:
        frappe.has_permission("Kefiya Transfer", ptype="cancel", doc=doc,
                              throw=True)
        original = doc.name
        doc.cancel()
        first = frappe.copy_doc(doc)
        # copy_doc keeps the docstatus of what it copies -- 2 by now.
        first.docstatus = 0
        first.amended_from = original
        first.status = "Draft"

    row = _clean_row(doc.items[0].as_dict())
    count = len(clean)
    names = []
    ordered = sorted(clean, key=lambda p: (str(p[0]), p[1]))
    for index, (day, login, amount) in enumerate(ordered, start=1):
        item = dict(row, amount=amount,
                    purpose=part_purpose(row.get("purpose"), index, count))
        target = first if index == 1 else frappe.new_doc("Kefiya Transfer")
        if index > 1:
            target.company = doc.company
            target.instant_payment = doc.instant_payment
            target.standing_order = doc.standing_order
        target.kefiya_login = login
        target.execution_date = str(day)
        # Kept here until its day: a dated order at the bank would count
        # against TODAY's limit, see the module docstring.
        target.manage_due_date = 1
        target.items = []
        target.append("items", item)
        if target.is_new():
            target.insert()
        else:
            target.save()
        names.append(target.name)

    if original:
        frappe.get_doc("Kefiya Transfer", names[0]).add_comment(
            "Info", _("Entered again from {0} to be paid in {1} parts within"
                      " the bank's limit.").format(original, count))
    return {"status": "ok", "documents": names, "cancelled": original}


def _problem_text(problem, detail, total):
    if problem == "limit":
        day, login, room = detail
        return _("On {0}, {1} has only {2} left within its limit.").format(
            frappe.utils.formatdate(day), login,
            frappe.utils.fmt_money(room))
    if problem == "account":
        return _("{0} cannot pay this order: it belongs to another company,"
                 " has no known limit, or is the recipient.").format(detail)
    return {
        "amount": _("Every part needs an amount above zero."),
        "date": _("No part can be dated before today."),
        "count": _("Split into at least two parts."),
        "sum": _("The parts must add up to {0}.").format(
            frappe.utils.fmt_money(total)),
    }[problem]
