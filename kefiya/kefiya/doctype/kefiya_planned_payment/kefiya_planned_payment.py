# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

from frappe.model.document import Document


class KefiyaPlannedPayment(Document):
    """A previewed, not-yet-booked payment (scheduled transfer, standing order
    occurrence or pending entry) retrieved from the bank.

    This is a temporary staging record only: it never affects the bank balance
    or reconciliation. It is replaced on every fetch and removed once the real
    Bank Transaction is booked (see kefiya.utils.planned_payment).
    """

    pass
