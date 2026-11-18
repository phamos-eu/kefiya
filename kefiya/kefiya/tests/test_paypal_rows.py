# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""PayPal transactions as bookings: amount and fee apart, nothing twice.

Rules agreed on 18.11.2026; the records below follow the shape PayPal's
Transaction Search returns, with invented values.
"""

import datetime
import os
import unittest

from kefiya.utils import paypal_rows as pr


def record(status="S", value="-25.00", fee="-0.85", currency="EUR",
           tx_id="1AB23456CD789012E"):
    info = {"transaction_id": tx_id,
            "transaction_status": status,
            "transaction_initiation_date": "2026-11-17T09:12:00+0000",
            "transaction_amount": {"currency_code": currency,
                                   "value": value},
            "transaction_subject": "Order 4711"}
    if fee is not None:
        info["fee_amount"] = {"currency_code": currency, "value": fee}
    return {"transaction_info": info,
            "payer_info": {"payer_name": {"alternate_full_name":
                                          "Erika Mustermann"}}}


class TestWhatABookingIs(unittest.TestCase):

    def test_amount_and_fee_on_lines_of_their_own(self):
        rows = pr.rows_from(record(value="50.00", fee="-1.70"), "EUR")
        self.assertEqual([r["amount"] for r in rows], [50.0, -1.7])
        self.assertEqual(rows[0]["reference"], "PAYPAL-1AB23456CD789012E")
        self.assertEqual(rows[1]["reference"],
                         "PAYPAL-1AB23456CD789012E-FEE")
        self.assertEqual(rows[0]["date"], "2026-11-17")
        self.assertEqual(rows[0]["counterparty"], "Erika Mustermann")
        self.assertIn("Order 4711", rows[0]["description"])

    def test_no_fee_no_fee_line(self):
        self.assertEqual(len(pr.rows_from(record(fee=None), "EUR")), 1)
        self.assertEqual(len(pr.rows_from(record(fee="0.00"), "EUR")), 1)

    def test_only_completed_ones(self):
        for status in ("P", "D", "V"):
            self.assertEqual(pr.rows_from(record(status=status), "EUR"), [])

    def test_a_foreign_currency_is_booked_by_its_conversion(self):
        self.assertEqual(pr.rows_from(record(currency="USD"), "EUR"), [])

    def test_no_id_no_booking(self):
        self.assertEqual(pr.rows_from(record(tx_id=""), "EUR"), [])


class TestWindows(unittest.TestCase):

    def test_at_most_31_days_each_and_no_gap(self):
        start = datetime.datetime(2026, 1, 1)
        end = datetime.datetime(2026, 3, 15)
        parts = pr.windows(start, end)
        self.assertEqual(parts[0][0], start)
        self.assertEqual(parts[-1][1], end)
        for (a, b), (c, _) in zip(parts, parts[1:]):
            self.assertEqual(b, c)
        for a, b in parts:
            self.assertLessEqual((b - a).days, 31)

    def test_nothing_to_fetch(self):
        moment = datetime.datetime(2026, 1, 1)
        self.assertEqual(pr.windows(moment, moment), [])

    def test_the_timestamp_format(self):
        self.assertEqual(pr.as_iso(datetime.datetime(2026, 1, 2, 3, 4, 5)),
                         "2026-01-02T03:04:05+0000")


def _read(*parts):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestTheFetch(unittest.TestCase):

    def setUp(self):
        self.source = _read("utils", "paypal.py")

    def test_nothing_twice(self):
        self.assertIn('"reference_number": entry["reference"]', self.source)

    def test_drafts_through_the_shared_constructor(self):
        self.assertIn("statement_import.create_booking(", self.source)
        self.assertNotIn(".submit()", self.source)

    def test_the_secret_never_reaches_a_message(self):
        self.assertIn('doc.get_password("client_secret")', self.source)
        self.assertEqual(self.source.count("from None"), 5)

    def test_overlap_for_late_listings(self):
        self.assertIn("days=OVERLAP_DAYS", self.source)

    def test_it_runs_daily(self):
        self.assertIn('"kefiya.utils.paypal.fetch_all_accounts"',
                      _read("hooks.py"))
