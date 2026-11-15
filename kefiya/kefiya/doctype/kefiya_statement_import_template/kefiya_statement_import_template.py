# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document

from kefiya.utils.statement_import import mapping as statement_mapping


class KefiyaStatementImportTemplate(Document):
	def validate(self):
		try:
			clean = statement_mapping.validate(self.mapping or "")
		except statement_mapping.MappingError as e:
			frappe.throw(str(e), title=frappe._("Invalid column mapping"))
		self.mapping = json.dumps(clean, ensure_ascii=False, indent=1)
