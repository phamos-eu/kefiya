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
                 "parenttype": "Kefiya Transfer"},
        fields=["parent", "end_to_end_id", "amount", "recipient_iban",
                "purpose"],
        parent_doctype="Kefiya Transfer")
    if not items:
        return []
    # One booking per order: without the ID, only an order of ONE payment can
    # be told apart by IBAN, amount and purpose.
    payments = {}
    for item in items:
        payments[item.parent] = payments.get(item.parent, 0) + 1
    dates = dict(frappe.get_all(
        "Kefiya Transfer", filters={"name": ("in", open_orders)},
        fields=["name", "execution_date"], as_list=True))

    start = add_days(nowdate(), -LOOK_BACK_DAYS)
    if from_date and getdate(from_date) < getdate(start):
        start = from_date
    txns = frappe.get_all(
        "Bank Transaction",
        # Drafts too: a booking is the bank's word whether or not somebody
        # has submitted it here yet.
        filters={"bank_account": bank_account, "docstatus": ("<", 2),
                 "withdrawal": (">", 0), "date": (">=", getdate(start))},
        fields=["name", "date", "withdrawal", "description",
                "reference_number", "bank_party_iban"])

    marked, used = [], set()
    for item in items:
        if item.parent in marked:
            continue
        free = [t for t in txns if t.get("name") not in used]
        txn = find_booking(item.end_to_end_id, item.amount, free)
        how = item.end_to_end_id
        if not txn and payments.get(item.parent) == 1:
            txn = find_booking_without_id(
                item.recipient_iban, item.amount, item.purpose,
                dates.get(item.parent), free)
            how = None
        if txn:
            _mark(item.parent, how, txn)
            marked.append(item.parent)
            used.add(txn.get("name"))
    return marked


#: How far a booking may lie from the order's execution date and still be
#: that order: a few days before (the bank executed on release, ahead of the
#: date) and a week after (weekends, holidays, a late fetch).
DAYS_BEFORE = 5
DAYS_AFTER = 7


def _compact(text):
    """Letters and digits only, upper case: how a purpose is compared."""
    return re.sub(r"[^0-9A-Z]", "", str(text or "").upper())


def find_booking_without_id(iban, amount, purpose, execution_date, txns):
    """The one withdrawal that is this order although it names no ID, or None.

    Not every bank writes the End-To-End ID into the statement. On
    18.11.2026 a BW Bank order was released in the bank's app four minutes
    after kefiya had stopped asking, booked the next morning -- and stayed
    "Due" here, because its booking carried no ID to recognise it by. The
    one action that invited then was a second send of the same money.

    All four have to agree, and only one booking may qualify:

    * the counter IBAN is the recipient's,
    * the amount is the same to the cent,
    * the booking text contains the purpose (compared on letters and digits
      -- banks drop spaces, cut lines and append their own date),
    * the booking date lies around the execution date.

    A purpose too short to tell anything apart (under 8 characters) is no
    evidence: a recurring payment of the same amount to the same payee
    would match it.
    """
    import datetime

    target = _compact(iban)
    words = _compact(purpose)[:40]
    if not target or len(words) < 8 or not execution_date:
        return None
    try:
        due = datetime.date.fromisoformat(str(execution_date)[:10])
    except ValueError:
        return None
    lower = due - datetime.timedelta(days=DAYS_BEFORE)
    upper = due + datetime.timedelta(days=DAYS_AFTER)
    found = []
    for txn in txns:
        if _compact(txn.get("bank_party_iban")) != target:
            continue
        if abs(_number(txn.get("withdrawal")) - _number(amount)) > 0.005:
            continue
        try:
            day = datetime.date.fromisoformat(str(txn.get("date"))[:10])
        except ValueError:
            continue
        if not lower <= day <= upper:
            continue
        if words not in _compact(txn.get("description")):
            continue
        found.append(txn)
    return found[0] if len(found) == 1 else None


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
    # Held back here, but the bank has it: holding means nothing any more.
    if doc.get("on_hold"):
        doc.db_set("on_hold", 0)
    if not doc.sent_on:
        doc.db_set("sent_on", getdate(txn.get("date")))
    if doc.get("vop_pending"):
        doc.db_set("vop_pending", 0)
    if end_to_end_id:
        doc.add_comment("Info", _(
            "Booked by the bank on {0} ({1}, End-To-End ID {2}). Marked as"
            " sent from the account statement."
        ).format(frappe.utils.formatdate(txn.get("date")), txn.get("name"),
                 end_to_end_id))
    else:
        doc.add_comment("Info", _(
            "Booked by the bank on {0} ({1}): recipient IBAN, amount and"
            " purpose agree, the bank's statement names no End-To-End ID."
            " Marked as sent from the account statement."
        ).format(frappe.utils.formatdate(txn.get("date")), txn.get("name")))

    # The reminders to release it are answered: the money is gone.
    for todo in frappe.get_all(
            "ToDo",
            filters={"reference_type": "Kefiya Transfer",
                     "reference_name": transfer_name, "status": "Open"},
            pluck="name"):
        frappe.db.set_value("ToDo", todo, "status", "Closed")
