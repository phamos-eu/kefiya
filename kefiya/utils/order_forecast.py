# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Open purchase orders in the forecast -- the order stage of a payment.

Standard ERPNext, nothing of any one site: a submitted Purchase Order that
is not completed, closed or held, and not fully billed, is money the company
has committed to pay. It stands in the forecast with what is still to come
from it:

    grand total - max(billed, paid)

Billed is the share invoices cover: those are the invoice stage's to
forecast (30). Paid is the advance booked against the order, or what the
bank shows paid on it, whichever is more. The larger of the two, not their
sum: a payment on a partial invoice is billed AND paid, and subtracting it
twice would hide money still owed. So the order line shrinks with every
partial invoice and every partial payment, and goes when the order is
completed, closed or paid in full -- one payment, one line, without the stage
ladder having to pair an order with its invoice by date and amount, which it
could not: an invoice is rarely due on the day the order was placed.

What the bank shows paid on an order: a withdrawal whose text names the
order number. Where it only names the supplier, it counts only when that is
unambiguous -- the supplier has exactly one open order, and the booking is
not allocated to anything yet (an invoice payment is). Each withdrawal counts
for one order at most.

Due date: the first open row of the order's payment schedule, else the
required-by date, else the order date. Account: the company's default bank
account, else none -- a line without one is still counted, just not on an
account.

Lines are kefiya's own (generated_by kefiya_orders, key PO-<order>). A line a
person cancelled stays cancelled; one answered by a booking stays answered.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

DOCTYPE = "Kefiya Planned Payment"
GENERATOR = "kefiya_orders"
PREFIX = "PO-"

#: A remainder below this is rounding, not money still to pay.
MIN_AMOUNT = 0.01

#: Statuses that mean the order is no longer waiting to be paid.
DONE_STATUSES = ("Completed", "Closed", "On Hold", "Cancelled")


def remaining(order, paid_at_bank=0.0):
    """What is still to be paid on an order, as the forecast counts it."""
    total = flt(order.get("grand_total"))
    billed = total * min(flt(order.get("per_billed")), 100) / 100.0
    paid = max(flt(order.get("advance_paid")), flt(paid_at_bank))
    return round(max(total - max(billed, paid), 0), 2)


def _normalize(text):
    return " ".join(str(text or "").lower().split())


def paid_at_bank(orders, transactions, supplier_ibans=None):
    """What the bank shows paid on each order.

    :param orders: open orders (name, supplier, supplier_name,
        transaction_date)
    :param transactions: withdrawals (name, date, withdrawal, description,
        reference_number, bank_party_name, bank_party_iban,
        unallocated_amount)
    :param supplier_ibans: {supplier: set of IBANs}
    :return: {order name: amount}
    """
    supplier_ibans = supplier_ibans or {}
    by_supplier = {}
    for order in orders:
        by_supplier.setdefault(order["supplier"], []).append(order)

    paid = {}
    for tx in sorted(transactions, key=lambda t: str(t.get("date"))):
        text = " ".join(str(tx.get(k) or "") for k in
                        ("description", "reference_number"))
        hit = [o for o in orders if o["name"] and o["name"] in text
               and str(tx.get("date")) >= str(o.get("transaction_date"))]
        if len(hit) != 1:
            hit = []
            if flt(tx.get("unallocated_amount")) > 0:
                party = _normalize(tx.get("bank_party_name"))
                iban = "".join(str(tx.get("bank_party_iban") or "")
                               .split()).upper()
                for supplier, own in by_supplier.items():
                    if len(own) != 1:
                        continue
                    names = {_normalize(own[0].get("supplier_name")),
                             _normalize(supplier)}
                    if (iban and iban in supplier_ibans.get(supplier, ())) \
                            or (party and party in names):
                        if str(tx.get("date")) >= str(
                                own[0].get("transaction_date")):
                            hit.append(own[0])
                if len(hit) != 1:
                    continue
            else:
                continue
        name = hit[0]["name"]
        paid[name] = round(paid.get(name, 0.0) + flt(tx.get("withdrawal")),
                           2)
    return paid


def due_date(order, schedule_rows):
    """First open payment-schedule date, else required-by, else ordered."""
    open_rows = sorted(
        str(r.get("due_date")) for r in schedule_rows or []
        if r.get("due_date") and flt(r.get("outstanding") or r.get(
            "payment_amount")) > 0)
    if open_rows:
        return open_rows[0]
    return str(order.get("schedule_date") or order.get("transaction_date"))


def refresh_purchase_order_lines():
    """Hourly: bring the order lines of the forecast in line with the
    open purchase orders.

    :return: {"created", "updated", "closed"}
    """
    return _refresh()


