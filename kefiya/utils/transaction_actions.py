# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The three things one does with a booking one is looking at.

In StarMoney a booking answers to a right-click: print it, forward the money,
mark it for later. All three were missing here -- a Bank Transaction could be
opened and read and that was the end of it.

  print     stays in the browser and needs nothing from this module
  forward   builds the beginning of a Kefiya Transfer from the booking
  mark      sets a follow-up flag on the booking

The follow-up flag is the one that has to survive a submitted document, and
"mark" is not an accounting statement: no amount, no account, no party is
touched by it, only a flag that says somebody wants to come back to this.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

#: SEPA gives the remittance information 140 characters. A purpose built from a
#: description has to be cut somewhere, and cutting it here beats having the
#: bank cut it or refuse the order.
PURPOSE_LIMIT = 140

FOLLOWUP_FIELD = "kefiya_followup"


def _names(names):
    """Accept one name, a JSON list from the browser, or a Python list."""
    if not names:
        return []
    if isinstance(names, str):
        stripped = names.strip()
        if stripped.startswith("["):
            try:
                names = json.loads(stripped)
            except ValueError:
                return [stripped]
        else:
            return [stripped]
    if isinstance(names, (list, tuple, set)):
        return [str(n) for n in names if n]
    return [str(names)]


@frappe.whitelist()
def set_followup(names, flagged=1):
    """Mark bookings for follow-up, or take the mark off again.

    :param names: one Bank Transaction name, or a list of them
    :param flagged: 1 to mark, 0 to clear
    :return: {"updated": int, "flagged": bool, "skipped": [...]}
    """
    flagged = 1 if str(flagged) not in ("0", "False", "false", "") else 0
    result = {"updated": 0, "flagged": bool(flagged), "skipped": []}

    if not frappe.get_meta("Bank Transaction").has_field(FOLLOWUP_FIELD):
        result["reason"] = _("The follow-up field is not installed.")
        return result

    for name in _names(names):
        # Whoever may not write the booking may not flag it either. Checked per
        # document: User Permissions can allow one company's bookings and not
        # another's, and a list action must not slip past that.
        frappe.has_permission("Bank Transaction", ptype="write", doc=name,
                              throw=True)
        current = frappe.db.get_value("Bank Transaction", name, FOLLOWUP_FIELD)
        if int(current or 0) == flagged:
            result["skipped"].append(name)
            continue
        # A submitted document: db_set is the documented way to change a field
        # on one. The field is allow_on_submit and carries no accounting value.
        frappe.get_doc("Bank Transaction", name).db_set(
            FOLLOWUP_FIELD, flagged, update_modified=False)
        result["updated"] += 1

    return result


def _login_for(bank_account):
    """The Kefiya Login that pays from this bank account, if there is one."""
    if not bank_account:
        return None
    rows = frappe.get_all(
        "Kefiya Login", filters={"bank_account": bank_account},
        fields=["name"], limit=1)
    return rows[0]["name"] if rows else None


@frappe.whitelist()
def transfer_from_transaction(name):
    """The beginning of a transfer, built from a booking.

    Money that arrived is money that can be passed on, and the booking already
    holds most of what the transfer needs. What it cannot know is where the
    money should go next: the counterparty of the original booking is offered
    as the starting point, because forwarding it back is a real case and
    typing an IBAN again is how digits get transposed. It is a suggestion in a
    draft, and every field of it can be overwritten before anything is sent.

    Nothing is created here. The caller receives values and opens a new,
    unsaved Kefiya Transfer with them.

    :param name: Bank Transaction name
    :return: dict with kefiya_login, company and one item
    """
    frappe.has_permission("Bank Transaction", ptype="read", doc=name,
                          throw=True)

    txn = frappe.get_doc("Bank Transaction", name)

    # Forwarding is about the money that came in. Where a booking only has an
    # outgoing amount, that is what there is to repeat.
    amount = flt(txn.get("deposit")) or flt(txn.get("withdrawal"))

    login = _login_for(txn.get("bank_account"))
    company = None
    if login:
        # The login knows the company; the booking does not always.
        company = frappe.db.get_value("Kefiya Login", login, "company")

    purpose = (txn.get("description") or "").strip()
    if txn.get("reference_number") and txn.get("reference_number") not in purpose:
        purpose = "{0} {1}".format(purpose, txn.get("reference_number")).strip()
    purpose = " ".join(purpose.split())[:PURPOSE_LIMIT]

    return {
        "source": name,
        "kefiya_login": login,
        "company": company,
        "amount": flt(amount),
        "item": {
            "recipient_name": (txn.get("bank_party_name") or "").strip(),
            "recipient_iban": (txn.get("bank_party_iban") or "").strip(),
            "amount": flt(amount),
            "purpose": purpose,
        },
    }


# --------------------------------------------------------------------------
# Forwarding: four ways, and each one says which it is
# --------------------------------------------------------------------------

#: Money that arrived can be passed on in four directions, and they are not
#: variations of one thing: who may receive it differs, and so does what has
#: to be checked before it is offered.
#:
#:   own_same_company    another account of the same company -- moving money
#:                       inside one book, the everyday case
#:   own_other_company   an account of a DIFFERENT company -- that is a
#:                       payment between two legal entities and wants to be
#:                       named as such, not hidden among the internal moves
#:   other_recipient     somebody else entirely; the recipient is typed in
#:   back_to_sender      straight back where it came from
#:
#: Amount and purpose travel unchanged in all four. That is the point of
#: forwarding: the same money, recognisable on the other side.
FORWARD_OWN_SAME = "own_same_company"
FORWARD_OWN_OTHER = "own_other_company"
FORWARD_OTHER = "other_recipient"
FORWARD_BACK = "back_to_sender"

