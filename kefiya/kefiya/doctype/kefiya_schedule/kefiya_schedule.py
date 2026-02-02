# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.model.document import Document
from dateutil.relativedelta import relativedelta
from frappe.utils import now_datetime
from frappe.utils.scheduler import is_scheduler_inactive
from frappe import _
from kefiya.utils.client import import_fints_transactions
from kefiya.utils.fints_controller import FinTSController
from kefiya.utils.fints_controller_legacy import FinTSController as FinTSControllerLegacy


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

    current_datetime = now_datetime()
    current_hour = current_datetime.strftime("%H")

    kefiya_settings = frappe.get_single('Kefiya Settings')
    overlap_days = kefiya_settings.overlap_days

    # Query child table
    for child_item in schedule_settings.schedule_items:
        # Get the last run / last imported transaction date
        if current_hour == child_item.hour or manual:
            try:
                if child_item.active and (child_item.import_frequency or manual):
                    lastruns = frappe.get_list(
                        'Kefiya Import',
                        filters={
                            'kefiya_login': child_item.kefiya_login,
                            'docstatus': 1,
                            'end_date': ('>', '1/1/1900')
                        },
                        fields=['name', 'end_date', 'modified'],
                        order_by='end_date desc, modified desc'
                    )[:1] or [None]
                    # Create new 'Kefiya Import' doc
                    kefiya_import = frappe.get_doc({
                        'doctype': 'Kefiya Import',
                        'kefiya_login': child_item.kefiya_login
                    })
                    if lastruns[0] and lastruns[0].end_date:
                        if child_item.import_frequency == 'Daily':
                            checkdate = (
                                now_datetime().date()
                            )
                        elif child_item.import_frequency == 'Weekly':
                            checkdate = (
                                now_datetime().date() - relativedelta(weeks=1)
                            )
                        elif child_item.import_frequency == 'Monthly':
                            checkdate = (
                                now_datetime().date() - relativedelta(months=1)
                            )
                        else:
                            raise ValueError('Unknown frequency')

                        new_from_date = lastruns[0].end_date - relativedelta(days=overlap_days)
                        if (
                            new_from_date <= now_datetime().date() and (
                                lastruns[0].end_date <= checkdate or manual
                            )
                        ):
                            if (now_datetime().date() - new_from_date).days > 90:
                                new_from_date = now_datetime().date() - relativedelta(days=90)

                            kefiya_import.from_date = new_from_date
                        else:
                            frappe.db.rollback()
                            continue
                    else:
                        kefiya_import.from_date = now_datetime().date() - relativedelta(days=overlap_days)
                   
                    kefiya_import.to_date = now_datetime().date()

                    kefiya_import.save()
                    if manual:
                        import_fints_transactions(
                            kefiya_import.name,
                            child_item.kefiya_login,
                            schedule_settings.name
                        )
                    else:
                        if frappe.db.get_single_value("Kefiya Settings", "enable_tan_authentication"):
                            FinTSController(child_item.kefiya_login) \
                                .import_fints_transactions(kefiya_import.name)
                        else:
                            FinTSControllerLegacy(child_item.kefiya_login) \
                                .import_fints_transactions(kefiya_import.name)
            except Exception:
                frappe.log_error(frappe.get_traceback())