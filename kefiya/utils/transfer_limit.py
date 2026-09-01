# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The bank's transfer limit, and what to do when an order exceeds it.

A bank refuses the whole order when it goes over the limit -- not the part
above it. A payment run that trips the limit therefore does not pay some of its
invoices, it pays none of them, and the person who pressed send finds out from
a rejection message rather than from the run.

So the limit is read from the bank rather than guessed (HIUPD, see
FinTSController.get_fints_limits), kept on the login, and enforced here before
anything is sent. An order that does not fit is not silently trimmed either:
splitting money across days is a decision, so it happens on request and creates
documents you can see, each with its own execution date.

FinTS Limitart:
    E   per single order
    T   per day
    W   per week
    M   per month
    Z   per period of ``limit_days`` days
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime

LIMIT_SINGLE = "E"
LIMIT_DAILY = "T"
LIMIT_WEEKLY = "W"
LIMIT_MONTHLY = "M"
LIMIT_PERIOD = "Z"

#: Segments that actually move money out of the account. A limit the bank
#: attaches to one of these binds a transfer; a limit on a statement retrieval
#: does not.
TRANSFER_SEGMENTS = ("HKCCS", "HKCCM", "HKCSE", "HKCME", "HKIPZ", "HKIPM")

#: Statuses that will still reach the bank, so their money is already spoken
#: for. "On Hold" is deliberately absent: a held order is not going out.
COMMITTED_STATUSES = ("Approved", "Due", "Scheduled at Bank", "Sent")


def refresh_transfer_limit(kefiya_login, controller=None):
    """Read the limit from the bank and keep it on the login.

    :return: the stored limit as a dict, or {"stored": False, "reason": ...}
    """
    if controller is None:
        from kefiya.utils.fints_controller import FinTSController

        controller = FinTSController(kefiya_login)

    rows = controller.get_fints_limits() or []
    chosen = pick_binding_limit(rows)

    login = frappe.get_doc("Kefiya Login", kefiya_login)
    if not chosen:
        # Recording the attempt matters: "the bank named no limit" and "we
        # never asked" look identical on the document otherwise, and only one
        # of them is a reason to let a large order through.
        login.transfer_limit_checked_on = now_datetime()
        login.save(ignore_permissions=True)
        return {"stored": False, "reason": _("the bank named no limit")}

    login.transfer_limit_amount = flt(chosen.get("amount"))
    login.transfer_limit_type = chosen.get("limit_type")
    login.transfer_limit_days = cint(chosen.get("days"))
    login.transfer_limit_checked_on = now_datetime()
    login.save(ignore_permissions=True)

    return {"stored": True, "limit": chosen, "candidates": rows}


def pick_binding_limit(rows):
    """Of everything the bank said, the one that stops an order first.

    A limit on a transfer segment beats the account-wide one -- it is the
    specific statement about the thing being done. Among equals the smaller
    amount wins, because that is the one that gets hit.
    """
    candidates = [r for r in (rows or [])
                  if r.get("amount") is not None and flt(r.get("amount")) > 0]
    if not candidates:
        return None

    specific = [r for r in candidates
                if r.get("scope") == "transaction"
                and (r.get("transaction") or "")[:5] in TRANSFER_SEGMENTS]
    pool = specific or [r for r in candidates if r.get("scope") == "account"]
    if not pool:
        pool = candidates

    return min(pool, key=lambda r: flt(r["amount"]))


def limit_window(limit_type, days, date):
    """The stretch of time one limit covers, as (start, end).

    Returns (None, None) for a per-order limit: it has no window, it applies to
    each order on its own.
    """
    date = getdate(date)
    if limit_type == LIMIT_DAILY:
        return date, date
    if limit_type == LIMIT_WEEKLY:
        start = add_days(date, -date.weekday())
        return start, add_days(start, 6)
    if limit_type == LIMIT_MONTHLY:
        start = date.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, add_days(end, -1)
    if limit_type == LIMIT_PERIOD:
        span = max(cint(days), 1)
        return add_days(date, -(span - 1)), date
    return None, None


