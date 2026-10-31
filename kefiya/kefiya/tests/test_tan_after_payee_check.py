# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The TAN after a payee check reaches the person who has to type it.

Measured on 30.10.2026 at a comdirect account. The bank could not check the
payee, the release box asked, the user released -- and the bank asked for a
TAN. It asks over the live connection, and the outgoing-payments page only
listened while a request of its own was running. The release box's request is
not one of those, so the TAN box never opened, several times in a row. The
order sat with "payee check open", and pressing Send did nothing at all.

And had the box opened, it would have answered through
resolve_tan_interaction -- which neither reads what the bank said nor marks
the order sent.

Source tests: the order of the lines is the point, and there is no browser.
"""

import os
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


def _read(*parts):
    with open(os.path.join(WURZEL, *parts), encoding="utf-8") as handle:
        return handle.read()


OUTBOX = ("public", "js", "controllers", "payment_outbox.js")
PROMPT = ("public", "js", "controllers", "tan_prompt.js")


class TestTheBoxOpens(unittest.TestCase):

    def setUp(self):
        self.outbox = _read(*OUTBOX)

    def test_the_listener_asks_for_the_orders_not_for_busy(self):
        listener = self.outbox.split(
            'frappe.realtime.on("fints_tan_interaction_required"')[1][:1400]
        self.assertIn("const want = live.tanFor;", listener)
        self.assertIn("{ transfer: want }", listener)
        self.assertNotIn("if (!live || !live.busy) return;", listener)

    def test_every_way_to_the_bank_says_which_orders(self):
        send = self.outbox.split("function handToBank(")[1][:200]
        self.assertIn("expectTan(login, names);", send)
        payee = self.outbox.split('} else if (outcome === "payee") {')[1][:80]
        self.assertIn("expectTan(login, names);", payee)
        one_by_one = self.outbox.split("function step(index, sent)")[1]
        self.assertLess(one_by_one.index("expectTan(orders[index].kefiya_login"),
                        one_by_one.index("submit_kefiya_transfer"))
        sequential_payee = self.outbox.split('case "payee":')[1][:120]
        self.assertIn("expectTan(", sequential_payee)

    def test_send_says_why_nothing_goes(self):
        send = self.outbox.split("function send() {")[1][:900]
        self.assertIn("r.blocked", send)
        self.assertIn('__("Not sent")', send)


class TestTheAnswerMarksTheOrder(unittest.TestCase):

    def setUp(self):
        self.prompt = _read(*PROMPT)

    def test_a_transfer_tan_goes_to_send_transfer_tan(self):
        answer = self.prompt.split("function answerTransferTan(")[1][:600]
        self.assertIn('"kefiya.utils.client.send_transfer_tan"', answer)
        self.assertIn("transfer_names: JSON.stringify(transfer.names", answer)

    def test_it_is_chosen_before_the_general_resolve(self):
        box = self.prompt.split("var dialog = frappe.prompt(fields")[1]
        self.assertLess(box.index("answerTransferTan(transfer"),
                        box.index("resolve_tan_interaction"))

    def test_only_a_release_counts_as_done(self):
        answer = self.prompt.split("function answerTransferTan(")[1][:1800]
        self.assertIn('if (m.status === "submitted")', answer)
        self.assertIn("if (done) done(true);", answer)

    def test_a_sent_order_has_no_open_payee_check(self):
        client = _read("utils", "client.py")
        marked = client.split("def _mark_sent(")[1].split("\ndef ")[0]
        self.assertIn('doc.db_set("vop_pending", 0)', marked)


if __name__ == "__main__":
    unittest.main()
