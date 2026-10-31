# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from kefiya.kefiya.page.kefiya_hub.kefiya_hub import get_imports, get_overview, record_failed_connection

COMPANY = "_Test GmbH"
OTHER_COMPANY = "My Company"
BANK = "_Test Hub Bank"
PREFIX = "_Test Hub"


def make_bank_account(name: str, iban: str) -> str:
    if not frappe.db.exists("Bank", BANK):
        frappe.get_doc({"doctype": "Bank", "bank_name": BANK}).insert()
    gl_account = frappe.get_all(
        "Account",
        filters={"company": COMPANY, "account_type": "Bank", "is_group": 0},
        pluck="name",
        limit=1,
    )[0]
    return frappe.get_doc({
        "doctype": "Bank Account",
        "account_name": f"{PREFIX} {name}",
        "bank": BANK,
        "company": COMPANY,
        "is_company_account": 1,
        "account": gl_account,
        "iban": iban,
    }).insert().name


def make_login(name: str, bank_account: str | None = None, company: str = COMPANY,
               failed_connection: int = 0) -> str:
    login = frappe.get_doc({
        "doctype": "Kefiya Login",
        "login_name": f"{PREFIX} {name}",
        "fints_url": "https://fints.example.com/fints",
        "blz": "99999999",
        "fints_login": "test",
        "fints_password": "test",
        "company": company,
        "bank_account": bank_account,
        "failed_connection": failed_connection,
    })
    login.insert(ignore_mandatory=True)
    return login.name


def make_import(login: str, days_ago: int, submit: bool = True) -> str:
    doc = frappe.get_doc({
        "doctype": "Kefiya Import",
        "kefiya_login": login,
        "from_date": add_days(nowdate(), -days_ago - 1),
        "to_date": add_days(nowdate(), -days_ago),
    }).insert()
    if submit:
        doc.submit()
    return doc.name


def make_open_transaction(bank_account: str, amount: float) -> None:
    frappe.get_doc({
        "doctype": "Bank Transaction",
        "date": nowdate(),
        "bank_account": bank_account,
        "company": COMPANY,
        "currency": "EUR",
        "deposit": amount,
        "description": f"{PREFIX} payment",
    }).insert().submit()


def login_by_name(overview: dict, name: str) -> dict:
    return next(d for d in overview["logins"] if d.name == f"{PREFIX} {name}")


@patch("kefiya.utils.bank_logo._get", lambda *args, **kwargs: None)
class TestKefiyaHubOverview(FrappeTestCase):
    """get_overview drives the step statuses and counts of the Kefiya page."""

    def setUp(self):
        frappe.db.delete("Bank Transaction", {"description": f"{PREFIX} payment"})
        for login in frappe.get_all("Kefiya Login", {"name": ["like", f"{PREFIX}%"]}, pluck="name"):
            frappe.db.delete("Kefiya Import", {"kefiya_login": login})
            frappe.delete_doc("Kefiya Login", login, force=True)
        frappe.db.delete("Bank Account", {"account_name": ["like", f"{PREFIX}%"]})
        if frappe.get_all("Kefiya Login", {"company": COMPANY}):
            self.skipTest(f"{COMPANY} has Kefiya Logins of its own on this site")

    def tearDown(self):
        frappe.db.rollback()

    def test_no_login_means_nothing_set_up(self):
        steps = get_overview(COMPANY)["steps"]
        self.assertEqual(steps["connect"], "todo")
        self.assertEqual(steps["link"], "todo")
        self.assertEqual(steps["import"], "todo")

    def test_failing_login_is_an_error(self):
        account = make_bank_account("Giro", "DE89999999990001111111")
        make_login("Failing", account, failed_connection=2)

        overview = get_overview(COMPANY)
        self.assertEqual(overview["steps"]["connect"], "error")
        self.assertEqual(login_by_name(overview, "Failing").status, "error")

    def test_login_without_bank_account_needs_linking(self):
        make_login("Unlinked")

        overview = get_overview(COMPANY)
        self.assertEqual(overview["steps"]["link"], "warning")
        self.assertEqual(login_by_name(overview, "Unlinked").status, "warning")

    def test_stale_import_needs_attention(self):
        account = make_bank_account("Giro", "DE89999999990001111111")
        login = make_login("Stale", account)
        make_import(login, days_ago=10)

        overview = get_overview(COMPANY)
        self.assertEqual(overview["steps"]["import"], "warning")
        self.assertTrue(login_by_name(overview, "Stale").import_stale)

    def test_recent_import_is_up_to_date(self):
        account = make_bank_account("Giro", "DE89999999990001111111")
        login = make_login("Fresh", account)
        make_import(login, days_ago=0)

        overview = get_overview(COMPANY)
        self.assertEqual(overview["steps"]["import"], "done")
        self.assertEqual(login_by_name(overview, "Fresh").status, "ok")
        self.assertEqual(overview["recent_imports"][0].kefiya_login, login)

    def test_open_transactions_are_counted_per_bank_account(self):
        account = make_bank_account("Giro", "DE89999999990001111111")
        make_login("Fresh", account)
        make_open_transaction(account, 100)
        make_open_transaction(account, 50)

        overview = get_overview(COMPANY)
        self.assertEqual(overview["steps"]["reconcile"], "warning")
        summary = next(a for a in overview["bank_accounts"] if a.name == account)
        self.assertTrue(summary.has_login)
        self.assertEqual(summary.open["count"], 2)
        self.assertEqual(summary.open["amount"], 150)

    def test_accounts_user_gets_the_overview(self):
        """Accounts Users may not read statement imports; the page must still load."""
        user = "_test_kefiya_hub_accounts_user@example.com"
        if not frappe.db.exists("User", user):
            frappe.get_doc({
                "doctype": "User",
                "email": user,
                "first_name": "Kefiya Hub",
                "send_welcome_email": 0,
                "roles": [{"role": "Accounts User"}],
            }).insert()
        make_login("Fresh")

        frappe.set_user(user)
        try:
            overview = get_overview(COMPANY)
        finally:
            frappe.set_user("Administrator")
        self.assertIn(f"{PREFIX} Fresh", [d.name for d in overview["logins"]])

    def test_users_without_accounts_roles_get_nothing(self):
        frappe.set_user("Guest")
        try:
            self.assertRaises(frappe.PermissionError, get_overview, COMPANY)
        finally:
            frappe.set_user("Administrator")

    def test_company_filter(self):
        make_login("Other company", company=OTHER_COMPANY)
        make_login("This company")

        names = [d.name for d in get_overview(COMPANY)["logins"]]
        self.assertIn(f"{PREFIX} This company", names)
        self.assertNotIn(f"{PREFIX} Other company", names)


