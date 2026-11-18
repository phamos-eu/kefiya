# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A transfer the bank has booked is sent -- whatever kefiya concluded.

Every order kefiya sends carries an End-To-End ID built from its own name
(``KEF-TRF-2026-00012-1``), and banks return it in the statement text
("EREF: ..."). When that ID turns up on a withdrawal of the same amount, the
bank has executed the order. That is the bank's verdict, and it outranks
anything kefiya concluded while sending.

Why this exists. On 16.11.2026 a Volksbank answered an instant payment with
0020, 0010 and 3076 ("strong authentication not necessary"), asked for no TAN
and booked the order the same day. kefiya read the missing TAN as a missing
signature, called the order "Not sent" and left it on the outgoing page as
due -- the one state that invites a second send. The signature rule is fixed
(fints_response.signature_waived), but a rule at send time can be wrong again
in a way nobody has met yet. The statement is the check that does not depend
on having foreseen it.

Runs after every import, independent of the auto-reconcile setting: it books
nothing, it only stops a paid order from asking to be paid again.
"""

import re

#: States in which an order may still be sent. "Scheduled at Bank" is not
#: among them: the bank holds that order and executes it on its date.
OPEN_STATES = ("Approved", "Due", "Failed", "On Hold")

#: How far back a statement is searched, whatever window was imported. An
#: order wrongly left open is found on the next fetch even when that fetch
#: only imported today.
LOOK_BACK_DAYS = 30


def mark_seen(bank_account, from_date=None, to_date=None):
    """Mark open orders of this account as sent where the statement shows them.

    :return: list of transfer names that were marked
    """
    import frappe
    from frappe.utils import add_days, getdate, nowdate

    if not bank_account:
        return []

    logins = frappe.get_all("Kefiya Login",
                            filters={"bank_account": bank_account},
                            pluck="name")
    if not logins:
        return []

    open_orders = frappe.get_all(
        "Kefiya Transfer",
        filters={"docstatus": 1, "kefiya_login": ("in", logins),
                 "status": ("in", OPEN_STATES)},
        pluck="name")
    if not open_orders:
        return []

    items = frappe.get_all(
        "Kefiya Transfer Item",
        filters={"parent": ("in", open_orders),
                 "parenttype": "Kefiya Transfer",
                 "end_to_end_id": ("!=", "")},
        fields=["parent", "end_to_end_id", "amount"],
        parent_doctype="Kefiya Transfer")
    if not items:
        return []

    start = add_days(nowdate(), -LOOK_BACK_DAYS)
    if from_date and getdate(from_date) < getdate(start):
        start = from_date
    txns = frappe.get_all(
        "Bank Transaction",
        filters={"bank_account": bank_account, "docstatus": 1,
                 "withdrawal": (">", 0), "date": (">=", getdate(start))},
        fields=["name", "date", "withdrawal", "description",
                "reference_number"])

    marked = []
    for item in items:
        if item.parent in marked:
            continue
        txn = find_booking(item.end_to_end_id, item.amount, txns)
        if txn:
            _mark(item.parent, item.end_to_end_id, txn)
            marked.append(item.parent)
    return marked


def find_booking(end_to_end_id, amount, txns):
    """The one withdrawal carrying exactly this ID and this amount, or None.

    Exactly: ``KEF-TRF-2026-00056-1`` must not be found inside
    ``KEF-TRF-2026-00056-10``. And the amount must match -- an ID on a
    booking of another size is not this payment. Two candidates are no
    answer either.
    """
    if not end_to_end_id:
        return None
    pattern = re.compile(re.escape(end_to_end_id) + r"(?![0-9A-Za-z])")
    found = []
    for txn in txns:
        text = "{0} {1}".format(txn.get("description") or "",
                                txn.get("reference_number") or "")
        if pattern.search(text) \
                and abs(_number(txn.get("withdrawal")) - _number(amount)) \
                <= 0.005:
            found.append(txn)
    return found[0] if len(found) == 1 else None


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _mark(transfer_name, end_to_end_id, txn):
    import frappe
    from frappe import _
    from frappe.utils import getdate

    doc = frappe.get_doc("Kefiya Transfer", transfer_name)
    doc.db_set("status", "Sent")
    if not doc.sent_on:
        doc.db_set("sent_on", getdate(txn.get("date")))
    if doc.get("vop_pending"):
        doc.db_set("vop_pending", 0)
    doc.add_comment("Info", _(
        "Booked by the bank on {0} ({1}, End-To-End ID {2}). Marked as sent"
        " from the account statement."
    ).format(frappe.utils.formatdate(txn.get("date")), txn.get("name"),
             end_to_end_id))

    # The reminders to release it are answered: the money is gone.
    for todo in frappe.get_all(
            "ToDo",
            filters={"reference_type": "Kefiya Transfer",
                     "reference_name": transfer_name, "status": "Open"},
            pluck="name"):
        frappe.db.set_value("ToDo", todo, "status", "Closed")
