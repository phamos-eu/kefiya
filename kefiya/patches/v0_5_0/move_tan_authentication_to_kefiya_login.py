import frappe


def execute():
    """Copy the site-wide TAN switch from Kefiya Settings to every Kefiya Login.

    New logins default to TAN authentication on. Existing logins keep what the
    site used so far, so a working connection doesn't change on update.
    """
    frappe.reload_doc("kefiya", "doctype", "kefiya_login")

    # Read tabSingles directly: the field is gone from Kefiya Settings' meta
    enabled = frappe.db.get_value(
        "Singles",
        {"doctype": "Kefiya Settings", "field": "enable_tan_authentication"},
        "value",
        order_by=None,
    )
    if enabled is None:
        # Fresh install or already moved: keep the logins as they are
        return

    frappe.db.sql(
        "update `tabKefiya Login` set enable_tan_authentication = %s",
        (1 if frappe.utils.cint(enabled) else 0,),
    )

    frappe.db.delete(
        "Singles",
        {"doctype": "Kefiya Settings", "field": "enable_tan_authentication"},
    )
