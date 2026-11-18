# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class KefiyaPayPalAccount(Document):
	def validate(self):
		# Switched on without credentials, the daily run would fail every
		# night with the same message -- refused here once instead.
		if self.enabled and not (self.client_id and self.client_secret):
			frappe.throw(_("Enter Client ID and Secret before enabling."))
