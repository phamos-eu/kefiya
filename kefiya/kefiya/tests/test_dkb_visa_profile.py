# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""DKB VISA card exports, read through the statement profiles.

Came from the original (phamos-eu/kefiya#59) as a reader of its own inside
the old import document. Here it is one more profile in statement_formats, so
it shares the encoding, the header search, the dedup and the booking with every
other format -- and these tests check the values that profile has to get right.

The amounts are the reason this file exists. DKB drops trailing zeros: "12,5",
"-4,8", "1.500". The general amount parser reads "12,5" as 125, so the profile
declares the comma as decimal separator, and every amount of the fixture is
checked here against what the statement says.
"""

import os
import unittest
from datetime import date

from kefiya.utils import statement_formats as formats

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "..", "tests", "fixtures",
    "dkb_visa_umsatzliste.csv")


def read_fixture():
    with open(FIXTURE, "rb") as handle:
        return handle.read()


def entries_of(raw):
    """The booked entries of a file, as statement_import.read_entries reads them."""
    name, columns, rows, _header = formats.read_rows(formats.decode(raw))
    profile = formats.PROFILES[name]
    entries = []
    for row in rows:
        if not any(str(cell).strip() for cell in row):
            continue
        if not formats.is_booked(row, columns, profile):
            continue
        entries.append(formats.to_entry(row, columns, profile))
    return name, entries


class TestDkbVisaIsRecognised(unittest.TestCase):

    def test_header_below_the_card_summary(self):
        name, _columns, _rows, header = formats.read_rows(
            formats.decode(read_fixture()))
        self.assertEqual(name, "dkb_visa")
        self.assertEqual(header, 4)

    def test_without_byte_order_mark(self):
        name, _entries = entries_of(read_fixture()[3:])
        self.assertEqual(name, "dkb_visa")

    def test_a_giro_export_is_not_taken_for_a_card(self):
        """No Belegdatum and no Umsatztyp: not this profile."""
        name, _columns = formats.detect_profile(
            ["Buchungstag", "Valutadatum", "Verwendungszweck", "Betrag"])
        self.assertNotEqual(name, "dkb_visa")


class TestDkbVisaAmounts(unittest.TestCase):

    def test_every_amount_of_the_statement(self):
        _name, entries = entries_of(read_fixture())
        self.assertEqual(
            [entry["amount"] for entry in entries],
            [1500.0, 250.0, 12.5, -23.40, -7.0, -1234.56, -49.99, -19.90,
             -2000.0, -0.96, -1.05, -9.99, -15.0, -4.8])

    def test_the_comma_is_the_decimal_separator(self):
        for text, amount in [
            ("12,5", 12.5),
            ("-4,8", -4.8),
            ("1.500", 1500.0),
            ("-2.000", -2000.0),
            ("-1.234,56", -1234.56),
            ("-7", -7.0),
        ]:
            with self.subTest(text=text):
                self.assertEqual(
                    formats.parse_amount(text, decimal_comma=True), amount)

    def test_spending_is_money_out(self):
        """The export signs from the account's side: no card flip."""
        self.assertFalse(formats.PROFILES["dkb_visa"]["charges_are_positive"])


class TestDkbVisaRows(unittest.TestCase):

    def test_row_count(self):
        _name, entries = entries_of(read_fixture())
        self.assertEqual(len(entries), 14)

    def test_pending_rows_are_not_booked(self):
        pending = ('"01.11.26";"";"Vorgemerkt";"BEISPIEL MARKT KOELN";'
                   '"Im Geschäft";"-3,20";""\n')
        _name, entries = entries_of(read_fixture() + pending.encode("utf-8"))
        self.assertEqual(len(entries), 14)

    def test_row_fields(self):
        _name, entries = entries_of(read_fixture())

        settlement = entries[0]
        self.assertEqual(settlement["date"], date(2026, 10, 30))
        self.assertEqual(settlement["description"],
                         "Ausgleich Kreditkarte (Gutschrift)")

        foreign = entries[6]
        # Belegdatum, not Wertstellung
        self.assertEqual(foreign["date"], date(2026, 10, 18))
        self.assertEqual(foreign["counterparty"], "CLOUD DIENST BEISPIEL")
        self.assertEqual(foreign["description"],
                         "CLOUD DIENST BEISPIEL (Onlinezahlung, -52,30 $)")


class TestDkbVisaIdentity(unittest.TestCase):
    """The dedup runs on reference_number, plus wiederholung for repeats."""

    def test_references_are_stable(self):
        _name, first = entries_of(read_fixture())
        _name, second = entries_of(read_fixture())
        self.assertEqual(
            [formats.reference_number("Konto", e) for e in first],
            [formats.reference_number("Konto", e) for e in second])

    def test_identical_rows_stay_apart(self):
        row = ('"04.10.26";"04.10.26";"Gebucht";"MUSTER CAFE BERLIN";'
               '"Im Geschäft";"-4,8";""\n')
        _name, entries = entries_of(read_fixture() + row.encode("utf-8"))
        self.assertEqual(len(entries), 15)
        reference = formats.reference_number("Konto", entries[-1])
        self.assertEqual(reference,
                         formats.reference_number("Konto", entries[-2]))
        self.assertNotEqual(formats.wiederholung(reference, 1),
                            formats.wiederholung(reference, 2))


if __name__ == "__main__":
    unittest.main()
