import frappe


def check_app_permission():
    """Whether the Kefiya tile shows on the apps screen for the current user.

    The tile opens Kefiya Hub, so it shows to whoever may open that page: its roles,
    including what the site changed in "Role Permission for Page and Report".
    """
    if frappe.session.user == "Administrator":
        return True
    return frappe.get_doc("Page", "kefiya-hub").is_permitted()
