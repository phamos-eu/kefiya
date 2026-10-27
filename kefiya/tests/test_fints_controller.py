# -*- coding: utf-8 -*-
# Copyright (c) 2026, phamos GmbH and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock, patch

import frappe
from fints.client import NeedTANResponse
from fints.exceptions import FinTSSCARequiredError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from kefiya.utils import fints_controller, fints_controller_legacy
from kefiya.utils.fints_controller import FetchTanRequired

CONTROLLERS = (fints_controller.FinTSController, fints_controller_legacy.FinTSController)


def make_controller(cls):
    """Build a controller without its __init__, which logs in to the bank."""
    controller = cls.__new__(cls)
    controller.kefiya_login = frappe._dict(account_iban="DE02120300000000202051")
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
