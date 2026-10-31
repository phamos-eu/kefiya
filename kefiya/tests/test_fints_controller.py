# -*- coding: utf-8 -*-
# Copyright (c) 2026, phamos GmbH and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock, patch

import frappe
from fints.client import NeedTANResponse
from fints.exceptions import FinTSConnectionError, FinTSSCARequiredError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from kefiya.utils import fints_controller, fints_controller_legacy
from kefiya.utils.fints_controller import FetchTanRequired
from kefiya.utils.fints_controller_legacy import TanAuthenticationRequired

CONTROLLERS = (fints_controller.FinTSController, fints_controller_legacy.FinTSController)


def make_controller(cls):
    """Build a controller without its __init__, which logs in to the bank."""
    controller = cls.__new__(cls)
    controller.kefiya_login = frappe._dict(
        name="DKB Test", account_iban="DE02120300000000202051"
    )
    controller.fints_connection = MagicMock()
    controller.get_fints_account_by_iban = MagicMock()
    controller.interactive = MagicMock()
    return controller


class TestFetchTanRequired(FrappeTestCase):
    def test_tan_on_fetch_raises_clear_error(self):
        start = add_days(getdate(), -120)
        for cls in CONTROLLERS:
            with self.subTest(controller=cls.__module__):
                controller = make_controller(cls)
                controller.fints_connection.get_transactions.return_value = MagicMock(
                    spec=NeedTANResponse
                )
                with self.assertRaises(FetchTanRequired):
                    controller.get_fints_transactions(start, add_days(getdate(), -1))

    def test_sca_required_error_on_fetch_raises_clear_error(self):
        start = add_days(getdate(), -120)
        for cls in CONTROLLERS:
            with self.subTest(controller=cls.__module__):
                controller = make_controller(cls)
                controller.fints_connection.get_transactions.side_effect = (
                    FinTSSCARequiredError("SCA required")
                )
                with self.assertRaises(FetchTanRequired):
                    controller.get_fints_transactions(start, add_days(getdate(), -1))

    def test_import_passes_tan_error_through_unwrapped(self):
        for cls in CONTROLLERS:
            with self.subTest(controller=cls.__module__):
                controller = make_controller(cls)
                controller.get_fints_transactions = MagicMock(
                    side_effect=FetchTanRequired("TAN needed")
                )
                with patch("frappe.get_doc"), self.assertRaises(FetchTanRequired):
                    controller.import_fints_transactions("KEFIYA-IMPORT-TEST")


# What fints raises when DKB rejects HKSPA, shortened
DKB_REJECTION = (
    "Error response: fints.message.FinTSInstituteMessage([...responses=["
    "fints.formals.Response(code='9800', reference_element=None, text='Dialog abgebrochen.'), "
    "fints.formals.Response(code='9210', reference_element=None, text='Auftrag abgelehnt')]"
)


