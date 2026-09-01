import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

def after_migrate():
	create_custom_fields(get_custom_fields())

#: Fields this app declares but must not take away again. Deleting a Custom
#: Field drops its column, and these carry values the site keeps using after
#: the app is gone -- balances, credit lines, the balance after each booking,
#: and a flag somebody set by hand. Uninstalling an app is not a reason to
#: destroy figures that were fetched from a bank.
KEEP_ON_UNINSTALL = {
	("Bank Account", "custom_account_balance"),
	("Bank Account", "custom_credit_line"),
	("Bank Account", "custom_account_balance_section"),
	("Bank Transaction", "bank_balance"),
	("Bank Transaction", "kefiya_followup"),
}


def before_uninstall():
	fields = {}
	for doctype, rows in get_custom_fields().items():
		keep = [r for r in rows
			if (doctype, r.get("fieldname")) not in KEEP_ON_UNINSTALL]
		if keep:
			fields[doctype] = keep
	delete_custom_fields(fields)

def delete_custom_fields(custom_fields):
	for doctype, fields in custom_fields.items():
		for field in fields:
			custom_field_name = frappe.db.get_value(
				"Custom Field", dict(dt=doctype, fieldname=field.get("fieldname"))
			)
			if custom_field_name:
				frappe.delete_doc("Custom Field", custom_field_name)

		frappe.clear_cache(doctype=doctype)

def get_custom_fields():
	custom_fields_payment_request = [
		{
			"label": "Kefiya Section",
			"fieldname": "kefiya_section",
			"fieldtype": "Section Break",
		},
		{
			"fieldname": "kefiya_column_break",
			"fieldtype": "Column Break",
			"insert_after": "company",
		},
		{
			"label": "Company Bank Account",
			"fieldname": "company_bank_account",
			"fieldtype": "Link",
            "options": "Bank Account",
			"insert_after": "kefiya_section",
		},
		{
			"fieldname": "kefiya_last_section",
			"fieldtype": "Section Break",
			"insert_after": "company_bank_account"
		}
	]

	# Bank Transaction is an ERPNext doctype, so anything this app needs on it
	# is a Custom Field. bank_balance existed on the production instance and
	# nowhere else, which meant a fresh install came up without it and the code
	# that fills it quietly did nothing. It is written out here exactly as it
	# stands there -- label, position, options and all -- so that installing
	# this app describes that field without changing it on the way past.
	custom_fields_bank_transaction = [
		{
			"label": "Banksaldo (laut Bank)",
			"fieldname": "bank_balance",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": "company",
			"read_only": 1,
			# db_set writes this on submitted documents; nobody types it in.
			"allow_on_submit": 0,
			"description": (
				"Der Saldo, wie er nach dieser Buchung stand. Zurueckgerechnet "
				"aus dem Saldo, den die Bank meldet, und nur innerhalb des "
				"abgerufenen Zeitraums gefuellt -- ausserhalb davon bleibt das "
				"Feld leer, statt eine Zahl zu zeigen, die stimmen koennte."
			),
		},
		{
			"label": "Wiedervorlage",
			"fieldname": "kefiya_followup",
			"fieldtype": "Check",
			"default": "0",
			"insert_after": "bank_balance",
			"allow_on_submit": 1,
			"in_standard_filter": 1,
			"description": (
				"Von Hand gesetztes Kennzeichen: hier will jemand noch einmal "
				"hinsehen. Traegt keine Buchungslogik."
			),
		},
	]

	# The balance and the credit line are written by this app -- store_balance()
	# fills them after every fetch -- and were nevertheless declared nowhere but
	# on one instance. Written out here exactly as they stand there, anchored on
	# the standard field `company`, so installing this app describes them
	# without moving anything on the way past.
	custom_fields_bank_account = [
		{
			"label": "Account Balance",
			"fieldname": "custom_account_balance_section",
			"fieldtype": "Section Break",
			"insert_after": "company",
		},
		{
			"label": "Account Balance",
			"fieldname": "custom_account_balance",
			"fieldtype": "Currency",
			"insert_after": "custom_account_balance_section",
			"in_list_view": 1,
			"description": (
				"Der Saldo, den die Bank zuletzt gemeldet hat. Wird beim Abruf "
				"gesetzt -- bei Avalen bleibt er leer, weil die Zahl dort eine "
				"Linie ist und kein Guthaben."
			),
		},
		{
			"label": "Kreditlinie",
			"fieldname": "custom_credit_line",
			"fieldtype": "Currency",
			"insert_after": "custom_account_balance",
			"description": (
				"Eingeräumte Kreditlinie / Dispo für den Liquiditäts-Forecast. "
				"Wird aus der Bankmeldung (HISAL) gefüllt, wo die Bank eine "
				"nennt."
			),
		},
		# What the bank allows on this account, read from HIUPD at every fetch.
		# Written by kefiya.utils.account_capabilities; nobody types it in,
		# which is why the whole section is read-only.
		{
			"label": "Was die Bank hier erlaubt",
			"fieldname": "custom_fints_capabilities_section",
			"fieldtype": "Section Break",
			"insert_after": "custom_credit_line",
			"collapsible": 1,
		},
		{
			"label": "Geschäftsvorfälle",
			"fieldname": "custom_fints_capabilities",
			"fieldtype": "Table",
			"options": "Kefiya Account Capability",
			"insert_after": "custom_fints_capabilities_section",
			"read_only": 1,
			"description": (
				"Die Geschäftsvorfälle, die die Bank für dieses Konto beim "
				"Anmelden nennt (HIUPD). Wird bei jedem Abruf neu geschrieben. "
				"Leer heißt: noch nicht abgerufen — nicht: nicht erlaubt."
			),
		},
		{
			"label": "Fähigkeiten geprüft am",
			"fieldname": "custom_capabilities_checked_on",
			"fieldtype": "Datetime",
			"insert_after": "custom_fints_capabilities",
			"read_only": 1,
			"description": (
				"Wann die Liste zuletzt von der Bank gelesen wurde. Ohne "
				"Datum wurde nie gefragt, und dann wird nichts ausgeblendet."
			),
		},
	]

	return {
		"Payment Request": custom_fields_payment_request,
		"Bank Transaction": custom_fields_bank_transaction,
		"Bank Account": custom_fields_bank_account,
	}
