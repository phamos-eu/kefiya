# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The parsed rows of the three sample exports, field by field.

statement_golden.json was written by the hardcoded parsers before the mapping engine
replaced them. Transaction ids must never change, or a re-import duplicates every row.
"""

import json
import os

from frappe.tests.utils import FrappeTestCase

from kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import import parse_statement

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def read(name) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def as_json(rows):
    return [{key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items()}
            for row in rows]


class TestStatementGolden(FrappeTestCase):
    def test_rows_match_the_snapshot(self):
        with open(os.path.join(FIXTURES, "statement_golden.json"), encoding="utf-8") as f:
            golden = json.load(f)
        for name, expected in golden.items():
            with self.subTest(file=name):
                self.assertEqual(as_json(parse_statement(read(name))), expected)
