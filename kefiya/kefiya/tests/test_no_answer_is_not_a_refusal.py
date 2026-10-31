# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A save that got no answer is not a save that failed.

Measured on 30.10.2026. A transfer was entered while the server was busy; the
gateway gave up after its timeout and answered 504 with an HTML page. The
form said "Not saved". The server had saved it. The order was entered again,
and the outbox held two identical drafts of 15.000,00 EUR -- one release away
from paying twice.

The form now asks before it says anything: an answer without the server's
words leaves the outcome open, and the outbox is searched for the draft.
"""

import os
import unittest


def _read(*parts):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


DIALOG = ("public", "js", "controllers", "transfer_form.js")


class TestNoAnswerIsNotARefusal(unittest.TestCase):

    def setUp(self):
        self.source = _read(*DIALOG)
        self.submit = self.source.split(
            "kefiya.transfer_form_submit = ")[1].split(
            "kefiya.outcome_unknown = ")[0]

    def test_the_open_outcome_is_asked_before_not_saved(self):
        catch = self.submit.split(".catch(function (r) {")[1]
        self.assertLess(catch.index("kefiya.outcome_unknown(r)"),
                        catch.index('__("Not saved")'))

    def test_a_gateway_timeout_counts_as_open(self):
        rule = self.source.split("kefiya.outcome_unknown = ")[1][:700]
        self.assertIn("[0, 502, 503, 504]", rule)
        self.assertIn("kefiya.server_message(r)", rule,
                      "A refusal with the server's words is a refusal.")

    def test_the_outbox_is_searched_for_the_draft(self):
        look = self.source.split("kefiya.transfer_form_unknown = ")[1][:2600]
        for needle in ('"frappe.client.get_list"',
                       '["Kefiya Transfer", "docstatus", "=", 0]',
                       '"recipient_iban", "="',
                       '"amount", "=", item.amount]',
                       '"creation", ">=", started]'):
            self.assertIn(needle, look)

    def test_the_start_is_taken_before_the_call(self):
        self.assertLess(self.submit.index("const started"),
                        self.submit.index('"frappe.client.insert"'))


if __name__ == "__main__":
    unittest.main()
