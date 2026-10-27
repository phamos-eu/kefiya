# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


# PSD2 lets banks require a TAN for account history older than this
SCA_FREE_HISTORY_DAYS = 90


class KefiyaImport(Document):
    def validate(self):
        today = getdate()
        from_date = getdate(self.from_date) if self.from_date else None
        to_date = getdate(self.to_date) if self.to_date else None

        errors = []
        if from_date and from_date > today:
            errors.append(_("'From Date' must not be in the future"))
        if to_date and to_date > today:
            errors.append(_("'To Date' must not be in the future"))
        if from_date and to_date and from_date > to_date:
            errors.append(_("'From Date' must not be after 'To Date'"))
        if errors:
            frappe.throw("<br>".join(errors), title=_("Invalid Dates"))

        if (
            from_date
            and (today - from_date).days > SCA_FREE_HISTORY_DAYS
            and self.has_value_changed("from_date")
        ):
            frappe.msgprint(
                _(
                    "'From Date' is more than {0} days in the past. Your bank"
                    " may ask for a TAN or return only the last {0} days."
                ).format(SCA_FREE_HISTORY_DAYS),
                indicator="orange",
                alert=True,
            )
