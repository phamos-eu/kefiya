# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""DKB Girokonto exports, read through the statement profiles.

From the original (phamos-eu/kefiya#60), where it is a reader of its own in
the old import document. Here it is a profile in statement_formats, like
DKB VISA. The expected values are the original's, on its sample file.
"""

import os
import unittest
from datetime import date

from kefiya.utils import statement_formats as formats

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "tests",
                        "fixtures")


def read_fixture(name="dkb_giro_umsatzliste.csv"):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        return handle.read()


def read(raw):
    name, columns, rows, header = formats.read_rows(formats.decode(raw))
    entries, pending, unreadable = formats.entries_of(
        rows, columns, formats.PROFILES[name], header)
    return name, entries, pending, unreadable


class TestDkbGiroIsRecognised(unittest.TestCase):

    def test_by_its_header(self):
        self.assertEqual(read(read_fixture())[0], "dkb_giro")

    def test_the_card_stays_the_card(self):
        self.assertEqual(read(read_fixture("dkb_visa_umsatzliste.csv"))[0],
                         "dkb_visa")


class TestDkbGiroRows(unittest.TestCase):

    def setUp(self):
        _name, self.entries, _pending, _unreadable = read(read_fixture())

    def test_row_count(self):
        self.assertEqual(len(self.entries), 12)

    def test_every_amount(self):
        """No trailing zeros in the export: "-450", "2.380,5", "-312,4"."""
        self.assertEqual(
            [e["amount"] for e in self.entries],
            [-450.0, 1190.0, -49.99, -19.90, -1500.0, 2380.5, -1234.56,
             -312.4, 99.0, -99.0, -12.5, -450.0])

    def test_withdrawal_names_the_payee(self):
        row = self.entries[2]
        self.assertEqual(row["date"], date(2026, 10, 28))
        self.assertEqual(row["counterparty"], "Beispiel Telekom AG")
        self.assertEqual(row["iban"], "DE58999999990002222222")
        self.assertEqual(row["description"],
                         "K1234567 R987654321 V123456789 Rechnung Oktober")

    def test_deposit_names_the_payer(self):
        row = self.entries[5]
        self.assertEqual(row["amount"], 2380.5)
        self.assertEqual(row["counterparty"], "BEISPIEL KUNDE AG")

    def test_the_date_is_the_booking_day(self):
        self.assertEqual(self.entries[3]["date"], date(2026, 10, 27))

    def test_the_customer_reference_is_not_the_identity(self):
        """It repeats -- a standing order carries the same one every month."""
        self.assertTrue(all(e["reference"] is None for e in self.entries))

    def test_references_are_unique(self):
        refs = [formats.reference_number("Konto", e) for e in self.entries]
        self.assertEqual(len(set(refs)), len(refs))


class TestPendingAndUnreadable(unittest.TestCase):

    def test_pending_rows_are_counted_not_booked(self):
        row = ('"01.11.26";"";"Vorgemerkt";"Muster GmbH";"Beispiel Leasing'
               ' GmbH";"LEASING RATE";"Ausgang";"DE89370400440532013000";'
               '"-450";"";"";""\n')
        _name, entries, pending, _unreadable = read(
            read_fixture() + row.encode("utf-8"))
        self.assertEqual((len(entries), pending), (12, 1))

    def test_an_unreadable_row_is_reported_by_its_line(self):
        # The original's broken row: no amount.
        broken = ('"02.10.26";"02.10.26";"Gebucht";"MUSTER KIOSK";'
                  '"Im Geschäft";"";""\n')
        raw = read_fixture("dkb_visa_umsatzliste.csv") + broken.encode("utf-8")
        _name, entries, _pending, unreadable = read(raw)
        self.assertEqual(len(entries), 14)
        self.assertEqual(unreadable, [20])


if __name__ == "__main__":
    unittest.main()
