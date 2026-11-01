# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.model.document import Document


class KefiyaPlannedPayment(Document):
    """One expected, not-yet-booked payment -- the forecast.

    from_bank = 1: reported by the bank (dated transfer, standing order,
    pending entry) and kept by kefiya.utils.planned_payment alone.
    from_bank = 0: planned here or by another app; kefiya only marks it
    superseded when a higher stage of the same payment appears
    (kefiya.utils.forecast_stages).

    Never affects the bank balance or reconciliation. A bank row is marked
    Matched once the real Bank Transaction is booked.
    """

    def before_insert(self):
        # Every writer brings its own key; a person adding a line by hand
        # has none to bring. The key is the name, so it must exist before
        # naming -- which runs after this hook.
        if not self.reference_key:
            self.reference_key = "MAN-" + frappe.generate_hash(length=10)
            self.is_manual = 1
            if not self.source_kind:
                self.source_kind = "Manual"
        if self.bank_account and not self.company:
            self.company = frappe.db.get_value(
                "Bank Account", self.bank_account, "company")
