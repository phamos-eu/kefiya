# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Does the ledger agree with the bank about what an account IS?

The app learns the kind of an account from the bank and keeps it on the Kefiya
Login. The ledger learns it from whoever set the account up. Nothing has ever
compared the two, and they drift in one direction: everything a bank fetch
touches gets created as a bank account, because that is what the setup does.

The cost is not cosmetic. `Account.account_type = "Bank"` is what puts a figure
into liquidity -- the balance sheet's cash, the cash flow, the reconciliation
tool. A Genossenschaftsanteil is money that exists and cannot be spent: the
membership has to be terminated, years ahead. An Aval is a granted line that
was never money at all. Counted as bank money they overstate what can be paid
out, which is the one number nobody may guess at.

That drift is silent, and it shows: of five Darlehen on this instance four had
been corrected to a liability by hand and the fifth had not. Nothing said so.
The kind was right on all five the whole time; nothing asked.

This module only ASKS. It does not repost anything: which account a
Genossenschaftsanteil belongs under is a decision for the person who keeps the
books, and moving an account that already carries entries is their move, not
the app's.
"""

import frappe
from frappe import _

from kefiya.utils import account_kind
from kefiya.utils.ledger_rule import LIQUID, NOT_LIQUID, is_misclassified

__all__ = ["LIQUID", "NOT_LIQUID", "is_misclassified",
           "ledger_complaint", "misclassified"]


def ledger_complaint(kefiya_login):
    """One sentence about this login's ledger account, or None when it agrees.

    Says what the account is and what the books make of it. Deliberately short
    and deliberately without a recommendation: naming the right target account
    is bookkeeping, and this is a fetch log.

    :param kefiya_login: name of a Kefiya Login
    :return: str or None
    """
    kind = account_kind.kind_of(kefiya_login)
    if kind not in NOT_LIQUID:
        return None

    account = frappe.db.get_value(
        "Kefiya Login", kefiya_login, "erpnext_account")
    if not account:
        return None

    account_type = frappe.db.get_value("Account", account, "account_type")
    if not is_misclassified(kind, account_type):
        return None

    return _("counted as cash in the books, but this is {0}").format(
        _(kind))


def misclassified():
    """Every login whose ledger account counts as cash and should not.

    The whole list rather than one account, because the drift is a pattern:
    seeing one of eight says nothing about the other seven.

    get_all rather than get_list: nothing here is displayed to a user who has
    not asked for it, and a reader without the right to see every Account
    should get a shorter list, never a wider one.

    :return: [{"login", "kind", "account", "account_type"}]
    """
    if not frappe.get_meta("Kefiya Login").has_field("account_kind"):
        return []

    rows = frappe.get_all(
        "Kefiya Login",
        filters={"account_kind": ["in", list(NOT_LIQUID)],
                 "erpnext_account": ["is", "set"]},
        fields=["name", "account_kind", "erpnext_account"],
        limit_page_length=0)
    if not rows:
        return []

    types = {r["name"]: r["account_type"] for r in frappe.get_all(
        "Account", filters={"name": ["in", [r["erpnext_account"]
                                            for r in rows]]},
        fields=["name", "account_type"], limit_page_length=0)}

    return [{"login": r["name"],
             "kind": r["account_kind"],
             "account": r["erpnext_account"],
             "account_type": types.get(r["erpnext_account"])}
            for r in rows
            if is_misclassified(r["account_kind"],
                                types.get(r["erpnext_account"]))]