@patch("kefiya.utils.bank_logo._get", lambda *args, **kwargs: None)
class TestKefiyaHubConnect(FrappeTestCase):
    """Data and actions behind the login cards of the Connect view."""

    def setUp(self):
        for login in frappe.get_all("Kefiya Login", {"name": ["like", f"{PREFIX}%"]}, pluck="name"):
            frappe.delete_doc("Kefiya Login", login, force=True)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback()

    def test_card_says_whether_a_connection_is_stored_but_not_the_state(self):
        with_state = make_login("With state")
        make_login("Without state")
        frappe.db.set_value("Kefiya Login", with_state, "stored_client_state", "secret-state")

        overview = get_overview(COMPANY)
        self.assertTrue(login_by_name(overview, "With state").has_client_state)
        self.assertFalse(login_by_name(overview, "Without state").has_client_state)
        self.assertNotIn("secret-state", frappe.as_json(overview))

    def test_failed_connection_is_counted_and_the_account_cleared(self):
        login = make_login("Failing", failed_connection=1)
        frappe.db.set_value("Kefiya Login", login, "account_iban", "DE89999999990001111111")

        self.assertEqual(record_failed_connection(login), 2)
        failed, iban = frappe.db.get_value("Kefiya Login", login, ["failed_connection", "account_iban"])
        self.assertEqual(failed, 2)
        self.assertFalse(iban)

    def test_failed_connection_needs_write_permission(self):
        login = make_login("Failing")
        frappe.set_user("Guest")
        self.assertRaises(frappe.PermissionError, record_failed_connection, login)


class TestGetAccountsErrors(FrappeTestCase):
    """client.get_accounts hides only the TAN request, not real errors."""

    def controller(self, error):
        class Controller:
            def __init__(self, *args):
                pass

            def get_fints_accounts(self):
                raise error

        return Controller

    def test_tan_request_ends_the_call_without_accounts(self):
        from kefiya.utils import client
        from kefiya.utils.fints_controller import TanInteractionRequired

        with patch.object(client, "_use_tan_authentication", return_value=True), \
                patch.object(client, "_get_fints_controller", return_value=self.controller(TanInteractionRequired())):
            self.assertIsNone(client.get_accounts("any", "any"))

    def test_connection_error_is_raised_in_tan_mode(self):
        from kefiya.utils import client

        with patch.object(client, "_use_tan_authentication", return_value=True), \
                patch.object(client, "_get_fints_controller",
                             return_value=self.controller(frappe.ValidationError("no connection"))):
            self.assertRaises(frappe.ValidationError, client.get_accounts, "any", "any")


@patch("kefiya.utils.bank_logo._get", lambda *args, **kwargs: None)
class TestKefiyaHubImports(FrappeTestCase):
    """get_imports feeds the Imports view."""

    def setUp(self):
        for login in frappe.get_all("Kefiya Login", {"name": ["like", f"{PREFIX}%"]}, pluck="name"):
            frappe.db.delete("Kefiya Import", {"kefiya_login": login})
            frappe.delete_doc("Kefiya Login", login, force=True)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback()

    def test_drafts_and_submitted_imports_with_their_json(self):
        login = make_login("Imports")
        draft = make_import(login, days_ago=3, submit=False)
        done = make_import(login, days_ago=1)
        frappe.get_doc({
            "doctype": "File",
            "file_name": f"{done}.json",
            "attached_to_doctype": "Kefiya Import",
            "attached_to_name": done,
            "content": "[]",
            "is_private": 1,
        }).insert()

        rows = {d.name: d for d in get_imports(COMPANY) if d.source == "FinTS"}
        self.assertEqual(rows[draft].docstatus, 0)
        self.assertIsNone(rows[draft].file_url)
        self.assertEqual(rows[done].docstatus, 1)
        self.assertTrue(rows[done].file_url.endswith(".json"))

    def test_limit_and_company(self):
        login = make_login("Imports")
        other = make_login("Other company", company=OTHER_COMPANY)
        for days in (1, 2, 3):
            make_import(login, days_ago=days)
        make_import(other, days_ago=1)

        rows = get_imports(COMPANY, limit=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(d.kefiya_login == login for d in rows if d.source == "FinTS"))
