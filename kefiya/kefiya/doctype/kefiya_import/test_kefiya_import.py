# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and Contributors
# See license.txt

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate


class TestKefiyaImport(FrappeTestCase):
    def make_import(self, from_offset=None, to_offset=None):
        """Build an unsaved Kefiya Import with dates relative to today."""
        doc = frappe.new_doc("Kefiya Import")
        if from_offset is not None:
            doc.from_date = add_days(nowdate(), from_offset)
        if to_offset is not None:
            doc.to_date = add_days(nowdate(), to_offset)
        return doc

    def setUp(self):
        frappe.local.message_log = []

    def test_from_date_older_than_90_days_is_allowed(self):
        self.make_import(-120, -1).validate()

    def test_from_date_older_than_90_days_shows_hint(self):
        self.make_import(-120, -1).validate()
        messages = [
            frappe.parse_json(m).get("message") for m in frappe.local.message_log
        ]
        self.assertTrue(any("90 days" in (m or "") for m in messages))

    def test_recent_from_date_shows_no_hint(self):
        self.make_import(-30, -1).validate()
        self.assertEqual(frappe.local.message_log, [])

    def test_future_dates_are_rejected(self):
        self.assertRaises(frappe.ValidationError, self.make_import(5).validate)
        self.assertRaises(
            frappe.ValidationError, self.make_import(None, 5).validate
        )

    def test_from_date_after_to_date_is_rejected(self):
        self.assertRaises(
            frappe.ValidationError, self.make_import(-10, -20).validate
        )
