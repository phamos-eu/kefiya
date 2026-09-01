import frappe
from frappe.utils import flt
from datetime import datetime
from frappe import _
from sepaxml import SepaTransfer


def _build_sepa_xml(payment_request_name):
	"""
	Build SEPA pain.001 XML for an Outward Payment Request (credit transfer).
	Returns (xml_string, None) on success or (None, error_message) on failure.
	"""
	if not SepaTransfer:
		return None, _("SEPA XML generation is not available (install sepaxml).")

	doc = frappe.get_doc("Payment Request", payment_request_name)
	if doc.payment_request_type != "Outward":
		return None, _("SEPA XML (pain.001) is only supported for Outward payment requests.")

	# Approval gate: a pain.001 file is a ready-to-execute payment
	# instruction. It must only be produced for a submitted -- i.e. approved
	# via the "Payment Request Freigabe (Ausgang)" 4-eyes workflow -- request.
	# Refusing drafts here is what makes the submit/approval gate meaningful:
	# without it, calling the whitelisted export endpoint on an unapproved draft
	# would hand out an executable SEPA file and bypass the approval entirely.
	if doc.docstatus != 1:
		return None, _(
			"SEPA XML (pain.001) can only be generated for a submitted "
			"(approved) Payment Request."
		)

	company_bank = frappe.get_doc("Bank Account", doc.company_bank_account)
	party_bank = frappe.get_doc("Bank Account", doc.bank_account)
	invoicedoc = frappe.get_doc(doc.reference_doctype, doc.reference_name)
	partydoc = frappe.get_doc(doc.party_type, doc.party)
	partyname = (
		partydoc.get("customer_name")
		if doc.party_type == "Customer"
		else partydoc.get("supplier_name")
		if doc.party_type == "Supplier"
		else partydoc.get("party_name", doc.party)
	)
	company_name = frappe.get_cached_value("Company", doc.company, "company_name") or doc.company

	if not company_bank.iban:
		return None, _("Company bank account {0} has no IBAN.").format(doc.company_bank_account)
	if not party_bank.iban:
		return None, _("Party bank account {0} has no IBAN.").format(doc.bank_account)

	# Pay the Payment Request amount, never more than what is still outstanding
	# on the invoice -- avoids overpaying a partially settled invoice.
	pay_amount = flt(doc.grand_total) or flt(invoicedoc.grand_total)
	outstanding = flt(invoicedoc.get("outstanding_amount"))
	if outstanding and pay_amount > outstanding:
		pay_amount = outstanding
	amount_cents = int(round(pay_amount * 100))
	if amount_cents <= 0:
		return None, _("Payment amount must be greater than zero.")

	execution_date = doc.transaction_date
	if execution_date:
		if isinstance(execution_date, str):
			execution_date = datetime.strptime(execution_date, "%Y-%m-%d").date()
	else:
		execution_date = datetime.now().date()

	bill_no = invoicedoc.get("bill_no") if doc.reference_doctype == "Purchase Invoice" else ""
	zweck = "Rechnung " + (bill_no if bill_no else doc.name)
	kategorie = (doc.mode_of_payment or "").strip()
	description = (zweck + (", " + kategorie if kategorie else "")).strip() or doc.name
	description = description[:140]

	config = {
		"name": (company_name or doc.company)[:70],
		"currency": doc.currency or "EUR",
		"IBAN": (company_bank.iban or "").replace(" ", "").upper(),
		"batch": False,
	}
	if company_bank.branch_code:
		config["BIC"] = (company_bank.branch_code or "").replace(" ", "").upper()

	payment = {
		"name": (partyname or doc.party)[:70],
		"IBAN": (party_bank.iban or "").replace(" ", "").upper(),
		"amount": amount_cents,
		"description": description,
		"execution_date": execution_date,
		"endtoend_id": doc.name[:35],
	}
	if party_bank.branch_code:
		payment["BIC"] = (party_bank.branch_code or "").replace(" ", "").upper()
	if doc.currency:
		payment["currency"] = doc.currency

	try:
		sepa = SepaTransfer(config, schema="pain.001.001.03", clean=False)
		sepa.add_payment(payment)
		# Validate the generated document against the pain.001.001.03 XSD.
		# ``clean=False`` above means input text is not sanitised,
		# so an out-of-spec name/IBAN/charset would otherwise silently produce a
		# malformed bank instruction. Fail closed instead: a schema violation
		# returns an error and blocks the export rather than handing out an
		# invalid SEPA file the bank would reject (or mis-process).
		try:
			xml_content = sepa.export(validate=True)
		except Exception as validation_error:
			return None, _(
				"Generated SEPA XML (pain.001) failed schema validation: {0}"
			).format(validation_error)

		if isinstance(xml_content, bytes):
			xml_content = xml_content.decode("utf-8")
		return xml_content, None
	except Exception as e:
		return None, str(e)


@frappe.whitelist()
def export_request(payment_request_name):
	# Permission gate: @frappe.whitelist() makes this callable by any logged-in
	# user regardless of DocType permissions, so producing an executable SEPA
	# payment file must require submit rights on the Payment Request (mirrors
	# kefiya.utils.client.submit_payment_request_via_fints). The approval-state
	# gate lives in _build_sepa_xml (docstatus == 1).
	frappe.has_permission(
		"Payment Request", ptype="submit",
		doc=payment_request_name, throw=True)
	try:
		settings = frappe.get_single("Kefiya Settings")

		export_action = (
			getattr(settings, "payment_request_export_action", None)
			or getattr(settings, "payment_request_csv_action", None)
			or "Download SEPA XML"
		)
		export_action = str(export_action).strip()

		xml_content, error = _build_sepa_xml(payment_request_name)
		if error:
			return {"status": "error", "message": error}
		if not xml_content:
			return {"status": "error", "message": _("Failed to generate SEPA XML.")}

		return {
			"status": "success",
			"export_action": export_action,
			"recipient_email": settings.recipient_email or "",
			"data": xml_content,
		}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def send_sepa_xml_via_email(payment_request_name):
	# Permission gate: this mails an executable SEPA payment file, so it must
	# require submit rights on the specific Payment Request rather than being
	# callable by any logged-in user holding the right on some other one.
	frappe.has_permission(
		"Payment Request", ptype="submit",
		doc=payment_request_name, throw=True)

	# Neither the attachment nor the recipient may come from the caller. Both
	# used to be parameters, so a user with submit rights could have arbitrary
	# content mailed to an arbitrary address through the server -- and the
	# approval and XSD gates in _build_sepa_xml did not apply to whatever the
	# client passed in. Build the file here and send it only to the address
	# configured in Kefiya Settings.
	settings = frappe.get_single("Kefiya Settings")
	recipient_email = (settings.recipient_email or "").strip()
	if not recipient_email:
		return {"status": "error", "message": _(
			"No recipient e-mail is configured in Kefiya Settings."
		)}

	xml_content, error = _build_sepa_xml(payment_request_name)
	if error:
		return {"status": "error", "message": error}
	if not xml_content:
		return {"status": "error", "message": _("Failed to generate SEPA XML.")}

	try:
		subject = _("Moneyplex SEPA File")
		message = _("Please find the attached SEPA XML payment file.")
		attachments = [{
			"fname": "payment_request_pain001.xml",
			"fcontent": xml_content,
		}]
		frappe.sendmail(
			recipients=[recipient_email],
			subject=subject,
			message=message,
			attachments=attachments,
			delayed=False,
			retry=3,
		)
		return {"status": "success", "message": _("Email sent successfully.")}
	except Exception as e:
		return {"status": "error", "message": str(e)}
