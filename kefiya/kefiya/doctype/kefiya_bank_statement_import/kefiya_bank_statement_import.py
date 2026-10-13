# Copyright (c) 2024, jHetzer and contributors
# For license information, please see license.txt

"""Import a statement file.

This document is a form and a status; the reading lives in
statement_formats.py and the booking in statement_import.py.

It used to hold both, twice: two near-identical methods that differed only in
the file's encoding, each with its own amount parser -- one of which divided
by a hundred whenever an amount carried no decimal separator. The encoding is
now handled once, the layout that needed them is declared as a profile, and
those 250 lines are gone with the bug in them.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from kefiya.utils import auszug_einlesen, statement_import


class KefiyaBankStatementImport(Document):

	@frappe.whitelist()
	def plan_import(self, file_url, bank_account=None):
		"""Report what an import would do, without writing anything.

		A bulk insert of bookings is the kind of write that should be looked
		at before it happens, not explained afterwards: which format was
		recognised, how the first bookings were understood -- above all which
		way round the signs came out -- and how many rows are already present.
		"""
		frappe.has_permission(
			"Kefiya Bank Statement Import", ptype="read", doc=self, throw=True)
		return statement_import.without_entries(
			statement_import.plan(file_url, bank_account or self.bank_account))

	@frappe.whitelist()
	def start_import(self, file_url, bank_account=None, company=None):
		"""Book the file.

		:param company: accepted and unused. The company comes from each
			booking's own Bank Account -- a file covering twenty accounts
			covers a dozen companies with them, and one field on this form
			cannot speak for all of them.
		"""
		# Permission gate: a whitelisted document method is callable by anyone
		# who may read the document; importing creates Bank Transactions.
		frappe.has_permission(
			"Kefiya Bank Statement Import", ptype="write", doc=self, throw=True)

		summary = statement_import.plan(
			file_url, bank_account or self.bank_account, dry_run=False)

		self.db_set('payload_count', summary.get("total") or 0)
		self.db_set('imported_records', summary.get("created") or 0)

		if not summary.get("profile"):
			self.update_status('Error')
		elif summary.get("failed") or summary.get("unreadable"):
			self.update_status('Partial Success')
		else:
			self.update_status('Success')

		if self.submit_after_success:
			# Deliberately not submitted. Submitting a booking is an approval,
			# and an approval is given at the document, not by a checkbox on an
			# import that ran unattended. Said out loud rather than ignored, so
			# the setting does not look as if it worked.
			frappe.msgprint(_(
				"{0} bookings were created as drafts. Submitting them is left"
				" to the Bank Transaction list."
			).format(summary.get("created") or 0))

		return statement_import.without_entries(summary)

	@frappe.whitelist()
	def plan_rebuild(self, file_url, bank_account=None):
		"""What rebuilding the account from this statement would do.

		Not an import: an import adds what is missing and keeps what is
		there. A rebuild replaces the account's bookings for the period the
		statement speaks for, and says beforehand how many go and how many
		come -- see auszug_einlesen.
		"""
		frappe.has_permission(
			"Kefiya Bank Statement Import", ptype="write", doc=self, throw=True)
		return auszug_einlesen.plan(file_url, bank_account or self.bank_account)

	@frappe.whitelist()
	def start_rebuild(self, file_url, bank_account=None):
		frappe.has_permission(
			"Kefiya Bank Statement Import", ptype="write", doc=self, throw=True)
		return auszug_einlesen.start(
			file_url, bank_account or self.bank_account, docname=self.name)

	def update_status(self, status):
		self.db_set('status', status)
		frappe.publish_realtime(
			'update_import_status', {'docname': self.name, 'status': status},
			user=frappe.session.user)
