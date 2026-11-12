# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Holdings of a depot reach the system.

Two defects kept every depot empty, and neither left a trace:

1. The fetch asked the login's own account for holdings. A login points at a
   payment account; a depot has no IBAN at most banks and can never be that
   account. The bank refused, the refusal was filed as "not supported", and
   nothing was logged.
2. Had a depot answered, the rows would still have been dropped: python-fints
   names the field ``ISIN`` and the controllers read ``isin``, so every row
   arrived without an identity and ``refresh_holdings`` skipped it.
"""

import json
import os
import unittest
from collections import namedtuple

from kefiya.utils.securities_rows import (holding_rows,
                                          same_account_number,
                                          sold_positions,
                                          depot_fetch_due)

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.normpath(os.path.join(HERE, "..", ".."))


def _read(*parts):
    with open(os.path.join(APP, *parts), encoding="utf-8") as handle:
        return handle.read()


# Same field names as fints.models.Holding, without importing fints.
Holding = namedtuple(
    "Holding",
    "ISIN name market_value value_symbol valuation_date pieces total_value "
    "acquisitionprice")


class TestHoldingRows(unittest.TestCase):

    def test_the_isin_survives_the_mapping(self):
        rows = holding_rows([Holding(
            "xx0000000002", "Example Fund", 12.5, "EUR", "2026-10-30",
            4.0, 50.0, 11.0)], "1234567")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["isin"], "XX0000000002")
        self.assertEqual(row["security_name"], "Example Fund")
        self.assertEqual(row["quantity"], 4.0)
        self.assertEqual(row["price"], 12.5)
        self.assertEqual(row["market_value"], 50.0)
        self.assertEqual(row["acquisition_price"], 11.0)
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["valuation_date"], "2026-10-30")
        self.assertEqual(row["securities_account"], "1234567")

    def test_no_holdings_is_no_rows(self):
        self.assertEqual(holding_rows(None, "1"), [])
        self.assertEqual(holding_rows([], "1"), [])

    def test_dict_input_is_read_as_well(self):
        rows = holding_rows([{"isin": "xx0000000001", "pieces": 1}], "9")
        self.assertEqual(rows[0]["isin"], "XX0000000001")
        self.assertEqual(rows[0]["quantity"], 1)


class TestAccountNumbers(unittest.TestCase):

    def test_leading_zeros_do_not_matter(self):
        self.assertTrue(same_account_number("0001234", "1234"))
        self.assertTrue(same_account_number(1234, "1234"))

    def test_different_or_empty_numbers_do_not_match(self):
        self.assertFalse(same_account_number("1234", "1235"))
        self.assertFalse(same_account_number("", ""))
        self.assertFalse(same_account_number(None, "0"))


class TestControllersShareTheMapping(unittest.TestCase):

    def test_neither_controller_reads_a_lowercase_isin(self):
        for name in ("fints_controller.py", "fints_controller_legacy.py"):
            source = _read("utils", name)
            self.assertNotIn('getattr(h, "isin"', source, name)
            self.assertIn("holding_rows(", source, name)


class TestDepotsAreFetchedByAccountNumber(unittest.TestCase):

    def test_fetch_all_reaches_the_linked_depots(self):
        source = _read("utils", "client.py")
        self.assertIn("refresh_linked_depots(kefiya_login, FetchCtl)", source)

    def test_the_depot_is_addressed_without_an_iban(self):
        source = _read("utils", "fints_controller.py")
        start = source.index("def get_fints_depot_holdings(")
        body = source[start:source.index("\n    def ", start + 1)]
        self.assertIn("iban=None", body)
        self.assertIn("accountnumber=number", body)
        self.assertIn("NeedTANResponse", body)

    def test_a_failed_depot_is_logged_not_swallowed(self):
        source = _read("utils", "securities.py")
        start = source.index("def refresh_one_depot(")
        body = source[start:source.index("\ndef ", start + 1)]
        self.assertIn("frappe.log_error(", body)
        self.assertIn('"Failed"', body)


class TestSoldPositions(unittest.TestCase):
    """A sold position otherwise just stops appearing: the last snapshot
    stays as history, and nothing says whether it was closed or whether the
    fetch came back short."""

    def test_what_is_missing_counts_as_sold(self):
        self.assertEqual(
            ["DE0000000001"],
            sold_positions(["DE0000000001", "DE0000000002"],
                           ["DE0000000002"]))

    def test_spelling_and_empties_do_not_count(self):
        self.assertEqual(
            [], sold_positions([" de0000000002 ", "", None],
                               ["DE0000000002"]))

    def test_a_new_position_has_not_vanished(self):
        self.assertEqual([], sold_positions([], ["DE0000000003"]))
        self.assertEqual([], sold_positions(None, None))

    def test_the_order_is_fixed(self):
        """Two runs must produce the same one, or every store looks
        different."""
        self.assertEqual(
            ["DE0000000001", "DE0000000002"],
            sold_positions(["DE0000000002", "DE0000000001"], []))


class TestClosingSoldPositions(unittest.TestCase):

    def setUp(self):
        source = _read("utils", "securities.py")
        start = source.index("def _close_sold_positions(")
        self.body = source[start:source.index("\ndef ", start + 1)]
        self.module = source

    def test_they_are_carried_forward_with_quantity_zero(self):
        self.assertIn("sold_positions(held.keys(), snapshot[\"isins\"])",
                      self.body)
        self.assertIn('"quantity": 0.0', self.body)
        self.assertIn('"market_value": 0.0', self.body)

    def test_the_price_is_zero_too(self):
        """A price left standing reads as a current one."""
        self.assertIn('"price": 0.0', self.body)

    def test_a_holding_entered_by_hand_is_never_touched(self):
        """It is somebody's own record of a depot the bank does not report.
        Zeroing it would throw their work away. Both reads and the write
        name the source, so a manual row is neither consulted nor written."""
        date_lookup, rows_at_that_date = self.body.split(
            'order_by="valuation_date desc"')
        self.assertIn('"source": SOURCE_BANK', date_lookup)
        self.assertIn('"source": SOURCE_BANK', rows_at_that_date)
        self.assertIn('"source": SOURCE_BANK', self.body[
            self.body.index("_upsert_snapshot_row("):])

    def test_compared_against_the_last_snapshot_before_this_one(self):
        """Not against everything ever held -- otherwise every position sold
        once would be zeroed again at every single fetch."""
        self.assertIn('"valuation_date": ("<", snapshot["valuation_date"])',
                      self.body)
        self.assertIn('order_by="valuation_date desc"', self.body)

    def test_a_zero_row_is_not_written_twice(self):
        self.assertIn('if flt(row.get("quantity")) == 0:', self.body)

    def test_it_never_raises(self):
        """By the time it runs, the snapshot itself is stored."""
        self.assertIn("except Exception:", self.body)
        self.assertIn("return 0", self.body)

    def test_one_writer_for_both_kinds_of_row(self):
        """The reported positions and the zero rows that close the others go
        through the same upsert, so their identity cannot drift. One place
        creates a holding row, and it is that one."""
        self.assertIn("_upsert_snapshot_row(", self.body)
        self.assertEqual(1, self.module.count("doc.insert("))

    def test_a_manual_row_at_the_same_date_is_left_alone(self):
        """compute_holding_key does not know the source, so without this the
        zero row would land on top of a position somebody keeps by hand --
        and the bank not reporting it is exactly why they do."""
        self.assertIn("if standing not in (None, SOURCE_BANK):", self.body)
        self.assertLess(self.body.index("standing not in"),
                        self.body.index("_upsert_snapshot_row("))

    def test_the_result_counts_them(self):
        start = self.module.index("def refresh_holdings(")
        body = self.module[start:self.module.index("\ndef ", start + 1)]
        self.assertIn('"closed": closed', body)
        self.assertIn("_close_sold_positions(", body)


class TestTheScheduleFetchesDepots(unittest.TestCase):
    """The depot fetch hung off fetch_all, which hangs off the button. A
    state that only appears when somebody presses is not a state."""

    def setUp(self):
        self.py = _read("kefiya", "doctype", "kefiya_schedule",
                        "kefiya_schedule.py")

    def test_the_run_reaches_the_due_depots(self):
        self.assertIn("_fetch_due_depots(login_name, controller_class)",
                      self.py)
        start = self.py.index("def _fetch_due_depots(")
        body = self.py[start:self.py.index("\ndef ", start + 1)]
        self.assertIn("refresh_linked_depots(login_name, controller_class)",
                      body)

    def test_after_the_transactions_are_committed(self):
        """A database error makes the running transaction unusable. Placed
        before the commit, the snapshot could have taken the bookings with
        it."""
        start = self.py.index("def scheduled_import_fints_payments(")
        run = self.py[start:]
        self.assertLess(run.index("frappe.db.commit()"),
                        run.index("_fetch_due_depots(login_name"))

    def test_and_costs_the_transactions_nothing(self):
        start = self.py.index("def _fetch_due_depots(")
        body = self.py[start:self.py.index("\ndef ", start + 1)]
        self.assertIn("except TanInteractionRequired:", body)
        self.assertIn("except Exception:", body)
        self.assertLess(body.index("except TanInteractionRequired:"),
                        body.index("except Exception:"))

    def test_the_controller_choice_is_made_once(self):
        """The import and the depot fetch must not read the toggle apart."""
        self.assertEqual(
            1, self.py.count('"Kefiya Login", login_name, '
                             '"enable_tan_authentication"'))
        self.assertIn("controller_class(login_name) \\", self.py)


class TestOneDepotFetchPerCalendarDay(unittest.TestCase):
    """A fetch by hand in the evening used to block the scheduled one the
    next morning (20-hour gate). One fetch per calendar day keeps the
    morning run with the bank fetch."""

    def test_never_fetched_is_due(self):
        self.assertTrue(depot_fetch_due(None, "2026-11-02 06:01:00"))

    def test_evening_click_does_not_block_the_next_morning(self):
        self.assertTrue(
            depot_fetch_due("2026-11-01 21:26:10", "2026-11-02 06:01:00"))

    def test_second_fetch_on_the_same_day_is_not_due(self):
        self.assertFalse(
            depot_fetch_due("2026-11-02 06:01:00", "2026-11-02 21:00:00"))

    def test_the_schedule_uses_the_calendar_day_gate(self):
        source = _read("utils", "securities.py")
        self.assertIn("depot_fetch_due(last, now)", source)
        self.assertNotIn("DEPOT_FETCH_MIN_HOURS", source)


class TestDoctypes(unittest.TestCase):

    def _fields(self, doctype_dir):
        meta = json.loads(_read(
            "kefiya", "doctype", doctype_dir, doctype_dir + ".json"))
        names = {f["fieldname"]: f for f in meta["fields"]}
        for fieldname in meta["field_order"]:
            self.assertIn(fieldname, names, fieldname)
        return names

    def test_a_depot_links_to_its_bank_and_records_its_fetch(self):
        fields = self._fields("kefiya_securities_account")
        for name in ("online_banking_url", "subaccount_number",
                     "fetch_holdings", "last_holdings_fetch",
                     "last_holdings_status", "last_holdings_message"):
            self.assertIn(name, fields)
        self.assertEqual(fields["online_banking_url"].get("options"), "URL")

    def test_a_holding_can_be_entered_by_hand(self):
        fields = self._fields("kefiya_security_holding")
        self.assertFalse(fields["kefiya_login"].get("reqd"))
        self.assertIn("Manual", fields["source"]["options"])
        self.assertIn("acquisition_price", fields)


if __name__ == "__main__":
    unittest.main()
