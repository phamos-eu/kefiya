import unittest
from unittest.mock import patch

import frappe

from kefiya.utils.permissions import check_app_permission


class TestAppPermission(unittest.TestCase):
    """Who sees the Kefiya tile on the apps screen."""

    def setUp(self):
        self.user = frappe.session.user

    def tearDown(self):
        frappe.set_user(self.user)

    def test_administrator_sees_it(self):
        frappe.set_user("Administrator")
        self.assertTrue(check_app_permission())

    def test_accounts_roles_see_it(self):
        frappe.set_user("Guest")
        for role in ("System Manager", "Accounts Manager", "Accounts User"):
            with patch("frappe.get_roles", return_value=["Employee", role]):
                self.assertTrue(check_app_permission(), role)

    def test_other_users_do_not(self):
        frappe.set_user("Guest")
        with patch("frappe.get_roles", return_value=["Employee", "Sales User"]):
            self.assertFalse(check_app_permission())
