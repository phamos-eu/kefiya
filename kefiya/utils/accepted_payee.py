# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Remembering that a person approved a payee, so they are asked once.

The rule this serves is in vop_rule; this is only the reading and writing of
it. Two functions, both narrow on purpose:

    was_accepted(iban, name)   did somebody already look at this exact payee
                               at this exact account and say yes
    remember(...)              they just did

What is deliberately NOT here: any way to add a payee without a payment having
been approved. A row in this table is a record of a person's decision, and one
that appeared some other way would be a lie about that person -- which is why
the DocType gives nobody create or write rights either.
"""

import frappe
from frappe.utils import now_datetime

from kefiya.utils.vop_rule import payee_key

ACCEPTED_PAYEE = "Kefiya Accepted Payee"


def was_accepted(iban, payee_name):
    """Has this exact payee at this exact account been approved before?

    Both halves have to match, normalised: a different spelling or a different
    account is a payee nobody looked at.

    Never raises, and answers False on any doubt. The cost of a wrong False is
    one confirmation somebody has already given once; the cost of a wrong True
    is a payment nobody looked at.
    """
    key = payee_key(iban, payee_name)
    if not key.strip("|"):
        return False
    try:
        return bool(frappe.db.exists(ACCEPTED_PAYEE, {"match_key": key}))
    except Exception:
        return False


def remember(iban, payee_name, vop_result=None, bank_name=None,
             kefiya_login=None):
    """Write down that somebody approved this payee.

    Called only after an approval actually went through, so the row means what
    it says. Best-effort: a payment that has already left must not fail over
    the bookkeeping of it -- the next order to the same payee would simply ask
    again, which is the safe direction.

    ignore_permissions, and deliberately: the approving user has the right to
    send the money, and nobody has write rights on this DocType by design. The
    record of who decided is in accepted_by.
    """
    key = payee_key(iban, payee_name)
    if not key.strip("|"):
        return None
    try:
        if frappe.db.exists(ACCEPTED_PAYEE, {"match_key": key}):
            return None
        doc = frappe.get_doc({
            "doctype": ACCEPTED_PAYEE,
            "payee_name": payee_name,
            "iban": (iban or "").replace(" ", "").upper(),
            "match_key": key,
            "vop_result": vop_result or "",
            "bank_name": bank_name or "",
            "accepted_by": frappe.session.user,
            "accepted_on": now_datetime(),
            "kefiya_login": kefiya_login,
        })
        doc.insert(ignore_permissions=True)
        return doc.name
    except Exception:
        frappe.log_error(
            title="Kefiya: remembering the approved payee failed",
            message=frappe.get_traceback())
        return None
