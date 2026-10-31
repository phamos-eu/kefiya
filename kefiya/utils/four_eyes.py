# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Four eyes on approving a transfer.

Approving a Kefiya Transfer -- submitting it -- is what locks the amounts and
the recipients. Submit rights are separate from create rights, and the
transfer's own docstring leaned on that: "the person entering a transfer is not
the person releasing it". It never checked it. Anyone holding both rights --
and the people who release payments usually do -- could type a transfer and
approve it in the same minute, alone. The outbox's "Approve for sending" did
exactly that for a whole selection, asking only whether the user may submit.

So the rule is checked where every path passes: in before_submit. The form,
the outbox's batch approval, "Approve and send" and submit_kefiya_transfer all
end there, and a check placed in one of them would have left the others open.

Two people count as having entered a transfer:

* whoever created it (``owner``), and
* whoever last changed the draft (``modified_by`` as it is stored, not as the
  submit is about to overwrite it). Editing someone else's draft and then
  approving it is entering it -- the amount on it is yours.

Kefiya Settings can relax the rule in two ways, and both start closed:

* ``self_approval_up_to`` -- a ceiling below which the person who entered a
  transfer may approve it alone. It defaults to 0, and a field that did not
  exist before reads as 0, so the rule is on from the moment this code is
  deployed, without anybody having to save the settings first.
* ``self_approval_roles`` -- roles whose holders may always approve alone,
  whatever the amount. Four eyes are not always necessary: a managing director
  who types a payment and releases it carries that responsibility anyway, and
  a second signature from someone who reports to them adds nothing. Empty by
  default; which roles belong here is the site's decision, not the app's.

The decision itself is kept free of Frappe so it can be tested without a bench.
"""

#: Why an approver is turned away. Keys, not messages: the messages are
#: translated where they are raised.
ENTERED = "entered"
CHANGED = "changed"


def conflict(approver, entered_by, changed_by):
    """Why ``approver`` may not approve this transfer, or None.

    ``entered_by`` is the creator, ``changed_by`` the last person who saved
    the draft. Frappe always has a session user, so an empty approver only
    happens in a test that forgot to set one; there is nobody to compare.
    """
    if not approver:
        return None
    if approver == entered_by:
        return ENTERED
    if changed_by and approver == changed_by:
        return CHANGED
    return None


def applies(total, self_approval_up_to):
    """Does the four-eyes rule bind a transfer over ``total``?

    ``self_approval_up_to`` is the largest total the person who entered a
    transfer may approve alone. 0 or empty means never: every transfer needs
    a second person.
    """
    ceiling = _as_amount(self_approval_up_to)
    if ceiling <= 0:
        return True
    return _as_amount(total) > ceiling


def may_approve_alone(user_roles, exempt_roles):
    """Does one of the user's roles release them from the rule?"""
    exempt = {r for r in (exempt_roles or []) if r}
    return any(r in exempt for r in (user_roles or []))


def _as_amount(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        # An unreadable ceiling must not open the door: treat it as "never".
        return 0.0


def enforce(doc):
    """Refuse the submit if the approver also entered the transfer."""
    import frappe
    from frappe import _

    ceiling = frappe.db.get_single_value(
        "Kefiya Settings", "self_approval_up_to")
    if not applies(doc.total_amount, ceiling):
        return

    exempt_roles = frappe.get_all(
        "Kefiya Self Approval Role",
        filters={"parent": "Kefiya Settings",
                 "parentfield": "self_approval_roles"},
        pluck="role")
    if may_approve_alone(frappe.get_roles(), exempt_roles):
        return

    # The stored value, read before this submit overwrites modified_by with
    # the approver. A document that was never saved has only its creator.
    changed_by = None
    if not doc.is_new():
        changed_by = frappe.db.get_value(doc.doctype, doc.name, "modified_by")

    why = conflict(frappe.session.user, doc.owner, changed_by)
    if why == ENTERED:
        frappe.throw(
            _("Four eyes: you entered this transfer, so someone else has to"
              " approve it."),
            title=_("Approval by a second person"))
    if why == CHANGED:
        frappe.throw(
            _("Four eyes: you were the last to change this transfer, so"
              " someone else has to approve it."),
            title=_("Approval by a second person"))
