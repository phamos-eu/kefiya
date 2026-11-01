# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class KefiyaSecurityHolding(Document):
    """A point-in-time snapshot of a security held in a securities account
    (Depot). Purely informational -- it never posts to the general ledger.
    One row per (securities account, ISIN, valuation date) builds a time
    series.

    Rows come from the bank (source "Bank", written by
    ``kefiya.utils.securities.refresh_holdings``) or are entered by hand
    (source "Manual") for a depot the bank does not serve over FinTS.
    """

    def validate(self):
        from kefiya.utils.securities import compute_holding_key

        if self.isin:
            self.isin = self.isin.strip().upper()
        if not self.isin:
            frappe.throw(_("Enter the ISIN of the security."))

        if not self.company and self.securities_account:
            self.company = frappe.db.get_value(
                "Kefiya Securities Account", self.securities_account,
                "company")

        quantity, price = flt(self.quantity), flt(self.price)
        if not flt(self.market_value) and quantity and price:
            self.market_value = quantity * price

        # The key is what makes a snapshot unique; a hand-entered row gets it
        # here, a fetched one already carries it.
        self.reference_key = compute_holding_key(
            self.securities_account, self.isin, self.valuation_date)
        duplicate = frappe.db.get_value(
            "Kefiya Security Holding",
            {"reference_key": self.reference_key, "name": ["!=", self.name]},
            "name")
        if duplicate:
            frappe.throw(_(
                "Securities account {0} already holds {1} on {2}: {3}."
            ).format(self.securities_account, self.isin,
                     frappe.format(self.valuation_date, {"fieldtype": "Date"}),
                     duplicate))
