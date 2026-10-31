# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The bank's standing orders, written down exactly as they came.

HICDB is read without a declared segment: cycle, rhythm and day of execution
come in an order nothing in python-fints confirms. The raw elements are what
confirms it, and the fetch used to read them and keep only a count.

Executed, not read: the formatter runs here without frappe and without a bank.
"""

import os
import unittest

from kefiya.utils import standing_order_evidence as evidence

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


class Header:
    type = "HICDB"
    version = 1
    number = 4


class Segment:
    header = Header()

    def __init__(self, data):
        self._additional_data = data


PAIN = (b"<Document><InstdAmt Ccy='EUR'>80.00</InstdAmt>"
        b"<Cdtr><Nm>Erika Beispiel</Nm></Cdtr></Document>")
ORDER = Segment([["DE89999999990001111111", "BYLADEM1"],
                 "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09", PAIN,
                 "ORDER-1", ["M", "1", "15", "20260115", None], "J", "N"])


class TestEveryElementIsKept(unittest.TestCase):

    def test_by_position_with_its_type(self):
        lines = evidence.segment_lines(ORDER)
        self.assertEqual(lines[0], "HICDB v1 #4")
        self.assertEqual(len(lines), 1 + 7)
        self.assertTrue(lines[3].startswith("  [2] b"))
        self.assertIn("Erika Beispiel", lines[3])
        self.assertIn("['M', '1', '15', '20260115', None]", lines[5])

    def test_nothing_is_interpreted(self):
        """The schedule stays as the bank sent it -- that is the point."""
        text = evidence.message("Login A", [ORDER])
        self.assertNotIn("monthly", text)
        self.assertIn("'M'", text)

    def test_the_reading_sits_next_to_the_raw(self):
        text = evidence.message("Login A", [ORDER], said="0020 ok",
                                parsed=[{"amount": 80.0,
                                         "schedule": {"cycle": "monthly"}}])
        self.assertIn("What the bank said to HKCDB: 0020 ok", text)
        self.assertIn("read as: amount=80.0", text)

    def test_an_empty_answer_still_says_so(self):
        text = evidence.message("Login A", [], said="3010 keine Eintraege")
        self.assertIn("HICDB segments: 0", text)
        self.assertIn("3010", text)

    def test_it_never_raises(self):
        class Broken:
            @property
            def header(self):
                raise RuntimeError("no header")
        self.assertIn("login=Login A", evidence.message("Login A", [Broken()]))
        self.assertEqual(evidence.describe(b"\xff\xfe"), "b'\ufffd\ufffd'")


class TestTheFetchWritesItDown(unittest.TestCase):

    def setUp(self):
        with open(os.path.join(WURZEL, "utils", "standing_orders.py"),
                  encoding="utf-8") as handle:
            self.source = handle.read()

    def test_after_reading_once_a_week(self):
        fetch = self.source.split("def fetch_standing_orders(")[1]
        self.assertIn("_write_down_the_answer(login, command_seg, response,"
                      " segments, page)", fetch)
        self.assertIn("EVIDENCE_EVERY_SECONDS = 7 * 24 * 3600", self.source)

    def test_as_a_file_on_the_login_not_in_the_error_log(self):
        """The Error Log is for errors. This is an answer given without
        complaint."""
        note = self.source.split("def _write_down_the_answer(")[1].split(
            "def fetch_standing_orders(")[0]
        self.assertIn('"doctype": "File"', note)
        self.assertIn('"attached_to_doctype": "Kefiya Login"', note)
        self.assertIn('"is_private": 1', note)
        self.assertNotIn("log_error", note)

    def test_the_file_is_named_by_day(self):
        self.assertEqual(evidence.file_name("2026-10-30"),
                         "dauerauftraege-bankantwort-2026-10-30.txt")

    def test_the_controller_says_which_login(self):
        with open(os.path.join(WURZEL, "utils", "fints_controller.py"),
                  encoding="utf-8") as handle:
            controller = handle.read()
        self.assertIn("login=self.kefiya_login.name)", controller)


if __name__ == "__main__":
    unittest.main()
