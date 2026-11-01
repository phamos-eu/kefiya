# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What to do when an order is refused for want of funds.

funds_check stops a transfer the account cannot carry. A refusal alone leaves
the person who pressed send with three steps to work out by themselves; two of
them are offered right in the refusal:

* **Make it a dated transfer (Terminüberweisung).** The bank holds it until
  the day and refuses it itself if the money is not there -- which is why
  funds_check does not judge dated transfers at all. Only the date changes:
  that is the one thing an approved order may still be told
  (``allow_on_submit``), so this works on drafts and on approved orders alike.
* **Split the amount.** Freely chosen parts, each with its own day: what the
  account carries now goes today, the rest on later days. The parts are the
  same payment -- recipient, IBAN and purpose stay, the total stays to the
  cent -- but the amount of an order is what its approval was about, so only a
  draft can be split. An approved order is cancelled and entered again, by the
  person whose job that is.

Rules without frappe are in ``check_parts``; the endpoints read and write.
"""

import datetime
import json

import frappe
from frappe import _

#: Rounding, not leniency.
CENT = 0.005

#: A purpose line is 140 characters in a SEPA message.
PURPOSE_MAX = 140


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def next_banking_day(today):
    """The first Monday-to-Friday after today. Holidays are the bank's."""
    day = _as_date(today) + datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day = day + datetime.timedelta(days=1)
    return day


def check_parts(parts, total, today):
    """Is this a valid way to split ``total``?

    :param parts: [{"amount", "date"}] in the order the person entered them
    :return: (problem key or None, parts with amounts rounded and dates parsed)
    """
    today = _as_date(today)
    clean = []
    for part in parts or []:
        try:
            amount = round(float(part.get("amount") or 0), 2)
        except (TypeError, ValueError):
            return "amount", []
        date = _as_date(part.get("date")) if part.get("date") else today
        if amount <= 0:
            return "amount", []
        if date is None or date < today:
            return "date", []
        clean.append({"amount": amount, "date": date})
    if len(clean) < 2:
        return "count", []
    if abs(sum(p["amount"] for p in clean) - round(float(total or 0), 2)) \
            > CENT:
        return "sum", []
    return None, clean


def part_purpose(purpose, index, count, word):
    """The purpose of one part, saying which part it is.

    The recipient matches the payment to an invoice by its purpose, and a
    part that arrives looking like the whole payment is booked as one. The
    marker goes in front of nothing the recipient needs: it is appended, and
    the original is shortened if the line would run over.
    """
    marker = " ({0} {1}/{2})".format(word, index, count)
    base = (purpose or "").strip()
    room = PURPOSE_MAX - len(marker)
    return (base[:room].rstrip() + marker).strip()


def _usable(doc):
    """Why this order cannot be changed here, or None."""
    if doc.docstatus == 2:
        return _("{0} is cancelled.").format(doc.name)
    if doc.status in ("Sent", "Scheduled at Bank"):
        return _("{0} has gone to the bank already.").format(doc.name)
    return None


@frappe.whitelist()
def make_dated(transfer_names, execution_date):
    """Turn orders into dated transfers the bank holds until their day.

    :return: {"status": "ok", "names": [...], "date": "YYYY-MM-DD"} or an
        error dict
    """
    from frappe.utils import nowdate

    from kefiya.utils.client import _refuse_unsupported

    if isinstance(transfer_names, str):
        transfer_names = json.loads(transfer_names)
    names = [n for n in (transfer_names or []) if n]
    if not names:
        return {"status": "error", "message": _("No transfers selected.")}

    date = _as_date(execution_date)
    if date is None or date <= _as_date(nowdate()):
        return {"status": "error", "message": _(
            "A dated transfer needs a day after today.")}

    docs = []
    for name in names:
        doc = frappe.get_doc("Kefiya Transfer", name)
        # Moving the date of an approved order needs the right to send it;
        # a draft is anybody's who may edit it.
        frappe.has_permission("Kefiya Transfer",
                              ptype="submit" if doc.docstatus == 1
                              else "write", doc=doc, throw=True)
        why = _usable(doc)
        if why:
            return {"status": "error", "message": why}
        docs.append(doc)

    # Asked before anything changes: an account the bank allows no dated
    # transfers on would otherwise end with orders moved for nothing.
    unsupported = _refuse_unsupported(docs, True)
    if unsupported:
        return unsupported

    for doc in docs:
        doc.execution_date = date
        doc.manage_due_date = 0
        doc.save()
    return {"status": "ok", "names": [d.name for d in docs],
            "date": str(date)}


@frappe.whitelist()
def split_amounts(transfer_name, parts):
    """Split a draft into parts the person chose, each with its own day.

    The first part stays on the original document, so its name and history
    survive. A part dated after today is handed to the bank as a dated
    transfer; a part for today goes out as an ordinary one.

    :param parts: JSON list of {"amount", "date"}
    :return: {"status": "ok", "documents": [names]} or an error dict
    """
    from frappe.utils import nowdate

    from kefiya.utils.transfer_limit import _clean_row

    frappe.has_permission("Kefiya Transfer", ptype="write",
                          doc=transfer_name, throw=True)
    doc = frappe.get_doc("Kefiya Transfer", transfer_name)
    if doc.docstatus != 0:
        return {"status": "error", "message": _(
            "Only a draft can be split: the amount is what its approval was"
            " about. Cancel and amend the order to split it.")}
    if len(doc.items or []) != 1:
        return {"status": "error", "message": _(
            "Only an order with one payment can be split into amounts.")}

    if isinstance(parts, str):
        parts = json.loads(parts)
    today = _as_date(nowdate())
    problem, clean = check_parts(parts, doc.total_amount, today)
    if problem:
        return {"status": "error", "message": {
            "amount": _("Every part needs an amount above zero."),
            "date": _("No part can be dated before today."),
            "count": _("Split into at least two parts."),
            "sum": _("The parts must add up to {0}.").format(
                frappe.utils.fmt_money(doc.total_amount)),
        }[problem]}

    row = _clean_row(doc.items[0].as_dict())
    word = _("Part payment")
    count = len(clean)
    created = []
    for index, part in enumerate(clean, start=1):
        values = dict(row, amount=part["amount"],
                      purpose=part_purpose(row.get("purpose"), index, count,
                                           word))
        target = doc if index == 1 else frappe.new_doc("Kefiya Transfer")
        if index > 1:
            for field in ("kefiya_login", "company", "instant_payment"):
                target.set(field, doc.get(field))
        target.execution_date = part["date"]
        # Later than today: the bank holds it. Today: it goes out now.
        target.manage_due_date = 0 if part["date"] > today else 1
        target.items = []
        target.append("items", values)
        if index == 1:
            target.save()
        else:
            target.insert()
        created.append(target.name)

    return {"status": "ok", "documents": created}
