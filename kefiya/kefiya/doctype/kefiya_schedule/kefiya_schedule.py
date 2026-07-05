# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, getdate
from frappe.utils.scheduler import is_scheduler_inactive
from frappe import _
from kefiya.utils.client import import_fints_transactions
from kefiya.utils.fints_controller import FinTSController
from kefiya.utils.fints_controller_legacy import FinTSController as FinTSControllerLegacy
from kefiya.utils.import_bank_transaction import resolve_incremental_from_date


class KefiyaSchedule(Document):
    def validate(self):  # pylint: disable=no-self-use
        if is_scheduler_inactive():
            frappe.throw(
                _("Scheduler is inactive. Cannot import data."),
                title=_("Scheduler Inactive")
            )


@frappe.whitelist()
def scheduled_import_fints_payments(manual=None):
    """Create payment entries by Kefiya Schedule.

    :param manual: Call manualy
    :type manual: bool
    :return: None
    """
    schedule_settings = frappe.get_single('Kefiya Schedule')

    today = now_datetime().date()
    current_hour = now_datetime().strftime("%H")

    # Minimum number of days between two scheduled runs of the same login.
    FREQUENCY_GAP_DAYS = {'Daily': 1, 'Weekly': 7, 'Monthly': 30}

    # Query child table
    for child_item in schedule_settings.schedule_items:
        if not (current_hour == child_item.hour or manual):
            continue
        try:
            if not (child_item.active and (child_item.import_frequency or manual)):
                continue

            login_name = child_item.kefiya_login
            bank_account, allowed_days = (frappe.db.get_value(
                "Kefiya Login", login_name,
                ["bank_account", "allowed_sync_days_in_past"]
            ) or (None, None))

            # Frequency gate: use the last IMPORT RUN time (creation), not the
            # transaction end_date. A run that returned no transactions (weekend,
            # or an expired SCA) still counts as a run, so we neither hammer the
            # bank every 20 minutes nor get stuck refetching only "today" forever.
            if not manual:
                last = frappe.get_all(
                    "Kefiya Import",
                    filters={"kefiya_login": login_name, "docstatus": 1},
                    fields=["creation"],
                    order_by="creation desc",
                    limit=1,
                )
                if last:
                    gap = FREQUENCY_GAP_DAYS.get(child_item.import_frequency, 1)
                    if (today - getdate(last[0].creation)).days < gap:
                        continue

            # Default fetch window: from the date of the account's last booked
            # transaction up to today ("immer vom Datum der letzten Umsaetze bis
            # heute"), clamped to the login's allowed look-back window. Overlap on
            # the last day is harmless -- duplicates are dropped by their hash.
            kefiya_import = frappe.get_doc({
                'doctype': 'Kefiya Import',
                'kefiya_login': login_name,
                'from_date': resolve_incremental_from_date(bank_account, allowed_days),
                'to_date': today,
            })
            kefiya_import.save()

            if manual:
                import_fints_transactions(
                    kefiya_import.name,
                    login_name,
                    schedule_settings.name
                )
            elif frappe.db.get_single_value("Kefiya Settings", "enable_tan_authentication"):
                FinTSController(login_name) \
                    .import_fints_transactions(kefiya_import.name)
            else:
                FinTSControllerLegacy(login_name) \
                    .import_fints_transactions(kefiya_import.name)
        except Exception:
            frappe.log_error(frappe.get_traceback())