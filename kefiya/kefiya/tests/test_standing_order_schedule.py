# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Standing orders kept here: when they run, and how a large one is split.

Moved from the instance's scripts on 30.10.2026. The schedule rule and the
split under the transfer limit decide what money leaves and when, so they
run here, without frappe, on real dates.
"""

import datetime
import json
import os
import unittest

from kefiya.utils import standing_order_schedule as s

D = datetime.date
HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


def _read(*parts):
    with open(os.path.join(WURZEL, *parts), encoding="utf-8") as handle:
        return handle.read()


class TestTheNextDate(unittest.TestCase):

    def test_the_first_date_on_or_after_today(self):
        self.assertEqual(
            s.next_execution("2026-01-15", 15, "Monthly", D(2026, 10, 30)),
            D(2026, 11, 15))

    def test_today_itself_counts(self):
        self.assertEqual(
            s.next_execution("2026-01-15", 30, "Monthly", D(2026, 10, 30)),
            D(2026, 10, 30))

    def test_a_missing_day_is_the_month_end(self):
        self.assertEqual(
            s.next_execution("2026-01-31", 31, "Monthly", D(2027, 2, 1)),
            D(2027, 2, 28))

    def test_quarterly_is_anchored_on_valid_from(self):
        self.assertEqual(
            s.next_execution("2026-02-10", 10, "Quarterly", D(2026, 10, 30)),
            D(2026, 11, 10))

    def test_never_again_for_a_date_already_executed(self):
        self.assertEqual(
            s.next_execution("2026-01-15", 15, "Monthly", D(2026, 11, 15),
                             last_executed_for="2026-11-15"),
            D(2026, 12, 15))

    def test_a_date_set_on_the_schedule_survives(self):
        self.assertEqual(
            s.next_execution("2026-01-15", 15, "Monthly", D(2026, 10, 30),
                             current="2027-03-15"),
            D(2027, 3, 15))

    def test_a_date_off_the_schedule_does_not(self):
        self.assertEqual(
            s.next_execution("2026-01-15", 15, "Monthly", D(2026, 10, 30),
                             current="2026-11-16"),
            D(2026, 11, 15))

    def test_after_the_end_of_validity_there_is_none(self):
        self.assertIsNone(
            s.next_execution("2026-01-15", 15, "Monthly", D(2026, 10, 30),
                             valid_until="2026-11-10"))

    def test_the_iban_shape(self):
        self.assertTrue(s.iban_is_plausible("DE89 9999 9999 0001 1111 11"))
        self.assertFalse(s.iban_is_plausible("12345"))


class TestSplittingUnderTheLimit(unittest.TestCase):

    def test_it_fits_on_the_day(self):
        parts, rest = s.split_backwards(800, D(2026, 11, 16), D(2026, 11, 10),
                                        lambda day: 5000)
        self.assertEqual((parts, rest), ([(D(2026, 11, 16), 800.0)], 0.0))

    def test_too_much_goes_onto_the_banking_days_before(self):
        # 16.11.2026 is a Monday: Sunday and Saturday are skipped.
        parts, rest = s.split_backwards(11900, D(2026, 11, 16),
                                        D(2026, 11, 10), lambda day: 5000)
        self.assertEqual(rest, 0.0)
        self.assertEqual(parts, [(D(2026, 11, 12), 1900.0),
                                 (D(2026, 11, 13), 5000.0),
                                 (D(2026, 11, 16), 5000.0)])

    def test_never_before_today(self):
        parts, rest = s.split_backwards(11900, D(2026, 11, 16),
                                        D(2026, 11, 16), lambda day: 5000)
        self.assertEqual(rest, 6900.0)

    def test_a_holiday_takes_nothing(self):
        parts, _ = s.split_backwards(100, D(2026, 12, 28), D(2026, 12, 20),
                                     lambda day: 5000)
        self.assertNotIn(D(2026, 12, 25), [p[0] for p in parts])


class TestItIsWiredUp(unittest.TestCase):

    def test_the_doctype_is_the_apps(self):
        meta = json.loads(_read("kefiya", "doctype", "kefiya_standing_order",
                                "kefiya_standing_order.json"))
        fields = {f["fieldname"] for f in meta["fields"]}
        for name in ("status", "execution_mode", "amount", "frequency",
                     "day_of_month", "next_execution_date", "approved_by",
                     "last_transfer", "last_executed_for", "detection_key"):
            self.assertIn(name, fields)
        self.assertEqual(meta["module"], "Kefiya")
        self.assertNotIn("custom", meta)

    def test_activating_is_approving_under_four_eyes(self):
        controller = _read("kefiya", "doctype", "kefiya_standing_order",
                           "kefiya_standing_order.py")
        self.assertIn("four_eyes.conflict(user, self.owner, changed_by)",
                      controller)
        self.assertIn("self.approved_by = user", controller)

    def test_the_job_places_approved_transfers_and_sends_nothing(self):
        job = _read("utils", "standing_order_execute.py")
        self.assertIn('"execution_mode": "System"', job)
        self.assertIn("frappe.set_user(order.approved_by)", job)
        self.assertIn("transfer.submit()", job)
        self.assertIn('"standing_order": order.name', job)
        self.assertNotIn("send_transfer", job)
        self.assertNotIn("submit_sepa_transfer", job)

    def test_it_runs_daily_and_can_be_paused(self):
        self.assertIn(
            "kefiya.utils.standing_order_execute.place_due_transfers",
            _read("hooks.py"))
        settings = json.loads(_read("kefiya", "doctype", "kefiya_settings",
                                    "kefiya_settings.json"))
        pause = [f for f in settings["fields"]
                 if f["fieldname"] == "pause_standing_orders"][0]
        self.assertEqual(pause["default"], "0")

    def test_a_site_built_doctype_is_taken_over_before_the_sync(self):
        patches = _read("patches.txt")
        pre = patches.split("[post_model_sync]")[0]
        self.assertIn("kefiya.patches.v0_5.standing_order_into_the_app", pre)


if __name__ == "__main__":
    unittest.main()


class TestATransferCanStartOne(unittest.TestCase):
    """"Dauerauftrag als Option bei Überweisungen, auch in der Liste
    filterbar" -- the option in the entry dialog and the filter in the
    outgoing payments."""

    def test_the_entry_dialog_offers_it_for_new_transfers_only(self):
        form = _read("public", "js", "controllers", "transfer_form.js")
        block = form.split("if (!isEdit) {")[1][:1500]
        self.assertIn('fieldname: "repeat"', block)
        self.assertIn('fieldname: "frequency"', block)
        self.assertIn('fieldname: "valid_until"', block)
        self.assertIn("kefiya.repeat_as_standing_order(entered, values",
                      form)

    def test_the_first_transfer_is_the_first_execution(self):
        endpoint = _read("utils", "standing_order_from_transfer.py")
        self.assertIn('"last_executed_for": str(first)', endpoint)
        self.assertIn('"status": "Proposed"', endpoint)
        self.assertIn("if not order.needs_second_person():", endpoint)
        self.assertIn('transfer.db_set("standing_order", order.name)',
                      endpoint)

    def test_the_outbox_filters_by_it(self):
        server = _read("utils", "outbox.py")
        self.assertIn('filters["standing_order"] = ["is", "set"]', server)
        page = _read("public", "js", "controllers", "payment_outbox.js")
        self.assertIn("data-act='standing'", page)
        self.assertIn("standing_only: view.standingOnly ? 1 : 0", page)
