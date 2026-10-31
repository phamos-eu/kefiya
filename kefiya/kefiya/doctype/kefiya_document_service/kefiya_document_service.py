# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class KefiyaDocumentService(Document):
	def validate(self):
		self.validate_endpoint()
		self.validate_accounts()

	def validate_endpoint(self):
		"""An API key travels in the body of every call to this service. Over
		plain http it travels in the clear, so the address is checked here
		rather than only at call time."""
		url = (self.base_url or "").strip()
		if url and not url.startswith("https://"):
			frappe.throw(_("The base URL must be an https address."))
		self.base_url = url.rstrip("/")

	def validate_accounts(self):
		"""One Bank Account may appear once. Twice would file every statement
		of that account twice -- or rather, would try to and report the second
		attempt as already present, which reads like a fault and is not one."""
		seen = set()
		for row in self.accounts or []:
			if row.bank_account in seen:
				frappe.throw(_("Bank Account {0} is listed more than once.")
							 .format(row.bank_account))
			seen.add(row.bank_account)

	def on_update(self):
		if self.enabled and not self.accounts:
			frappe.msgprint(_(
				"The service is switched on but no account is configured."
				" A statement with no bank account has nowhere to go."
			), indicator="orange", alert=True)
