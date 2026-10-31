# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Two ways an order was reported as sent without ever reaching the bank.

Both were found on the same transfer. 58,80 EUR went out, no TAN was asked
for, nothing was debited, and the outbox said "Sent" -- for the second time in
this project, with a guard already written against exactly that.

The guard could not work. required_signatures() reads the number the bank
states per account, and stored_rows() never selected the column: the row came
back with the field unset, cint(None) turned it into 0, and 0 reads as "no
signature required". That is the one reading that turns a missing TAN into a
successful payment, and it applied to every account on the instance -- 49
stored transfer capabilities there demand a signature.

The second is why no TAN was asked for in the first place. The message carried
ReqdExctnDt = 16.08.2026 on the 25th, because build_pain001_for() sent
doc.execution_date whatever it meant. For an order the bank holds, the date IS
the order. For one WE hold, the date is our bookkeeping: the order sits in the
outbox and, on the day it is sent, is an ordinary immediate transfer. Sending
it as a requested execution date puts a past day into the message.

Read from source: frappe is not installed where these run, and what has to be
true is about the code.
"""

import os
import unittest


def _source(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return handle.read()


class TestTheSignatureNumberIsActuallyRead(unittest.TestCase):

    @staticmethod
    def _rows():
        return _source("utils", "account_capabilities.py").split(
            "def stored_rows(")[1].split("\ndef ")[0]

    def test_the_column_the_guard_depends_on_is_selected(self):
        self.assertIn('"required_signatures"', self._rows())

    def test_every_field_the_module_reads_is_fetched(self):
        """A field the reader uses and the query does not select comes back
        unset, and an unset number is a quiet zero."""
        body = self._rows()
        for field in ("capability", "transaction", "allowed",
                      "required_signatures"):
            self.assertIn('"' + field + '"', body, field)

    def test_the_whole_list_is_fetched(self):
        """One account here has 51 capability rows, and the transfer sits at
        index 29. A default page length would decide by position."""
        self.assertIn("limit_page_length=0", self._rows())

    def test_an_unknown_answer_still_blocks_nothing(self):
        """Silence is not permission and not refusal: an account nobody has
        fetched must send exactly as it did before this module existed."""
        body = _source("utils", "account_capabilities.py").split(
            "def required_signatures(")[1].split("\ndef ")[0]
        self.assertIn("return None", body)

    def test_the_guard_refuses_rather_than_records(self):
        body = _source("utils", "fints_controller.py").split(
            "def _refuse_unsigned(")[1].split("\n    def ")[0]
        self.assertIn("frappe.throw", body)
        self.assertIn("not needed", body)


class TestTheBankIsAskedBeforeAnythingIsConcluded(unittest.TestCase):
    """A refusal explains the missing TAN: an order the bank turned down is
    not an order it wants signed.

    The question moved into _refuse_a_refused_order, because it was written
    out here and asked NOWHERE else -- the payee release and the direct debit
    both logged that the bank had said something and reported success anyway.
    """

    @staticmethod
    def _send():
        body = _source("utils", "fints_controller.py")
        return body.split("def submit_sepa_transfer(")[1].split(
            "\n    def ")[0]

    @staticmethod
    def _asking():
        body = _source("utils", "fints_controller.py")
        return body.split("def _refuse_a_refused_order(")[1].split(
            "\n    def ")[0]

    def test_the_response_is_read_at_all(self):
        self.assertIn("fints_response.verdict_of(response)", self._asking())
        self.assertIn("self._refuse_a_refused_order(response)", self._send())

    def test_a_refusal_stops_the_send_with_the_banks_words(self):
        body = self._asking()
        self.assertIn("fints_response.refused(verdict)", body)
        refusal = body.split("fints_response.refused(verdict)")[1]
        self.assertIn("frappe.throw", refusal)
        self.assertIn("fints_response.as_text(verdict)", refusal)

    def test_it_is_asked_before_the_unsigned_guard(self):
        """Otherwise a refused order is reported as a missing signature,
        which sends the reader to the wrong bank department."""
        body = self._send()
        self.assertLess(body.index("self._refuse_a_refused_order(response)"),
                        body.index("self._refuse_unsigned("))

    def test_the_signature_refusal_carries_what_the_bank_said(self):
        body = _source("utils", "fints_controller.py").split(
            "def _refuse_unsigned(")[1].split("\n    def ")[0]
        self.assertIn("fints_response.as_text(verdict)", body)

    def test_the_silent_success_is_logged_with_it_too(self):
        body = self._send()
        log = body.split("completed without TAN challenge")[1]
        self.assertIn("fints_response.as_text(verdict)", log)


class TestTheStoredFintsStateFitsInItsColumn(unittest.TestCase):
    """A TAN challenge that cannot be persisted is a payment that cannot be
    completed.

    "Data too long for column 'stored_dialog_state'": the bank DID ask for a
    TAN on the Sparkasse account, and the send died storing the dialog. Small
    Text is a MariaDB TEXT -- 65,535 bytes -- and a dialog state carrying the
    pain.001 message plus the VoP segments goes past it. The comdirect access
    was already at 36,600 of those bytes.
    """

    def test_every_stored_fints_blob_is_long_text(self):
        import json

        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        with open(os.path.join(root, "kefiya", "doctype", "kefiya_login",
                               "kefiya_login.json"), encoding="utf-8") as fh:
            meta = json.load(fh)

        too_small = [f["fieldname"] for f in meta["fields"]
                     if f.get("fieldname", "").startswith("stored_")
                     and f.get("fieldtype") in ("Data", "Small Text", "Text")]
        self.assertEqual(too_small, [],
                         "These hold FinTS state and will overflow: "
                         + ", ".join(too_small))


class TestNoEmptySecondDialog(unittest.TestCase):
    """frappe.throw renders its own dialog. A second box underneath saying
    "Unknown error." is worse than no second box -- the reader is left
    wondering which of the two is the real answer."""

    def test_the_page_says_nothing_when_it_knows_nothing(self):
        body = _source("public", "js", "controllers", "payment_outbox.js")
        rule = body.split("function reportFailure(")[1].split("\n\t}")[0]
        self.assertIn("if (!text) return;", rule)

    def test_the_dialogs_all_go_through_it(self):
        body = _source("public", "js", "controllers", "payment_outbox.js")
        for title in ('__("Not sent")', '__("Not approved")',
                      '__("Not changed")'):
            self.assertIn("reportFailure(" + title, body, title)

    def test_the_inline_load_error_still_says_something(self):
        """That one is not a second dialog -- it replaces the page, so a bare
        heading with no reason under it would be worse."""
        body = _source("public", "js", "controllers", "payment_outbox.js")
        self.assertIn('errText(r) || __("Unknown error.")', body)


class TestOnlyTheBanksOwnDateGoesIntoTheMessage(unittest.TestCase):
    """The date means opposite things depending on who holds the order."""

    @staticmethod
    def _rule():
        return _source("kefiya", "doctype", "kefiya_transfer",
                       "kefiya_transfer.py").split(
            "def requested_execution_date(")[1].split("\ndef ")[0]

    def test_an_order_we_hold_carries_no_date_to_the_bank(self):
        """It is sent ON the day, and that moment is an ordinary immediate
        transfer -- the date was our bookkeeping.

        "No date" is not today's date: the German banks write it as the fixed
        value 1999-01-01, and the Volksbank checks for exactly that. See
        pain_dk and test_pain_dk.
        """
        body = self._rule()
        self.assertIn('cint(getattr(doc, "manage_due_date", 0))', body)
        self.assertIn("pain_dk.execution_date(today, bank_holds_it=False)",
                      body)
        self.assertNotIn("return today", body)

    def test_a_past_date_is_refused_and_not_moved_forward(self):
        """Silently turning "execute on the 16th" into "execute today" is a
        different payment than the one somebody approved."""
        body = self._rule()
        self.assertIn("wanted < today", body)
        self.assertIn("frappe.throw", body)

    def test_the_builder_asks_the_rule_rather_than_the_field(self):
        body = _source("kefiya", "doctype", "kefiya_transfer",
                       "kefiya_transfer.py").split("def build_pain001_for(")[1]
        self.assertIn('"execution_date": requested_execution_date(doc)', body)
        self.assertNotIn("doc.execution_date or now_datetime().date()", body)