def committed_amount(kefiya_login, start, end, exclude=None):
    """How much is already spoken for in a window, ignoring the given documents.

    :param exclude: one document name or a list of them -- the ones being
        judged right now, which must not be counted against themselves.
    """
    if isinstance(exclude, str):
        exclude = [exclude]
    exclude = [name for name in (exclude or []) if name]

    filters = {
        "kefiya_login": kefiya_login,
        "docstatus": 1,
        "status": ("in", COMMITTED_STATUSES),
        "execution_date": ("between", [getdate(start), getdate(end)]),
    }
    if exclude:
        filters["name"] = ("not in", exclude)

    rows = frappe.get_all("Kefiya Transfer", filters=filters,
                          fields=["total_amount"])
    return sum(flt(r.total_amount) for r in rows)


def check_transfer(doc):
    """Would this order be refused by the bank? Say so before it is sent."""
    return check_batch([doc])


def check_batch(docs):
    """The same question for several orders leaving together.

    The limit applies to what leaves the ACCOUNT, not to one document, so a
    batch is measured as a whole. Judging each document on its own would wave
    through ten orders that each fit and together do not.

    :return: {"ok": bool, "limit": float|None, "limit_type": str|None,
        "available": float|None, "total": float, "reason": str|None}
    """
    docs = [d for d in (docs or []) if d]
    if not docs:
        return {"ok": True, "limit": None, "limit_type": None,
                "available": None, "total": 0, "reason": None}

    total = sum(flt(d.total_amount) for d in docs)
    login = frappe.get_doc("Kefiya Login", docs[0].kefiya_login)
    limit = flt(login.transfer_limit_amount)
    limit_type = login.transfer_limit_type

    if not limit or not limit_type:
        # Unknown, not unlimited. Nothing is blocked on a guess, but nothing
        # pretends to have been checked either.
        return {"ok": True, "limit": None, "limit_type": None,
                "available": None, "total": total,
                "reason": _("no limit known")}

    if limit_type == LIMIT_SINGLE:
        # Per ORDER, so a batch is judged per document -- the bank sees one
        # message per order even when they are sent together.
        too_big = [d for d in docs if flt(d.total_amount) > limit]
        if too_big:
            return {"ok": False, "limit": limit, "limit_type": limit_type,
                    "available": limit, "total": total,
                    "reason": _("The bank allows at most {0} per order, {1} is"
                                " {2}.").format(
                        frappe.utils.fmt_money(limit), too_big[0].name,
                        frappe.utils.fmt_money(too_big[0].total_amount))}
        return {"ok": True, "limit": limit, "limit_type": limit_type,
                "available": limit, "total": total, "reason": None}

    start, end = limit_window(limit_type, login.transfer_limit_days,
                              docs[0].execution_date or now_datetime().date())
    used = committed_amount(docs[0].kefiya_login, start, end,
                            exclude=[d.name for d in docs])
    available = limit - used
    if total > available:
        return {"ok": False, "limit": limit, "limit_type": limit_type,
                "available": available, "total": total,
                "reason": _(
                    "{0} of the bank's {1} limit is left for {2}, this comes to"
                    " {3}."
                ).format(frappe.utils.fmt_money(available),
                         frappe.utils.fmt_money(limit),
                         frappe.utils.formatdate(start),
                         frappe.utils.fmt_money(total))}
    return {"ok": True, "limit": limit, "limit_type": limit_type,
            "available": available, "total": total, "reason": None}


def plan_split(amounts, limit, available_first=None):
    """Distribute payments over days so no day exceeds the limit.

    Greedy and order-preserving: payments stay in the order they were entered,
    and a day is filled until the next one would not fit. Anything cleverer
    would reorder somebody's payment run to save a day, which is not a trade
    the person who entered it agreed to.

    :param amounts: list of payment amounts, in document order
    :param limit: the per-window limit
    :param available_first: what is left in the first window (defaults to the
        full limit) -- other orders on the same day have already used some of it
    :return: list of lists of indices, one per day; or None when a single
        payment is larger than the limit and no arrangement can help
    """
    limit = flt(limit)
    if limit <= 0:
        return None
    if any(flt(a) > limit for a in amounts):
        return None

    room = flt(limit if available_first is None else available_first)
    days = []
    current = []
    for index, amount in enumerate(amounts):
        amount = flt(amount)
        if current and amount > room:
            days.append(current)
            current = []
            room = limit
        elif not current and amount > room:
            # The first window is too full even for the first payment: start on
            # the next one rather than emitting an empty day.
            room = limit
        current.append(index)
        room -= amount
    if current:
        days.append(current)
    return days


