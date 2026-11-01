# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Who may see what is on a bank account.

A balance is not everybody's business: the person who enters invoices does
not need to know what the company holds. Frappe already has the means for
that -- a field's permission level. A site that puts the balance fields
(Bank Account.custom_account_balance and its neighbours, Bank
Transaction.bank_balance) on a level above 0 and grants that level only to
some roles has said who may read them, and forms, lists and reports follow.

What does not follow is code that reads the fields itself and hands the
number on: the account standing shown when a transfer is entered, the fetch
log that says what the bank reported. Those ask here first. A site that left
the fields on level 0 has said nothing, and everybody keeps seeing them.
"""

import frappe

#: The field whose permission level decides. The others are kept on the same
#: level by whoever configures the site; one field answers for all.
DOCTYPE = "Bank Account"
FIELD = "custom_account_balance"


def may_see_balances(user=None):
    """May this user read bank balances?"""
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    meta = frappe.get_meta(DOCTYPE)
    field = meta.get_field(FIELD)
    if not field or not int(field.permlevel or 0):
        return True
    return int(field.permlevel) in meta.get_permlevel_access(
        "read", user=user)
