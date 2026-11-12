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

WHERE THE BANK NAMES NONE, and that is most accounts -- 24 of 59 on the
instance this was measured on, all of them checked and all of them blank --
the limit is entered by hand on the login. It is then used exactly like the
bank's own; check_batch cannot tell them apart and must not.

Who wins is settled, and it is not "the last writer": a value the bank
REPORTS replaces what stands there, and a bank that reports nothing, or
zero, leaves it alone. pick_binding_limit keeps only amounts above zero, so
an empty answer never becomes `chosen`, and refresh_transfer_limit then
stamps transfer_limit_checked_on and touches nothing else. That is what
makes a hand-entered limit survive every nightly fetch.

A blank limit is UNKNOWN, not unlimited. check_batch says so and lets the
order through: nothing is blocked on a guess. The cost of that is the
reason the hand entry exists -- an order over an unpublished limit is
refused by the bank with a bare "9050 Die Nachricht enthaelt Fehler / 9010
Der Auftrag wurde nicht ausgefuehrt", after the TAN was spent on it, with
nothing in it that names a limit.

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


def limit_day(row, today=None):
    """The day an order counts against the limit.

    The day the bank took it, once it has: a dated order (Terminueberweisung)
    is counted on the day it is sent, not on the day it is executed -- several
    parts of one payment, sent on several days and all executed on the last,
    is how a large payment is paid within the limit (01.11.2026). Until it is
    sent, the day it is meant to leave: its execution date.
    """
    if row.get("sent_on"):
        return getdate(row.get("sent_on"))
    return getdate(row.get("execution_date") or today or now_datetime().date())


def committed_amount(kefiya_login, start, end, exclude=None):
    """How much is already spoken for in a window, ignoring the given documents.

    Each order counts on its limit_day: the day it was sent, or, not sent
    yet, its execution date.

    :param exclude: one document name or a list of them -- the ones being
        judged right now, which must not be counted against themselves.
    """
    if isinstance(exclude, str):
        exclude = [exclude]
    exclude = [name for name in (exclude or []) if name]
    start, end = getdate(start), getdate(end)

    filters = {
        "kefiya_login": kefiya_login,
        "docstatus": 1,
        "status": ("in", COMMITTED_STATUSES),
    }
    if exclude:
        filters["name"] = ("not in", exclude)
    or_filters = [
        ["execution_date", "between", [start, end]],
        ["sent_on", "between", [str(start) + " 00:00:00",
                                str(end) + " 23:59:59.999999"]],
    ]

    rows = frappe.get_all("Kefiya Transfer", filters=filters,
                          or_filters=or_filters,
                          fields=["total_amount", "execution_date",
                                  "sent_on"])
    return sum(flt(r.total_amount) for r in rows
               if start <= limit_day(r) <= end)


def check_transfer(doc):
    """Would this order be refused by the bank? Say so before it is sent."""
    return check_batch([doc])