FORWARD_VARIANTS = (FORWARD_OWN_SAME, FORWARD_OWN_OTHER, FORWARD_OTHER,
                    FORWARD_BACK)

#: What each one is called where somebody reads it.
FORWARD_LABELS = {
    FORWARD_OWN_SAME: "To an own account, same company",
    FORWARD_OWN_OTHER: "To an own account, different company",
    FORWARD_OTHER: "To another recipient",
    FORWARD_BACK: "Back to the sender",
}

#: The two variants that pick an account rather than typing one.
FORWARD_PICKS_ACCOUNT = (FORWARD_OWN_SAME, FORWARD_OWN_OTHER)


def _booking_company(txn):
    """Whose book this booking sits in.

    Taken from the bank account rather than from the login: an account belongs
    to a company whether or not anybody set up a FinTS access for it.
    """
    account = txn.get("bank_account")
    if not account:
        return None
    return frappe.db.get_value("Bank Account", account, "company")


@frappe.whitelist()
def forward_targets(name, variant):
    """The accounts this variant may forward to.

    Empty for the two variants that do not pick an account -- and empty is
    then an answer, not a failure.

    :param name: Bank Transaction name
    :param variant: one of FORWARD_VARIANTS
    :return: [{"name", "account_name", "bank", "iban", "company"}]
    """
    frappe.has_permission("Bank Transaction", ptype="read", doc=name,
                          throw=True)
    if variant not in FORWARD_PICKS_ACCOUNT:
        return []

    txn = frappe.get_doc("Bank Transaction", name)
    company = _booking_company(txn)

    # get_all applies read permissions and User Permissions, so nobody is
    # offered an account they would not be allowed to open.
    rows = frappe.get_all(
        "Bank Account",
        filters={"disabled": 0, "is_company_account": 1},
        fields=["name", "account_name", "bank", "iban", "company"],
        order_by="company, account_name")

    out = []
    for row in rows:
        # Forwarding to the account the money is already on is not a
        # forwarding. The bank says so too, but only after the TAN.
        if row["name"] == txn.get("bank_account"):
            continue
        same = bool(company) and row.get("company") == company
        if variant == FORWARD_OWN_SAME and not same:
            continue
        if variant == FORWARD_OWN_OTHER and same:
            continue
        out.append(row)
    return out


@frappe.whitelist()
def forward_amount(name, variant=FORWARD_BACK, target_account=None):
    """The beginning of a transfer that passes this booking's money on.

    Nothing is created and nothing is sent: the caller receives values and
    opens a new, unsaved Kefiya Transfer with them. Every field can still be
    overwritten before anything reaches a bank.

    The variant is checked rather than trusted. A picker that was filtered in
    the browser is a convenience; the same rule has to hold here, or "same
    company" is a label and not a fact.

    :param name: Bank Transaction name
    :param variant: one of FORWARD_VARIANTS
    :param target_account: Bank Account, for the two variants that pick one
    :return: the dict transfer_from_transaction returns, with the recipient
        filled in according to the variant
    """
    if variant not in FORWARD_VARIANTS:
        frappe.throw(_("Unknown way of forwarding: {0}").format(variant))

    values = transfer_from_transaction(name)
    txn = frappe.get_doc("Bank Transaction", name)

    if variant == FORWARD_BACK:
        # transfer_from_transaction already offers the counterparty.
        values["variant"] = variant
        return values

    if variant == FORWARD_OTHER:
        # Amount and purpose stay; the recipient is for a person to enter.
        # Leaving the previous counterparty in would be the likeliest wrong
        # payment of the four: it looks filled in and it is not the one meant.
        values["item"]["recipient_name"] = ""
        values["item"]["recipient_iban"] = ""
        values["item"]["own_account"] = None
        values["variant"] = variant
        return values

    if not target_account:
        frappe.throw(_("Pick the account the money is to go to."))

    frappe.has_permission("Bank Account", ptype="read", doc=target_account,
                          throw=True)
    target = frappe.get_doc("Bank Account", target_account)

    if target.name == txn.get("bank_account"):
        frappe.throw(_(
            "That is the account the money is already on. A transfer from an"
            " account to itself is not a transfer."))

    company = _booking_company(txn)
    same = bool(company) and target.get("company") == company
    if variant == FORWARD_OWN_SAME and not same:
        frappe.throw(_(
            "{0} belongs to {1}, not to {2}. Use \"To an own account,"
            " different company\" -- a payment between two companies is not an"
            " internal move and should not look like one."
        ).format(target.name, target.get("company") or _("no company"),
                 company or _("no company")))
    if variant == FORWARD_OWN_OTHER and same:
        frappe.throw(_(
            "{0} belongs to the same company. Use \"To an own account, same"
            " company\"."
        ).format(target.name))

    values["item"]["own_account"] = target.name
    values["item"]["recipient_name"] = (target.get("account_name") or "").strip()
    values["item"]["recipient_iban"] = (
        target.get("iban") or "").replace(" ", "").upper()
    values["variant"] = variant
    values["target_company"] = target.get("company")
    return values
