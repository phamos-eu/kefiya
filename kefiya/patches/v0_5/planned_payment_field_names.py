# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Kefiya Planned Payment took the forecast's field names.

planned_date -> due_date, amount -> expected_amount, payment_kind ->
source_kind, and every row written before was reported by the bank. Frappe
leaves the old columns in the table, so the values are copied across where
the new field is still empty. Idempotent; a site without old columns has
nothing to do.
"""

import frappe

TABLE = "tabKefiya Planned Payment"


def execute():
    columns = set(frappe.db.get_table_columns("Kefiya Planned Payment"))
    if not {"planned_date", "amount", "payment_kind"} <= columns:
        return

    # "Standing Order" was what the dated-DEBIT fetch wrote: money the
    # account collects, filed under the wrong name.
    frappe.db.sql("""
        UPDATE `{0}`
        SET due_date = COALESCE(due_date, planned_date),
            expected_amount = COALESCE(expected_amount, amount),
            source_kind = COALESCE(source_kind,
                CASE payment_kind WHEN 'Standing Order' THEN 'Scheduled Debit'
                                  ELSE payment_kind END),
            from_bank = 1
        WHERE planned_date IS NOT NULL
    """.format(TABLE))
