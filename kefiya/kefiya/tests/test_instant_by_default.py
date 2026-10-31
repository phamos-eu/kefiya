# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""An instant payment by default -- including on a dated transfer.

The two used to exclude each other outright:

    if getdate(self.execution_date) > today and cint(self.instant_payment):
        throw("An instant payment ... cannot carry a future execution date.")

That is right for one of the two dated transfers and wrong for the other, and
the difference is who holds the order:

  the BANK holds it   the order goes out NOW carrying a future execution date.
                      No bank offers an instant payment that way -- a genuine
                      contradiction.
  WE hold it          the order waits in the outbox and is sent ON the day. At
                      that moment it is an ordinary immediate transfer, which
                      may perfectly well be an instant one. No contradiction at
                      all -- and this is what the blanket refusal also caught.
"""

import os
import unittest


def _read(*parts):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


DOC = ("kefiya", "doctype", "kefiya_transfer", "kefiya_transfer.py")
FORM = ("kefiya", "doctype", "kefiya_transfer", "kefiya_transfer.js")
DIALOG = ("public", "js", "controllers", "transfer_form.js")


class TestInstantIsTheDefault(unittest.TestCase):

    def test_the_document_says_so(self):
        import json
        meta = json.loads(_read("kefiya", "doctype", "kefiya_transfer",
                                "kefiya_transfer.json"))
        field = [f for f in meta["fields"]
                 if f["fieldname"] == "instant_payment"][0]
        self.assertEqual(field.get("default"), "1")

    def test_the_outbox_dialog_ticks_it_for_a_new_order(self):
        dialog = _read(*DIALOG)
        self.assertIn("default: existing ? (existing.instant_payment ? 1 : 0) : 1",
                      dialog)

    def test_an_existing_order_keeps_what_it_says(self):
        """Correcting an order must not silently make it faster or slower."""
        dialog = _read(*DIALOG)
        self.assertIn("existing.instant_payment ? 1 : 0", dialog)


class TestOnlyTheBankHeldDateExcludesIt(unittest.TestCase):

    def test_the_refusal_now_asks_who_holds_the_order(self):
        body = _read(*DOC).split("def validate(")[1].split("\n    def ")[0]
        self.assertIn(
            'if cint(self.instant_payment) and not cint(self.manage_due_date):',
            body,
            "Without manage_due_date in the condition, an order we hold "
            "ourselves is refused for a contradiction it does not have.")

    def test_the_message_names_the_way_out(self):
        """An error that only says no leaves the reader to guess."""
        body = _read(*DOC).split("def validate(")[1].split("\n    def ")[0]
        self.assertIn("kept here", body)
        self.assertIn("on the day", body)


class TestTheDefaultCannotBecomeATrap(unittest.TestCase):
    """On by default means every order on an account whose bank has no instant
    payments would be refused at send -- on every order, until somebody works
    out what the default did."""

    def test_an_explicit_refusal_turns_it_off(self):
        body = _read(*DOC).split(
            "def drop_instant_where_the_bank_refuses_it(")[1].split(
            "\n    def ")[0]
        self.assertIn("capabilities.refuses(bank_account, wanted)", body)
        self.assertIn("self.instant_payment = 0", body)

    def test_silence_is_not_a_refusal(self):
        """An account nobody has fetched knows nothing about itself; treating
        that as a refusal takes instant payments from every new account."""
        body = _read(*DOC).split(
            "def drop_instant_where_the_bank_refuses_it(")[1].split(
            "\n    def ")[0]
        self.assertIn("refuses(", body)
        self.assertNotIn("allows(", body)

    def test_turning_it_off_is_said_out_loud(self):
        body = _read(*DOC).split(
            "def drop_instant_where_the_bank_refuses_it(")[1].split(
            "\n    def ")[0]
        self.assertIn("frappe.msgprint(", body)

    def test_the_collective_case_is_asked_about_separately(self):
        """Several payments make it HKIPM, which a bank rules on separately
        from HKIPZ."""
        body = _read(*DOC).split(
            "def drop_instant_where_the_bank_refuses_it(")[1].split(
            "\n    def ")[0]
        self.assertIn("payment_count=len(self.items)", body)


class TestTheFormsSayItBeforeTheSave(unittest.TestCase):
    """Instant is on by default, so the incompatible choice would otherwise
    greet the user with a validation error at save."""

    def test_the_document_form_switches_it_off_when_the_bank_holds_the_date(self):
        form = _read(*FORM)
        self.assertIn("function kefiya_reconcile_instant_with_the_date(frm)",
                      form)
        self.assertIn("!frm.doc.execution_date || frm.doc.manage_due_date",
                      form)

    def test_it_reacts_to_both_fields_that_can_cause_it(self):
        form = _read(*FORM)
        for handler in ("manage_due_date: function (frm) {",
                        "execution_date: function (frm) {"):
            block = form.split(handler)[1][:200]
            self.assertIn("kefiya_reconcile_instant_with_the_date(frm)", block)

    def test_a_past_or_todays_date_changes_nothing(self):
        """Only a FUTURE date can be handed to the bank at all."""
        body = _read(*FORM).split(
            "function kefiya_reconcile_instant_with_the_date(frm) {")[1][:700]
        self.assertIn("frappe.datetime.get_diff", body)

    def test_switching_it_off_is_never_silent(self):
        """Somebody would otherwise wonder later why the payment took a day."""
        body = _read(*FORM).split(
            "function kefiya_reconcile_instant_with_the_date(frm) {")[1][:900]
        self.assertIn("frappe.show_alert", body)

    def test_the_dialog_does_the_same_at_the_moment_of_choosing(self):
        dialog = _read(*DIALOG)
        self.assertIn('const bank_holds_it = mode.value === "bank";', dialog)
        self.assertIn('dialog.set_value("instant_payment", 0)', dialog)