def _refresh():
    orders = frappe.get_all(
        "Purchase Order",
        filters={"docstatus": 1, "status": ("not in", list(DONE_STATUSES)),
                 "per_billed": ("<", 100)},
        fields=["name", "company", "supplier", "supplier_name", "grand_total",
                "per_billed", "advance_paid", "schedule_date",
                "transaction_date"],
        limit_page_length=0)

    schedules = {}
    names = [o["name"] for o in orders]
    if names:
        for row in frappe.get_all(
                "Payment Schedule",
                filters={"parenttype": "Purchase Order",
                         "parent": ("in", names)},
                fields=["parent", "due_date", "payment_amount",
                        "outstanding"],
                limit_page_length=0):
            schedules.setdefault(row["parent"], []).append(row)

    # Withdrawals since the oldest open order, read once.
    paid = {}
    if orders:
        since = min(str(o["transaction_date"]) for o in orders)
        transactions = frappe.get_all(
            "Bank Transaction",
            filters={"docstatus": 1, "withdrawal": (">", 0),
                     "date": (">=", since)},
            fields=["name", "date", "withdrawal", "description",
                    "reference_number", "bank_party_name", "bank_party_iban",
                    "unallocated_amount"],
            limit_page_length=0)
        supplier_ibans = {}
        for acc in frappe.get_all(
                "Bank Account",
                filters={"party_type": "Supplier",
                         "party": ("in", sorted({o["supplier"]
                                                 for o in orders}))},
                fields=["party", "iban"], limit_page_length=0):
            supplier_ibans.setdefault(acc["party"], set()).add(
                "".join(str(acc.get("iban") or "").split()).upper())
        paid = paid_at_bank(orders, transactions, supplier_ibans)

    default_account = {}
    for acc in frappe.get_all(
            "Bank Account",
            filters={"is_company_account": 1, "is_default": 1, "disabled": 0},
            fields=["name", "company"], limit_page_length=0):
        default_account.setdefault(acc["company"], acc["name"])

    existing = {r["name"]: r for r in frappe.get_all(
        DOCTYPE, filters={"generated_by": GENERATOR},
        fields=["name", "status", "cancellation_reason", "expected_amount",
                "due_date", "bank_account"],
        limit_page_length=0)}

    now = now_datetime()
    out = {"created": 0, "updated": 0, "closed": 0}
    wanted = set()
    for order in orders:
        amount = remaining(order, paid.get(order["name"], 0.0))
        if amount < MIN_AMOUNT:
            continue
        key = PREFIX + order["name"]
        wanted.add(key)
        values = {
            "due_date": getdate(due_date(order, schedules.get(order["name"]))),
            "expected_amount": amount,
            "bank_account": default_account.get(order["company"]),
            # The order number where the reader looks: a payment on it
            # should name it, and this is where it can be copied from.
            "purpose": _("Purchase Order {0}").format(order["name"]),
        }
        line = existing.get(key)
        if line is None:
            frappe.get_doc(dict(
                values,
                doctype=DOCTYPE,
                reference_key=key,
                direction="Outgoing",
                source_kind="Purchase Order",
                status="Open",
                company=order["company"],
                party_type="Supplier",
                party=order["supplier"],
                counterparty_name=order.get("supplier_name"),
                reference_doctype="Purchase Order",
                reference_name=order["name"],
                generated_by=GENERATOR,
                last_generated_on=now,
                from_bank=0,
            )).insert(ignore_permissions=True, ignore_if_duplicate=True)
            out["created"] += 1
            continue
        if line["status"] not in ("Open", "Partially Matched"):
            # Cancelled by a person, replaced by a later stage, booked:
            # every one of those is somebody's answer, kept.
            continue
        changed = {}
        if abs(flt(line["expected_amount"]) - amount) >= MIN_AMOUNT:
            changed["expected_amount"] = amount
        if str(line["due_date"]) != str(values["due_date"]):
            changed["due_date"] = values["due_date"]
        if not line.get("bank_account") and values["bank_account"]:
            changed["bank_account"] = values["bank_account"]
        if changed:
            changed["last_generated_on"] = now
            frappe.db.set_value(DOCTYPE, key, changed)
            out["updated"] += 1

    for key, line in existing.items():
        if key in wanted or line["status"] not in ("Open",
                                                   "Partially Matched"):
            continue
        # Completed, closed, held, cancelled or billed in full: the order no
        # longer stands for money still to pay.
        frappe.db.set_value(DOCTYPE, key, {
            "status": "Cancelled", "cancellation_reason": "Settlement",
            "last_generated_on": now})
        out["closed"] += 1
    return out
