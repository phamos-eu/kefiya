# -*- coding: utf-8 -*-
# Copyright (c) 2026, phamos GmbH and contributors
# For license information, please see license.txt

import logging
import threading
from unittest.mock import MagicMock, patch

import frappe
import requests
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


def bank_answers_400(client):
    """Mimic fints.connection: log the message, get HTTP 400 back, raise."""
    def get_sepa_accounts():
        logging.getLogger("fints.connection").debug("Sending HKSPA:1:3")
        response = MagicMock(ok=False, status_code=400, url="https://banking.example/fints", text="Ungültige Nachricht")
        for hook in client.connection.session.hooks["response"]:
            hook(response)
        raise FinTSConnectionError("Bad status code 400")
    return get_sepa_accounts


class TestBankTrafficLog(FrappeTestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.connection.session = requests.Session()

    def test_http_error_and_traffic_go_to_error_log(self):
        with patch("frappe.log_error") as log_error, self.assertRaises(FinTSConnectionError):
            with fints_controller.log_bank_traffic_on_error(self.client, "Kefiya: Could not load accounts"):
                bank_answers_400(self.client)()

        message = log_error.call_args.kwargs["message"]
        self.assertIn("HTTP 400 from https://banking.example/fints", message)
        self.assertIn("Ungültige Nachricht", message)
        self.assertIn("Sending HKSPA:1:3", message)
        self.assertEqual(self.client.connection.session.hooks["response"], [])

    def test_other_threads_are_not_recorded(self):
        def other_request():
            logging.getLogger("fints.connection").debug("Sending for another login")

        with patch("frappe.log_error") as log_error, self.assertRaises(FinTSConnectionError):
            with fints_controller.log_bank_traffic_on_error(self.client, "Kefiya: Could not load accounts"):
                thread = threading.Thread(target=other_request)
                thread.start()
                thread.join()
                bank_answers_400(self.client)()

        message = log_error.call_args.kwargs["message"]
        self.assertIn("Sending HKSPA:1:3", message)
        self.assertNotIn("another login", message)
        self.assertEqual(logging.getLogger("fints").level, logging.NOTSET)

    def test_tan_interaction_is_not_logged(self):
        with patch("frappe.log_error") as log_error, self.assertRaises(fints_controller.TanInteractionRequired):
            with fints_controller.log_bank_traffic_on_error(self.client, "Kefiya: Could not load accounts"):
                raise fints_controller.TanInteractionRequired()

        log_error.assert_not_called()


class TestAccountsBankRefusal(FrappeTestCase):
    def test_bank_reason_and_error_log_link_replace_the_400(self):
        # DKB ends the dialog with 9800/9040; fints goes on and gets HTTP 400
        controller = make_controller(fints_controller.FinTSController)
        controller.kefiya_login = frappe._dict(iban_list=None)
        controller.fints_connection.connection.session = requests.Session()
        controller.fints_connection.init_tan_response = None

        def get_sepa_accounts():
            for code, text in (("9800", "Dialog abgebrochen."), ("9040", "Bitte resynchronisiere deinen chipTAN-Generator.")):
                logging.getLogger("fints.client").error(
                    "Dialog response: %s - %s", code, text,
                    extra={"fints_response_code": code, "fints_response_text": text},
                )
            raise FinTSConnectionError("Bad status code 400")
        controller.fints_connection.get_sepa_accounts.side_effect = get_sepa_accounts

        with patch("frappe.log_error", return_value=frappe._dict(name="ERR-0001")), \
                self.assertRaises(frappe.ValidationError) as ctx:
            controller._FinTSController__fetch_fints_accounts()

        message = str(ctx.exception)
        self.assertIn("9040: Bitte resynchronisiere deinen chipTAN-Generator.", message)
        self.assertNotIn("Bad status code 400", message)
        self.assertIn("chipTAN generator needs to be resynchronised", message)
        self.assertIn('href="/app/error-log/ERR-0001"', message)


class TestTanChallenge(FrappeTestCase):
    def test_qr_image_and_escaped_text(self):
        response = MagicMock(
            spec=NeedTANResponse,
            challenge="Scan the code <now>",
            challenge_html=None,
            challenge_matrix=("image/png", b"\x89PNG"),
        )
        challenge = fints_controller.tan_challenge(response)
        self.assertEqual(challenge["text"], "Scan the code &lt;now&gt;")
        self.assertEqual(challenge["image"], "data:image/png;base64,iVBORw==")

    def test_non_image_matrix_is_dropped(self):
        for mime_type in ("text/html", 'image/png" onerror="alert(1)'):
            with self.subTest(mime_type=mime_type):
                response = MagicMock(
                    spec=NeedTANResponse,
                    challenge=None,
                    challenge_html=None,
                    challenge_matrix=(mime_type, b"<script>"),
                )
                self.assertIsNone(fints_controller.tan_challenge(response))

    def test_structured_text_comes_from_challenge_html(self):
        response = MagicMock(
            spec=NeedTANResponse,
            challenge="<b>Betrag</b>",
            challenge_html="<b>Betrag</b>",
            challenge_matrix=None,
        )
        self.assertEqual(fints_controller.tan_challenge(response), {"text": "<b>Betrag</b>"})

    def test_ask_for_tan_sends_the_challenge(self):
        for decoupled, request in ((False, "request_tan"), (True, "request_mfa_confirmation")):
            with self.subTest(decoupled=decoupled):
                controller = make_controller(fints_controller.FinTSController)
                controller._FinTSController__persist_fints_state = MagicMock()
                response = MagicMock(
                    spec=NeedTANResponse,
                    decoupled=decoupled,
                    challenge="Scan the code",
                    challenge_html=None,
                    challenge_matrix=None,
                )
                controller.ask_for_tan(response)
                kwargs = getattr(controller.interactive, request).call_args.kwargs
                self.assertEqual(kwargs["challenge"], {"text": "Scan the code"})


class TestResolveTanInteraction(FrappeTestCase):
    """The form offers the accounts once the TAN is resolved."""

    def resolve(self, controller):
        from kefiya.utils import client

        with patch.object(client, "_use_tan_authentication", return_value=True), \
                patch.object(fints_controller, "FinTSController", controller):
            return client.resolve_tan_interaction(
                "DKB phamos", {"mfa_confirmation": 1, "tan": "123456"}
            )

    def test_accepted_tan_returns_the_accounts(self):
        controller = MagicMock()
        controller.return_value.get_fints_accounts.return_value = [{"iban": "DE02120300000000202051"}]
        self.assertEqual(
            self.resolve(controller),
            {"tan_resolved": True, "accounts": [{"iban": "DE02120300000000202051"}]},
        )

    def test_further_interaction_is_reported(self):
        controller = MagicMock(side_effect=fints_controller.TanInteractionRequired())
        self.assertEqual(self.resolve(controller), {"tan_resolved": False})
