# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

from frappe.model.document import Document


class KefiyaPlannedPayment(Document):
    """One expected, not-yet-booked payment -- the forecast.

    from_bank = 1: reported by the bank (dated transfer, standing order,
    pending entry) and kept by kefiya.utils.planned_payment alone.
    from_bank = 0: planned here or by another app; kefiya never touches it.

    Never affects the bank balance or reconciliation. A bank row is marked
    Matched once the real Bank Transaction is booked.
    """

    pass
