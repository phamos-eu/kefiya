# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A transfer's TAN is answered for the order, not for the login.

Found by reading the code on 31.10.2026, not on a run: on the Kefiya Transfer
form the TAN box was the form's own (fints_interactive.js), and it sent the
TAN to resolve_tan_interaction. That releases the parked order at the bank and
marks nothing -- the transfer stayed "Approved" with its money gone, and once
the send lock ran out after ten minutes "Send now" would have paid it again.

send_transfer_tan is the path that reads what the bank answered and marks the
order sent only on a release. Every box that answers for a transfer goes
there, the typed TAN and the release in the banking app alike.
"""

import os
import unittest

CONTROLLERS = os.path.join(os.path.dirname(__file__), "..", "..", "public",
                           "js", "controllers")


def _js(name):
    with open(os.path.join(CONTROLLERS, name), encoding="utf-8") as handle:
        return handle.read()


def _between(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


class TestTheTransferFormAnswersForTheOrder(unittest.TestCase):

    def test_the_form_hands_the_order_to_the_shared_box(self):
        source = _js("fints_interactive.js")
        branch = _between(source, 'frm.doctype === "Kefiya Transfer"',
                          "return;")
        self.assertIn("kefiya.tan_prompt(data,", branch)
        self.assertIn("transfer: {", branch)
        self.assertIn("names: [frm.doc.name]", branch)

    def test_it_branches_off_before_the_forms_own_box(self):
        source = _js("fints_interactive.js")
        self.assertLess(source.index('frm.doctype === "Kefiya Transfer"'),
                        source.index("frappe.prompt("))


class TestTheBoxNeverResolvesATransfer(unittest.TestCase):

    def setUp(self):
        source = _js("tan_prompt.js")
        self.submit = _between(source, "var dialog = frappe.prompt(fields,",
                               '"kefiya.utils.client.resolve_tan_interaction"')

    def test_a_typed_tan_goes_to_send_transfer_tan(self):
        self.assertIn("if (transfer && data.tan_required) {", self.submit)
        self.assertIn("answerTransferTan(transfer, values.tan, done);",
                      self.submit)

    def test_a_release_in_the_app_goes_there_too(self):
        branch = _between(self.submit, "if (transfer && data.mfa_required) {",
                          'answerTransferTan(transfer, "", done);')
        self.assertIn("awaitingRelease[transfer.login]", branch)

    def test_the_polling_says_while_it_runs(self):
        source = _js("tan_prompt.js")
        poll = _between(source, "function awaitRelease(opts) {",
                        "kefiya.await_release = awaitRelease;")
        self.assertIn("awaitingRelease[login] = true;", poll)
        self.assertEqual(poll.count("delete awaitingRelease[login];"), 2,
                         "finish() and stop() must both clear it")


if __name__ == "__main__":
    unittest.main()