def check_batch(docs, on=None):
    """The same question for several orders leaving together.

    ``on`` is the day they count on. Sending, that is today -- also for a
    dated order the bank executes later (see limit_day). Planning, it is
    left empty and the execution date stands in.

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
                              on or docs[0].execution_date
                              or now_datetime().date())
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


# ---------------------------------------------------------------------------
# Partial payments: one payment in parts, over days and accounts.
#
# split_over_limit above moves whole payments of a collective order to later
# days. It cannot help when one payment alone is larger than what the account
# may send -- the case of a large repayment or a purchase price. The functions
# below pay such a payment in parts: backwards from its date, on the
# transfer's own account and on accounts of the same company the user picked,
# each part a transfer of its own (agreed on 01.11.2026, see
# partial_payment_plan for the rule).
# ---------------------------------------------------------------------------


def _limit_of(kefiya_login):
    """(amount, type, days) of a login, or None when the limit is unknown."""
    row = frappe.db.get_value(
        "Kefiya Login", kefiya_login,
        ["transfer_limit_amount", "transfer_limit_type",
         "transfer_limit_days"],
        as_dict=True)
    if not row or flt(row.transfer_limit_amount) <= 0 \
            or not row.transfer_limit_type:
        return None
    return (flt(row.transfer_limit_amount), row.transfer_limit_type,
            cint(row.transfer_limit_days))


def _windows_containing(limit_type, days, day):
    """Every limit window a transfer on ``day`` counts in.

    Day, week and month windows are a partition: one window per day. A period
    of N days is rolling -- the bank looks back N days from each date -- so a
    transfer on ``day`` also counts for the N-1 days after it.
    """
    if limit_type == LIMIT_PERIOD:
        span = max(cint(days), 1)
        return [limit_window(limit_type, days, add_days(day, k))
                for k in range(span)]
    return [limit_window(limit_type, days, day)]


def _room_function(exclude):
    """room_on(login, day, planned) for partial_payment_plan.plan_parts."""
    committed = {}

    def used_in(login, start, end):
        key = (login, str(start), str(end))
        if key not in committed:
            committed[key] = committed_amount(login, start, end,
                                              exclude=exclude)
        return committed[key]

    def room_on(login, day, planned):
        limit = _limit_of(login)
        if not limit:
            return 0
        amount, limit_type, days = limit
        if limit_type == LIMIT_SINGLE:
            # Per order: one part per account and day, each up to the limit.
            taken = any(p[1] == login and getdate(p[0]) == getdate(day)
                        for p in planned)
            return 0 if taken else amount
        free = None
        for start, end in _windows_containing(limit_type, days, day):
            start, end = getdate(start), getdate(end)
            used = used_in(login, start, end) + sum(
                p[2] for p in planned
                if p[1] == login and start <= getdate(p[0]) <= end)
            room = amount - used
            free = room if free is None else min(free, room)
        return max(free or 0, 0)

    return room_on


def _paying_iban(kefiya_login):
    from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
        normalize_iban,
    )

    account = frappe.db.get_value("Kefiya Login", kefiya_login,
                                  "bank_account")
    iban = frappe.db.get_value("Bank Account", account, "iban") \
        if account else None
    return normalize_iban(iban or "")


def _partial_payment_source(transfer_name):
    """The draft to be paid in parts, or an error message."""
    frappe.has_permission("Kefiya Transfer", ptype="write",
                          doc=transfer_name, throw=True)
    doc = frappe.get_doc("Kefiya Transfer", transfer_name)
    if doc.docstatus != 0:
        return doc, _(
            "Only a draft can be paid in parts: after approval the amount is"
            " locked.")
    if len(doc.items) != 1:
        return doc, _(
            "Partial payments are planned for a transfer with one payment."
            " Use \"Split over days\" for a collective order, or enter the"
            " large payment on a transfer of its own.")
    if flt(doc.items[0].amount) <= 0:
        return doc, _("The payment has no amount.")
    if not _limit_of(doc.kefiya_login):
        return doc, _(
            "No transfer limit is known for {0}. It must be kept on the bank"
            " access before a payment can be planned in parts -- the limit is"
            " never guessed.").format(doc.kefiya_login)
    return doc, None


@frappe.whitelist()
def partial_payment_accounts(transfer_name):
    """The accounts a draft may be paid from in parts.

    The transfer's own account first, then every other account of the SAME
    company with a bank access -- the money of another company is never an
    option. An account whose limit is unknown is listed but cannot be picked,
    and the recipient's own account is left out.
    """
    doc, error = _partial_payment_source(transfer_name)
    if error:
        return {"status": "error", "message": error}

    from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
        normalize_iban,
    )

    recipient = normalize_iban(doc.items[0].recipient_iban or "")
    accounts = []
    for login in frappe.get_all(
            "Kefiya Login", fields=["name", "bank_account"],
            order_by="name", limit_page_length=500):
        if not login.bank_account:
            continue
        company = frappe.db.get_value("Bank Account", login.bank_account,
                                      "company")
        if company != doc.company:
            continue
        if _paying_iban(login.name) == recipient:
            continue
        limit = _limit_of(login.name)
        accounts.append({
            "kefiya_login": login.name,
            "bank_account": login.bank_account,
            "own": login.name == doc.kefiya_login,
            "limit": limit[0] if limit else None,
            "limit_type": limit[1] if limit else None,
            "usable": bool(limit),
        })
    accounts.sort(key=lambda a: (not a["own"], a["kefiya_login"]))
    return {"status": "ok", "amount": flt(doc.items[0].amount),
            "due": str(getdate(doc.execution_date or now_datetime().date())),
            "accounts": accounts}


#: How the parts reach the payee (agreed 01.11.2026, both on offer):
#: "bank" -- dated orders (Terminueberweisung): every part carries the due
#: date and is held by the bank; the parts are SENT on different days, and the
#: bank counts the limit on the day of sending, executing all of them on the
#: due date. "kefiya" -- each part is executed on its own day, kept here until
#: then.
MODE_BANK = "bank"
MODE_KEFIYA = "kefiya"
MODES = (MODE_BANK, MODE_KEFIYA)


def _mode(mode):
    mode = mode or MODE_BANK
    if mode not in MODES:
        frappe.throw(_("Unknown way of paying in parts: {0}").format(mode))
    return mode


def _plan(doc, extra_logins):
    """Plan the parts of ``doc`` -- always on the server, from the database.

    The accounts are checked again here: what the browser sends is a wish,
    not a permission.
    """
    from kefiya.utils import partial_payment_plan

    if isinstance(extra_logins, str):
        extra_logins = frappe.parse_json(extra_logins or "[]")
    allowed = {a["kefiya_login"] for a in
               partial_payment_accounts(doc.name).get("accounts", [])
               if a["usable"]}
    logins = [doc.kefiya_login]
    for login in extra_logins or []:
        if login not in allowed:
            frappe.throw(_(
                "{0} cannot be used for this payment: it belongs to another"
                " company, has no known limit, or is the recipient."
            ).format(login))
        if login not in logins:
            logins.append(login)

    due = getdate(doc.execution_date or now_datetime().date())
    today = getdate(now_datetime().date())
    if due < today:
        frappe.throw(_(
            "The execution date {0} has passed. Set the date by which the"
            " payment must be complete, then plan the parts."
        ).format(frappe.utils.formatdate(due)))

    parts, rest = partial_payment_plan.plan_parts(
        flt(doc.items[0].amount), due, today, logins,
        _room_function(exclude=[doc.name]))
    return parts, rest, due


def _part_rows(parts, due, mode):
    """Each part as shown: the day it is sent and the day it is executed."""
    return [{"send_on": str(day),
             "execution_date": str(due if mode == MODE_BANK else day),
             "kefiya_login": login, "amount": amount}
            for day, login, amount in parts]


@frappe.whitelist()
def plan_partial_payments(transfer_name, extra_logins=None, mode=None):
    """What paying this draft in parts would place -- nothing is changed."""
    mode = _mode(mode)
    doc, error = _partial_payment_source(transfer_name)
    if error:
        return {"status": "error", "message": error}
    parts, rest, due = _plan(doc, extra_logins)
    result = {
        "status": "ok" if rest <= 0.004 else "does_not_fit",
        "mode": mode,
        "due": str(due),
        "parts": _part_rows(parts, due, mode),
        "rest": rest,
    }
    if rest > 0.004:
        result["message"] = _(
            "{0} of {1} does not fit the limits between today and {2}."
            " Nothing would be placed: pick another account, or set a later"
            " date."
        ).format(frappe.utils.fmt_money(rest),
                 frappe.utils.fmt_money(doc.items[0].amount),
                 frappe.utils.formatdate(due))
    return result


@frappe.whitelist()
def split_into_partial_payments(transfer_name, extra_logins=None, mode=None):
    """Pay a draft in parts: one draft transfer per part.

    The plan is made again here rather than taken from the browser, so the
    limits are those of this moment. All or nothing: when the amount does not
    fit, nothing is changed. The first part stays on the original document,
    so its name and anything referring to it survive; every part is a draft,
    approved and sent like any transfer, each with its own TAN.

    mode "bank": every part is a dated order for the due date, held by the
    bank; the day to send it is written on it as a comment -- the bank counts
    the limit on that day. mode "kefiya": every part is executed on its own
    day and kept here until then (manage_due_date).
    """
    from kefiya.utils import partial_payment_plan

    mode = _mode(mode)
    doc, error = _partial_payment_source(transfer_name)
    if error:
        return {"status": "error", "message": error}
    parts, rest, due = _plan(doc, extra_logins)
    if rest > 0.004 or not parts:
        return {"status": "error", "message": _(
            "{0} does not fit the limits between today and {1}. Nothing was"
            " changed."
        ).format(frappe.utils.fmt_money(rest), frappe.utils.formatdate(due))}

    row = doc.items[0].as_dict()
    count = len(parts)
    names = []
    for index, (day, login, amount) in enumerate(parts, start=1):
        item = _clean_row(row)
        item["amount"] = amount
        item["purpose"] = partial_payment_plan.part_purpose(
            row.get("purpose"), index, count)
        item["end_to_end_id"] = partial_payment_plan.part_end_to_end_id(
            row.get("end_to_end_id"), index, count)
        if index == 1:
            target = doc
            target.items = []
        else:
            target = frappe.new_doc("Kefiya Transfer")
            target.company = doc.company
            target.standing_order = doc.standing_order
        target.kefiya_login = login
        if mode == MODE_BANK:
            # A dated order at the bank: not instant -- the bank holds it.
            target.execution_date = str(due)
            target.manage_due_date = 0
            target.instant_payment = 0
        else:
            target.execution_date = str(day)
            target.manage_due_date = 1
            target.instant_payment = doc.instant_payment
        target.append("items", item)
        if index == 1:
            target.save()
        else:
            target.insert()
        if mode == MODE_BANK:
            target.add_comment("Comment", _(
                "Part {0} of {1}: send to the bank on {2}. Executed by the"
                " bank on {3}."
            ).format(index, count, frappe.utils.formatdate(day),
                     frappe.utils.formatdate(due)))
        names.append(target.name)

    return {"status": "ok", "documents": names,
            "parts": _part_rows(parts, due, mode),
            "message": _(
                "Paid in {0} parts. Each part is a draft: approve and send it"
                " like any transfer."
            ).format(count)}
