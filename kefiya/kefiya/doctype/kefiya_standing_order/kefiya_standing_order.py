# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A payment made again and again on a cycle -- at the bank or kept here.

execution_mode "Bank": the bank holds it and executes it; kefiya only shows
its dates in the forecast. "System": kept here; on its date the transfer is
placed in the outgoing payments, approved, and somebody sends it with a TAN
(kefiya.utils.standing_order_execute).

Activating a "System" order approves every one of its executions. That is
the moment the four-eyes rule of Kefiya Settings applies to -- the transfers
it places later are approved in its name and need no second approval each.
Changing what the order pays (amount, recipient, account, schedule) while it
is active is approving it again.

The checks below were the instance's before-save script until 30.10.2026.
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime, nowdate

from kefiya.utils import four_eyes
from kefiya.utils import standing_order_schedule as schedule

#: What an approval covers. A change to any of these on an active order is
#: a new approval.
APPROVED_FIELDS = ("amount", "counterparty_iban", "counterparty_name",
                   "bank_account", "frequency", "day_of_month", "valid_from",
                   "valid_until", "purpose", "execution_mode")


class KefiyaStandingOrder(Document):
    def validate(self):
        self.counterparty_iban = (self.counterparty_iban or "").replace(
            " ", "").upper()
        if self.counterparty_bic:
            self.counterparty_bic = self.counterparty_bic.replace(
                " ", "").upper()

        if not schedule.iban_is_plausible(self.counterparty_iban):
            frappe.throw(_("The recipient's IBAN is not plausible: {0}")
                         .format(self.counterparty_iban))
        if not 1 <= cint(self.day_of_month) <= 31:
            frappe.throw(_("The day of execution must lie between 1 and 31."))
        if self.valid_until and str(self.valid_until) < str(self.valid_from):
            frappe.throw(_("\"Valid Until\" lies before \"Valid From\"."))
        if flt(self.amount) <= 0:
            frappe.throw(_("The amount must be greater than zero."))
        if self.purpose and len(self.purpose) > 140:
            frappe.throw(_("The purpose may hold at most 140 characters."))

        self.check_the_accounts()

        if (self.execution_mode == "System" and self.status == "Active"
                and not frappe.db.exists("Kefiya Login",
                                         {"bank_account": self.bank_account})):
            frappe.throw(_(
                "There is no bank access (Kefiya Login) for {0}, so no"
                " transfer could ever be placed for it."
            ).format(self.bank_account))

        self.next_execution_date = schedule.next_execution(
            self.valid_from, self.day_of_month, self.frequency, nowdate(),
            last_executed_for=self.last_executed_for,
            current=self.next_execution_date,
            valid_until=self.valid_until)

        self.approve_if_activated()

    def check_the_accounts(self):
        if self.counter_account:
            account = frappe.db.get_value(
                "Account", self.counter_account,
                ["company", "is_group", "account_type"], as_dict=True)
            if (not account or account.company != self.company
                    or cint(account.is_group)):
                frappe.throw(_(
                    "The counter account must be a ledger account of {0}."
                ).format(self.company))
            if (account.account_type in ("Receivable", "Payable")
                    and not (self.party_type and self.party)):
                frappe.throw(_(
                    "Please give party type and party for the counter"
                    " account {0}.").format(self.counter_account))

        owner = frappe.db.get_value("Bank Account", self.bank_account,
                                    "company")
        if owner and owner != self.company:
            frappe.throw(_("The paying account belongs to {0}, not to {1}.")
                         .format(owner, self.company))

    def approve_if_activated(self):
        """Activating -- or changing an active order -- is approving it."""
        if self.execution_mode != "System" or self.status != "Active":
            return
        before = None if self.is_new() else self.get_doc_before_save()
        if before and before.status == "Active" and not any(
                str(before.get(f) or "") != str(self.get(f) or "")
                for f in APPROVED_FIELDS):
            return

        user = frappe.session.user
        if self.needs_second_person():
            changed_by = None
            if not self.is_new():
                changed_by = frappe.db.get_value(self.doctype, self.name,
                                                 "modified_by")
            why = four_eyes.conflict(user, self.owner, changed_by)
            if why:
                frappe.throw(_(
                    "Four eyes: you entered or last changed this standing"
                    " order, so someone else has to activate it. Activating"
                    " approves every transfer it will place."),
                    title=_("Approval by a second person"))
        self.approved_by = user
        self.approved_on = now_datetime()

    def needs_second_person(self):
        ceiling = frappe.db.get_single_value("Kefiya Settings",
                                             "self_approval_up_to")
        if not four_eyes.applies(self.amount, ceiling):
            return False
        exempt = frappe.get_all(
            "Kefiya Self Approval Role",
            filters={"parent": "Kefiya Settings",
                     "parentfield": "self_approval_roles"},
            pluck="role")
        return not four_eyes.may_approve_alone(frappe.get_roles(), exempt)
