# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Moving money between one's own accounts.

A Kontoübertrag is not a different kind of order. To the bank it is an ordinary
SEPA credit transfer whose recipient happens to be an account of the same
company, and it goes out through the same HKCCS/HKCCM as any other. Nothing in
the FinTS layer needs to know about it.

What was missing is smaller and duller than a new order type: a way to say
"that account over there" instead of typing an IBAN one already owns. Typing it
is how digits get transposed, and a transposed digit on a transfer to oneself
pays a stranger just as reliably as any other.

So this module answers two questions:

    own_accounts()   -- which accounts may I move money to from here?
    is_own_transfer()-- is this row a move between our own accounts?

and the transfer refuses the one case a bank would refuse anyway, but later and
less clearly: paying an account from itself.
"""

import frappe
from frappe import _


def _iban_of(bank_account):
    """The IBAN on a Bank Account, stripped of formatting."""
    if not bank_account:
        return ""
    from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
        normalize_iban,
    )

    return normalize_iban(
        frappe.db.get_value("Bank Account", bank_account, "iban") or "")


@frappe.whitelist()
def own_accounts(kefiya_login=None, company=None):
    """The company's own accounts, minus the one being paid from.

    The paying account is left out on purpose: a transfer from an account to
    itself is not a transfer. The bank rejects it, but only after the order has
    been sent and a TAN spent on it.

    :param kefiya_login: the access the money leaves from (optional)
    :param company: restrict to one company (optional; taken from the login)
    :return: [{"name", "account_name", "bank", "iban", "company"}]
    """
    paying_account = None
    if kefiya_login:
        # Reading the login's own row: the same right the transfer needs.
        frappe.has_permission("Kefiya Login", ptype="read", doc=kefiya_login,
                              throw=True)
        paying_account, login_company = (frappe.db.get_value(
            "Kefiya Login", kefiya_login, ["bank_account", "company"]
        ) or (None, None))
        company = company or login_company

    filters = {"disabled": 0, "is_company_account": 1}
    if company:
        filters["company"] = company

    # get_all applies read permissions and User Permissions, so a user only
    # ever sees the accounts they are allowed to see anyway.
    rows = frappe.get_all(
        "Bank Account", filters=filters,
        fields=["name", "account_name", "bank", "iban", "company"],
        order_by="company, account_name", limit_page_length=0)

    paying_iban = _iban_of(paying_account)
    out = []
    for row in rows:
        if not row.get("iban"):
            # Without an IBAN it cannot be a recipient. Silently left out
            # rather than offered and then refused.
            continue
        if row["name"] == paying_account:
            continue
        if paying_iban and _iban_of(row["name"]) == paying_iban:
            # Two Bank Account records for one real account -- it happens.
            continue
        out.append(row)
    return out


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def own_account_query(doctype, txt, searchfield, start, page_len, filters):
    """Link-field query behind the Own Account picker.

    Same list as own_accounts(), in the shape a Link field wants. The search
    text matches the account name, the bank and the IBAN, because people look
    for an account by whichever of the three they happen to remember.
    """
    filters = filters or {}
    rows = own_accounts(kefiya_login=filters.get("kefiya_login"),
                        company=filters.get("company"))

    needle = (txt or "").strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in (r.get("account_name") or "").lower()
                or needle in (r.get("bank") or "").lower()
                or needle in (r.get("iban") or "").lower()
                or needle in (r.get("name") or "").lower()]

    start = int(start or 0)
    page_len = int(page_len or 20)
    return [(r["name"], r.get("account_name") or "", r.get("iban") or "")
            for r in rows[start:start + page_len]]


def is_own_transfer(row, company=None):
    """Does this row pay an account we own?

    :param row: a Kefiya Transfer Item
    :return: the Bank Account name, or None
    """
    if getattr(row, "own_account", None):
        return row.own_account

    from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
        normalize_iban,
    )

    iban = normalize_iban(getattr(row, "recipient_iban", ""))
    if not iban:
        return None

    filters = {"disabled": 0, "is_company_account": 1}
    if company:
        filters["company"] = company
    for other in frappe.get_all("Bank Account", filters=filters,
                                fields=["name", "iban"],
                                limit_page_length=0):
        if normalize_iban(other.get("iban") or "") == iban:
            return other["name"]
    return None


def fill_from_own_account(row):
    """Copy name and IBAN of the chosen own account into the row.

    Only fills what is empty or stale: whatever the user typed over it stays.
    Runs on the server as well as in the browser, because a row may arrive
    from an import or from another script that never opened a form.
    """
    if not getattr(row, "own_account", None):
        return False

    account = frappe.db.get_value(
        "Bank Account", row.own_account,
        ["account_name", "iban", "branch_code"], as_dict=True)
    if not account:
        return False

    from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
        normalize_iban,
    )

    changed = False
    iban = normalize_iban(account.get("iban") or "")
    if iban and normalize_iban(getattr(row, "recipient_iban", "")) != iban:
        row.recipient_iban = iban
        changed = True
    if not getattr(row, "recipient_name", None):
        row.recipient_name = account.get("account_name") or row.own_account
        changed = True
    return changed


def refuse_paying_yourself(doc):
    """Stop a transfer that pays the account it is drawn from.

    The bank refuses it too -- but after the order was sent and a TAN spent
    on it, which is one strong authentication wasted and a failure the user
    has to interpret.
    """
    paying_account = frappe.db.get_value(
        "Kefiya Login", doc.kefiya_login, "bank_account")
    paying_iban = _iban_of(paying_account)
    if not paying_iban:
        return

    from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
        normalize_iban,
    )

    for row in doc.items:
        if normalize_iban(row.recipient_iban or "") == paying_iban:
            frappe.throw(_(
                "Row {0}: this pays the account the money is drawn from."
                " A transfer to itself is not a transfer."
            ).format(row.idx))
