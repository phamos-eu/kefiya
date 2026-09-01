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


def _log_import_failure(login_name):
    """Log a per-login import failure without ever raising.

    `frappe.log_error(text)` passes its single positional argument as *title*,
    which is the Error Log's `method` field -- a Data column capped at 140
    characters. Handing it a full traceback therefore raised
    CharacterLengthExceededError from inside the except block below, so one
    broken login aborted the whole scheduler tick instead of just its own
    iteration. Title and message must stay separate, and this helper must
    never raise: error logging is not allowed to end the batch.
    """
    try:
        title = "Kefiya Schedule: import failed for {0}".format(login_name)
        frappe.log_error(
            title=title[:140],
            message=frappe.get_traceback(),
            reference_doctype="Kefiya Login",
            reference_name=login_name,
        )
    except Exception:
        try:
            frappe.logger("kefiya").exception(
                "Kefiya Schedule: import failed for %s", login_name
            )
        except Exception:
            pass


def _record_fetch_attempt(login_name):
    """Stamp the login with "we tried", so the frequency gate can see it.

    Without this a login that can never succeed is retried on every single
    tick. The gate below reads the last successful Kefiya Import, and a failed
    run leaves none: the rollback in _recover_from_failed_login() discards even
    the draft. Six loan accounts and a handful of misconfigured IBANs therefore
    hit their banks every twenty minutes and wrote three Error Log entries an
    hour each, instead of one a day.

    Written through the document lifecycle, and guarded like the error logging
    around it -- a failure to record the attempt must never end the batch.
    """
    try:
        login = frappe.get_doc("Kefiya Login", login_name)
        login.last_fetch_attempt = now_datetime()
        login.save(ignore_permissions=True)
    except Exception:
        try:
            frappe.logger("kefiya").exception(
                "Kefiya Schedule: could not record attempt for %s", login_name
            )
        except Exception:
            pass


def _recover_from_failed_login(login_name):
    """Discard a failed login's partial work and record why, never raising.

    Rolls back the Kefiya Import created before the fetch, which would
    otherwise survive as an empty draft: while an aborting tick still
    propagated, the scheduler rolled the whole run back and no such leftovers
    appeared -- now that the loop continues, this iteration has to clean up
    after itself. The rollback drops the Error Log as well, hence the explicit
    commit afterwards.

    Every step is guarded. A commit or rollback can fail in its own right, and
    an exception escaping here would abort the tick -- exactly the failure this
    whole helper exists to prevent.
    """
    try:
        frappe.db.rollback()
    except Exception:
        pass

    _log_import_failure(login_name)
    _record_fetch_attempt(login_name)

    try:
        frappe.db.commit()
    except Exception:
        pass


@frappe.whitelist()
def scheduled_import_fints_payments(manual=None):
    """Create payment entries by Kefiya Schedule.

    :param manual: Call manualy
    :type manual: bool
    :return: None
    """
    # Permission gate: whitelisted endpoints are callable by any logged-in
    # user, and `manual=1` additionally bypasses the frequency gate below --
    # so without this check anyone could trigger a bank fetch for every
    # configured login at will. The scheduler itself runs as Administrator and
    # is unaffected. Mirrors the gates added to the money endpoints in
    # kefiya/utils/client.py.
    frappe.has_permission("Kefiya Schedule", ptype="write", throw=True)

    schedule_settings = frappe.get_single('Kefiya Schedule')

    today = now_datetime().date()
    current_hour = now_datetime().strftime("%H")

    # Minimum number of days between two scheduled runs of the same login.
    FREQUENCY_GAP_DAYS = {'Daily': 1, 'Weekly': 7, 'Monthly': 30}

    # Minimum number of days before a login that FAILED is tried again. Kept at
    # one day regardless of the configured frequency: long enough to stop the
    # every-20-minutes retry loop, short enough that a bank in maintenance does
    # not cost a Monthly login the whole month.
    ATTEMPT_RETRY_GAP_DAYS = 1

    # Query child table
    for child_item in schedule_settings.schedule_items:
        if not (current_hour == child_item.hour or manual):
            continue
        try:
            if not (child_item.active and (child_item.import_frequency or manual)):
                continue

            login_name = child_item.kefiya_login
            bank_account, allowed_days, skip_fetch, last_attempt = (
                frappe.db.get_value(
                    "Kefiya Login", login_name,
                    ["bank_account", "allowed_sync_days_in_past",
                     "skip_fetch", "last_fetch_attempt"]
                ) or (None, None, 0, None))

            # Loan and clearing accounts are never offered for statement
            # retrieval, so fetching them fails every single run. Skipping them
            # keeps the failure list down to the ones worth looking at.
            if skip_fetch:
                continue

            # Frequency gate: use the last import RUN, not the transaction
            # end_date. A run that returned no transactions (weekend, or an
            # expired SCA) still counts as a run, so we neither hammer the bank
            # every 20 minutes nor get stuck refetching only "today" forever.
            #
            # A failed run counts too. It leaves no submitted Kefiya Import --
            # the recovery rolls back even the draft -- so a login that can
            # never succeed used to be retried on every tick, three times an
            # hour, forever. `last_fetch_attempt` is stamped on both paths, so
            # the newer of the two is what the gate goes by.
            if not manual:
                last = frappe.get_all(
                    "Kefiya Import",
                    filters={"kefiya_login": login_name, "docstatus": 1},
                    fields=["creation"],
                    order_by="creation desc",
                    limit=1,
                )
                gap = FREQUENCY_GAP_DAYS.get(child_item.import_frequency, 1)

                # A successful run closes the gate for the configured frequency.
                if last and (today - getdate(last[0].creation)).days < gap:
                    continue

                # A failed attempt closes it only until the next day. This
                # stamp exists to stop the 20-minute retry loop, not to punish
                # a login for a bank that happened to be in maintenance: with
                # Weekly or Monthly the full gap would push the next try a week
                # or a month out, and a few of those in a row reach the 90-day
                # FinTS window past which the transactions cannot be fetched at
                # all any more.
                if last_attempt and (today - getdate(last_attempt)).days \
                        < ATTEMPT_RETRY_GAP_DAYS:
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

            _record_fetch_attempt(login_name)

            # Settle this login before touching the next one. Without it a
            # later rollback would also discard the logins that already
            # succeeded in this tick.
            frappe.db.commit()
        except Exception:
            _recover_from_failed_login(child_item.kefiya_login)