class TestLegacyTanAuthenticationRequired(FrappeTestCase):
    def setUp(self):
        # log_error commits, so it would keep test entries in the Error Log
        log_error = patch("frappe.log_error")
        log_error.start()
        self.addCleanup(log_error.stop)

    def get_accounts(self, side_effect):
        controller = make_controller(fints_controller_legacy.FinTSController)
        controller.fints_connection.get_sepa_accounts.side_effect = side_effect
        controller._FinTSController__get_fints_accounts()

    def test_rejected_account_list_offers_tan_and_retry(self):
        with self.assertRaises(TanAuthenticationRequired):
            self.get_accounts(Exception(DKB_REJECTION))
        message = frappe.message_log[-1]
        self.assertEqual(
            message["primary_action"]["client_action"],
            "kefiya.interactive.use_tan_and_try_again",
        )
        self.assertEqual(message["primary_action"]["args"], "DKB Test")
        self.assertIn("DKB Test", message["message"])
        self.assertIn("9800 Dialog abgebrochen., 9210 Auftrag abgelehnt", message["message"])

    def test_no_button_without_write_access(self):
        with patch("frappe.has_permission", return_value=False), self.assertRaises(
            TanAuthenticationRequired
        ):
            self.get_accounts(Exception(DKB_REJECTION))
        message = frappe.message_log[-1]
        self.assertNotIn("primary_action", message)
        self.assertIn("Ask someone who can edit this Kefiya Login", message["message"])

    def test_http_400_on_account_list_asks_for_tan(self):
        with self.assertRaises(TanAuthenticationRequired):
            self.get_accounts(FinTSConnectionError("Bad status code 400"))
        self.assertIn("Bank answer: HTTP 400", frappe.message_log[-1]["message"])

    def test_failed_account_list_skips_the_dialog_end(self):
        # fints would send HKEND on exit; it fails too and hides the message
        for error in (FinTSConnectionError("Bad status code 400"), Exception("code='9942'")):
            with self.subTest(error=str(error)):
                controller = make_controller(fints_controller_legacy.FinTSController)
                controller.fints_connection.get_sepa_accounts.side_effect = error
                with self.assertRaises(frappe.ValidationError):
                    controller._FinTSController__get_fints_accounts()
                self.assertFalse(controller.fints_connection._standing_dialog.open)

    def test_sca_required_error_asks_for_tan(self):
        with self.assertRaises(TanAuthenticationRequired):
            self.get_accounts(FinTSSCARequiredError("SCA required"))

    def test_tan_at_dialog_start_asks_for_tan(self):
        controller = make_controller(fints_controller_legacy.FinTSController)
        controller.fints_connection.init_tan_response = MagicMock(spec=NeedTANResponse)
        with self.assertRaises(TanAuthenticationRequired):
            controller._FinTSController__init_tan_processing()

    def test_other_errors_keep_the_bank_message(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            self.get_accounts(Exception("code='9942' PIN falsch"))
        self.assertNotIsInstance(ctx.exception, TanAuthenticationRequired)


class TestTanAuthenticationPerLogin(FrappeTestCase):
    def test_controller_follows_the_login(self):
        from kefiya.utils import client

        for enabled, module in ((1, fints_controller), (0, fints_controller_legacy)):
            with self.subTest(enabled=enabled), patch(
                "frappe.db.get_value", return_value=enabled
            ) as get_value:
                self.assertIs(client._get_fints_controller("DKB Test"), module.FinTSController)
                get_value.assert_called_with(
                    "Kefiya Login", "DKB Test", "enable_tan_authentication"
                )

    def test_new_login_uses_tan_by_default(self):
        login = frappe.new_doc("Kefiya Login")
        self.assertEqual(login.enable_tan_authentication, 1)


class TestMoveTanAuthenticationPatch(FrappeTestCase):
    def run_patch(self, site_value):
        from kefiya.patches.v0_5_0 import move_tan_authentication_to_kefiya_login as move

        frappe.db.delete(
            "Singles", {"doctype": "Kefiya Settings", "field": "enable_tan_authentication"}
        )
        frappe.db.sql(
            "insert into `tabSingles` (doctype, field, value) values (%s, %s, %s)",
            ("Kefiya Settings", "enable_tan_authentication", site_value),
        )
        # reload_doc can commit; the schema is already synced in tests
        with patch("frappe.reload_doc"):
            move.execute()

    def test_second_run_keeps_the_logins(self):
        if not frappe.db.count("Kefiya Login"):
            self.skipTest("no Kefiya Login on this site")
        from kefiya.patches.v0_5_0 import move_tan_authentication_to_kefiya_login as move

        self.run_patch("1")
        with patch("frappe.reload_doc"):
            move.execute()
        values = set(frappe.get_all("Kefiya Login", pluck="enable_tan_authentication"))
        self.assertEqual(values, {1})

    def test_logins_get_the_site_setting(self):
        if not frappe.db.count("Kefiya Login"):
            self.skipTest("no Kefiya Login on this site")
        for site_value, expected in (("1", 1), ("0", 0)):
            with self.subTest(site_value=site_value):
                self.run_patch(site_value)
                values = set(frappe.get_all("Kefiya Login", pluck="enable_tan_authentication"))
                self.assertEqual(values, {expected})
                self.assertFalse(
                    frappe.db.exists(
                        "Singles",
                        {"doctype": "Kefiya Settings", "field": "enable_tan_authentication"},
                    )
                )
