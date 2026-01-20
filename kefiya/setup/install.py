import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

def after_migrate():
	create_custom_fields(get_custom_fields())

def before_uninstall():
	delete_custom_fields(get_custom_fields())

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
			"insert_after": "kefiya_section",
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

	return {
		"Payment Request": custom_fields_payment_request
	}
