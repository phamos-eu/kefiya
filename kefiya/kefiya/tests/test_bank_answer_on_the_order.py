# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What the bank said when it took an order, written on the order.

Measured on 30.10.2026 at a comdirect account: an instant transfer was
released with a photoTAN and marked "Sent" -- correctly, the bank had refused
nothing -- and the banking app did not show it. The bank's answer to the TAN
was nowhere to be read. Kefiya Transfer has carried a "Bank Response" field
all along, and nothing ever wrote to it.

Not the Error Log: an accepted order is not an error.
"""

import os
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


def _read(*parts):
    with open(os.path.join(WURZEL, *parts), encoding="utf-8") as handle:
        return handle.read()


class TestTheAnswerIsKept(unittest.TestCase):

    def setUp(self):
        self.client = _read("utils", "client.py")
        self.controller = _read("utils", "fints_controller.py")

    def test_marking_sent_writes_it(self):
        marked = self.client.split("def _mark_sent(")[1].split("\ndef ")[0]
        self.assertIn('doc.db_set("bank_reference", result["bank_said"])',
                      marked)
        self.assertNotIn("log_error", marked)

    def test_the_release_after_a_tan_hands_it_over(self):
        release = self.client.split("def send_transfer_tan(")[1].split(
            "\ndef ")[0]
        self.assertIn('_mark_sent(docs, {"bank_said": '
                      "_what_the_bank_said_to_the_tan(", release)
        said = self.client.split("def _what_the_bank_said_to_the_tan(")[1]
        self.assertIn('"init_tan_response"', said[:600])

    def test_a_send_without_tan_hands_it_over(self):
        self.assertIn('"bank_said": fints_response.as_text(verdict)}',
                      self.controller)


if __name__ == "__main__":
    unittest.main()
