# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Correcting the forecast by hand: cancel a line, bring it back, add one.

Nearly every line of the forecast is written by something -- the bank fetch,
an invoice run, a plan of another app -- and every one of those writes its
lines again on its next run, under the same key. Deleting a line therefore
corrects nothing: it comes back. Cancelling it does, because every writer
leaves a cancelled line alone:

  * kefiya's bank fetch reopens only lines its own sweep cancelled (no
    reason); a line a person cancelled carries one;
  * the stage ladder reopens only lines it replaced itself;
  * the other writers already treat Cancelled as settled.

So "delete" in the user's words is a cancellation with a reason, and stays
reversible. A line added by hand is a plan (source_kind Manual): when the
bank later reports the same payment, the stage ladder replaces it.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

DOCTYPE = "Kefiya Planned Payment"

#: What a person may give as the reason. "Superseded by higher stage" is the
#: stage ladder's, not a person's: it is what the ladder reopens on its own.
REASONS = ("Duplicate", "Settlement", "Waiver", "Uncollectible", "Reversal",
           "Other")

#: Lines in these states are answered by a booking and are not corrected
#: here -- the booking is the bank's last word.
SETTLED = ("Matched", "Partially Matched", "Booked")


def _names(names):
    if isinstance(names, str):
        names = frappe.parse_json(names) if names.startswith("[") \
            else [names]
    return [n for n in (names or []) if n]


@frappe.whitelist()
def cancel_lines(names, reason, note=None):
    """Take lines out of the forecast.

    :param names: one name or a list (JSON) of Kefiya Planned Payment names
    :param reason: one of REASONS
    :param note: optional free text, kept as a comment on each line
    :return: {"cancelled": [...], "skipped": [{"name", "why"}]}
    """
    if reason not in REASONS:
        frappe.throw(_("Choose a reason for the cancellation."))
    done, skipped = [], []
    for name in _names(names):
        doc = frappe.get_doc(DOCTYPE, name)
        frappe.has_permission(DOCTYPE, ptype="write", doc=doc, throw=True)
        if doc.status in SETTLED:
            skipped.append({"name": name, "why": _(
                "already answered by a booking")})
            continue
        if doc.status == "Cancelled" and doc.cancellation_reason == reason:
            continue
        doc.status = "Cancelled"
        doc.cancellation_reason = reason
        doc.save()
        if note:
            doc.add_comment("Comment", note)
        done.append(name)
    return {"cancelled": done, "skipped": skipped}


@frappe.whitelist()
def reopen_lines(names):
    """Bring cancelled or expired lines back into the forecast.

    :return: {"reopened": [...], "skipped": [{"name", "why"}]}
    """
    done, skipped = [], []
    for name in _names(names):
        doc = frappe.get_doc(DOCTYPE, name)
        frappe.has_permission(DOCTYPE, ptype="write", doc=doc, throw=True)
        if doc.status not in ("Cancelled", "Expired"):
            skipped.append({"name": name, "why": _("not cancelled")})
            continue
        doc.status = "Open"
        doc.cancellation_reason = None
        doc.superseded_by = None
        doc.save()
        done.append(name)
    return {"reopened": done, "skipped": skipped}


@frappe.whitelist()
def create_line(bank_account, due_date, amount, direction="Outgoing",
                counterparty_name=None, counterparty_iban=None,
                purpose=None):
    """Add a payment to the forecast by hand.

    :return: the new line's name
    """
    frappe.has_permission(DOCTYPE, ptype="create", throw=True)
    if direction not in ("Incoming", "Outgoing"):
        frappe.throw(_("Direction must be Incoming or Outgoing."))
    if flt(amount) <= 0:
        frappe.throw(_("The amount must be greater than zero."))
    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "bank_account": bank_account,
        "due_date": getdate(due_date),
        "expected_amount": flt(amount),
        "direction": direction,
        "counterparty_name": counterparty_name,
        "counterparty_iban": counterparty_iban,
        "purpose": purpose,
        "source_kind": "Manual",
        "is_manual": 1,
        "status": "Open",
    })
    doc.insert()
    return doc.name