@frappe.whitelist()
def split_over_limit(transfer_name):
    """Split a draft transfer into several, one per day, within the limit.

    Only drafts: after approval the amounts are locked, and moving a payment to
    another day behind an approver's back is exactly what the approval is for.

    :return: {"status": ..., "documents": [names], "message": ...}
    """
    frappe.has_permission("Kefiya Transfer", ptype="write",
                          doc=transfer_name, throw=True)

    doc = frappe.get_doc("Kefiya Transfer", transfer_name)
    if doc.docstatus != 0:
        return {"status": "error", "message": _(
            "Only a draft can be split. Cancel and amend the order, or enter"
            " the remainder as a new one."
        )}

    verdict = check_transfer(doc)
    if verdict["ok"]:
        return {"status": "ok", "documents": [doc.name], "message": _(
            "This order is within the limit; nothing to split."
        )}
    if verdict["limit_type"] == LIMIT_SINGLE and len(doc.items) == 1:
        return {"status": "error", "message": _(
            "A single payment cannot be split: the bank allows at most {0} per"
            " order."
        ).format(frappe.utils.fmt_money(verdict["limit"]))}

    amounts = [flt(row.amount) for row in doc.items]
    plan = plan_split(amounts, verdict["limit"],
                      available_first=verdict.get("available"))
    if plan is None:
        return {"status": "error", "message": _(
            "One of the payments is larger than the limit of {0} on its own."
            " Splitting cannot help -- that payment has to be raised with the"
            " bank."
        ).format(frappe.utils.fmt_money(verdict["limit"]))}
    if len(plan) == 1:
        return {"status": "ok", "documents": [doc.name], "message": _(
            "This order fits into one day after all."
        )}

    rows = [row.as_dict() for row in doc.items]
    start = getdate(doc.execution_date or now_datetime().date())
    created = []

    # The first day stays on the original document, so its name, its history
    # and anything already referring to it survive the split.
    keep = plan[0]
    doc.items = []
    for index in keep:
        doc.append("items", _clean_row(rows[index]))
    doc.save()
    created.append(doc.name)

    for offset, group in enumerate(plan[1:], start=1):
        clone = frappe.new_doc("Kefiya Transfer")
        clone.kefiya_login = doc.kefiya_login
        clone.company = doc.company
        clone.instant_payment = doc.instant_payment
        clone.manage_due_date = doc.manage_due_date
        clone.execution_date = _next_banking_day(start, offset)
        for index in group:
            clone.append("items", _clean_row(rows[index]))
        clone.insert()
        created.append(clone.name)

    return {"status": "ok", "documents": created, "message": _(
        "Split into {0} orders, one per day, each within the bank's limit."
    ).format(len(created))}


def _clean_row(row):
    """A child row without the identity of the row it came from.

    end_to_end_id goes too, and that is not housekeeping: it is built from the
    document name and the row number, banks use it to spot a payment they have
    already seen, and before_submit only fills the empty ones. A copied id would
    therefore travel to the bank naming an order it is not in -- and twice.
    """
    drop = ("name", "owner", "creation", "modified", "modified_by", "parent",
            "parentfield", "parenttype", "idx", "docstatus", "doctype",
            "end_to_end_id")
    return {k: v for k, v in dict(row).items() if k not in drop}


def _next_banking_day(start, offset):
    """Skip Saturday and Sunday: a bank does not execute on them, so a split
    that lands on a weekend simply delays the whole tail by two days."""
    date = getdate(start)
    moved = 0
    while moved < offset:
        date = add_days(date, 1)
        if date.weekday() < 5:
            moved += 1
    return date
