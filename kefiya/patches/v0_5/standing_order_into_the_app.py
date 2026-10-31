# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Kefiya Standing Order is the app's now, where a site created it itself.

A site may have built the DocType by hand before the app shipped it. Marked
as not custom, the model sync takes it over and keeps its table and rows; the
fields the app adds are created, the ones it shares are updated. Runs before
the model sync. A site without the DocType has nothing to do.
"""

import frappe


def execute():
    if frappe.db.exists("DocType", "Kefiya Standing Order"):
        frappe.db.set_value("DocType", "Kefiya Standing Order",
                            {"custom": 0, "module": "Kefiya"},
                            update_modified=False)
