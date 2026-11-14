import frappe

# The roles the Kefiya pages are open to (see the "roles" of kefiya/kefiya/page/*/*.json)
KEFIYA_ROLES = {"System Manager", "Accounts Manager", "Accounts User"}


def check_app_permission():
    """Whether the Kefiya tile shows on the apps screen for the current user."""
    if frappe.session.user == "Administrator":
        return True
    return bool(KEFIYA_ROLES & set(frappe.get_roles()))
