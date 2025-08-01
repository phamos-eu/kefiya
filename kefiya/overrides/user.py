from frappe.auth import MAX_PASSWORD_SIZE
from frappe.core.doctype.user.user import _get_user_for_update_password, handle_password_test_fail, reset_user_data, test_password_strength
from frappe.utils.password import update_password as _update_password
from frappe.utils import today
import frappe
from frappe import _
from frappe.utils import cint


@frappe.whitelist(allow_guest=True, methods=["POST"])
def update_password(
	new_password: str, logout_all_sessions: int = 0, key: str | None = None, old_password: str | None = None
):
    """Update password for the current user.

    Args:
            new_password (str): New password.
            logout_all_sessions (int, optional): If set to 1, all other sessions will be logged out. Defaults to 0.
            key (str, optional): Password reset key. Defaults to None.
            old_password (str, optional): Old password. Defaults to None.
    """

    if len(new_password) > MAX_PASSWORD_SIZE:
        frappe.throw(_("Password size exceeded the maximum allowed size."))

    result = test_password_strength(new_password)
    feedback = result.get("feedback", None)

    if feedback and not feedback.get("password_policy_validation_passed", False):
        handle_password_test_fail(feedback)

    res = _get_user_for_update_password(key, old_password)
    if res.get("message"):
        frappe.local.response.http_status_code = 410
        return res["message"]
    else:
        user = res["user"]

    logout_all_sessions = cint(logout_all_sessions) or frappe.db.get_single_value(
        "System Settings", "logout_on_password_reset"
    )
    _update_password(user, new_password, logout_all_sessions=cint(logout_all_sessions))

    user_doc, redirect_url = reset_user_data(user)

    user_doc.validate_reset_password()

    # frappe.local.login_manager.login_as(user)

    frappe.db.set_value("User", user, "last_password_reset_date", today())
    frappe.db.set_value("User", user, "reset_password_key", "")

    # Return success message instead of redirect
    frappe.msgprint(_("Your password has been updated. Please log in manually."))
    return "/login"