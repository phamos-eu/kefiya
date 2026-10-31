# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

from frappe.model.document import Document


class KefiyaSecurityHolding(Document):
    """A point-in-time snapshot of a security held in a FinTS securities
    account (Depot). Purely informational -- it never posts to the general
    ledger. One row per (login, ISIN, valuation date) builds a time series.
    """

    pass
