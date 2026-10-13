# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The outgoing-payments page reads from the app, not from one site.

Two things were wrong with the Server Script this replaces, and only one of
them was visible: it wrote the reasons an order is not going out as German
literals typed without umlauts -- "Zurueckgestellt", "Faellig erst am",
"Empfaengerpruefung offen" -- which is what somebody finally reported. The
other was that the page's data source lived outside the app at all, so the
page worked on that one site, and nothing here could see the dependency.

These tests read the module rather than run it: a Frappe database is not
available offline, and what has to be true is about the source anyway -- that
no German is baked into it, and that it asks the app the questions the script
answered by hand.
"""

import os
import re
import unittest


def _app_path(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(root, *parts)


def _source(*parts):
    with open(_app_path(*parts), encoding="utf-8") as handle:
        return handle.read()


class TestNothingIsWrittenInGerman(unittest.TestCase):
    """The whole reason this moved. A German literal in the source cannot be
    corrected by a translator, is not read by the translation test, and on the
    site it came from had lost its umlauts."""

    #: Words that only appear in German, spelled the way a script editor with
    #: no umlaut key produces them.
    UMLAUT_FREE_GERMAN = ("Zurueck", "Faellig", "Empfaenger", "ueberweis",
                          "Ueberweis", "abgesendet", "freigegeben")

    def test_no_user_facing_string_is_german(self):
        body = _source("utils", "outbox.py")
        # Only the strings, not the prose around them: the docstring names the
        # German literals it replaced, which is the point of the docstring.
        literals = re.findall(r'_\(\s*"([^"]*)"', body)
        for text in literals:
            for german in self.UMLAUT_FREE_GERMAN:
                self.assertNotIn(german, text, text)

    def test_every_reason_is_a_translatable_string(self):
        body = _source("utils", "outbox.py").split(
            "def _blocked_reason(")[1].split("\ndef ")[0]
        returns = re.findall(r"return\s+(.+)", body)
        for line in returns:
            if line.strip() == '""':
                continue
            self.assertTrue(line.startswith("_("),
                            "Not translatable: " + line)


class TestTheAnswersComeFromTheApp(unittest.TestCase):
    """The script reimplemented two things the app already knows, and both
    had drifted: which accounts may pay, and who may do what."""

    def test_the_payer_list_is_the_apps_own(self):
        body = _source("utils", "outbox.py")
        self.assertIn("transfer_sources()", body)

    def test_permissions_are_asked_of_the_framework(self):
        """A Server Script cannot call has_permission, so the script read
        DocPerm rows by hand. Here there is no reason to."""
        body = _source("utils", "outbox.py")
        self.assertIn("frappe.has_permission", body)
        self.assertNotIn("Custom DocPerm", body)

    def test_the_list_itself_is_read_with_permissions(self):
        """An order somebody may not see must not turn up inside a total at
        the bottom of the page."""
        body = _source("utils", "outbox.py")
        self.assertIn('frappe.get_list(\n        "Kefiya Transfer"', body)


class TestTheReceiptsAreCounted(unittest.TestCase):
    """"Where is the receipt" is the question the page could not answer.

    It is not a question about this document: the travel expense PDF is
    created on the Business Trip and stays there, and the transfer only says
    "Reisekosten BT-0001" in its purpose.
    """

    def test_the_purpose_is_read_for_document_names(self):
        body = _source("utils", "outbox.py")
        self.assertIn("DOCNAME_IN_TEXT", body)

    @staticmethod
    def _pattern():
        """Read out of the source rather than imported: frappe is not
        installed where these run, and the pattern is a literal."""
        body = _source("utils", "outbox.py")
        return body.split('DOCNAME_IN_TEXT = r"')[1].split('"')[0]

    def test_the_pattern_matches_a_business_trip_and_a_transfer(self):
        pattern = self._pattern()
        for name in ("BT-0001", "KEF-TRF-2026-00001", "RE-260427-034"):
            self.assertTrue(re.search(pattern, "Reisekosten " + name), name)

    def test_the_pattern_does_not_match_a_bare_number(self):
        """A looser pattern matches the recipient's own invoice numbers and
        goes looking for documents that are not ours."""
        self.assertIsNone(re.search(self._pattern(), "Rechnung 4711"))

    def test_it_is_the_same_pattern_the_detail_view_uses(self):
        """Two patterns would mean the list counts receipts the dialog does
        not show, or the other way round."""
        js = _source("public", "js", "controllers", "transfer_details.js")
        in_js = js.split("kefiya.DOCNAME_IN_TEXT = /")[1].split("/g")[0]
        self.assertEqual(self._pattern(), in_js)

    def test_the_count_is_one_query_for_the_whole_page(self):
        """Asking per row would be a request per order to show one number."""
        body = _source("utils", "outbox.py").split(
            "def _receipt_counts(")[1].split("\ndef ")[0]
        self.assertEqual(body.count("frappe.get_all"), 1)

    def test_only_receipts_the_reader_may_see_are_counted(self):
        """Otherwise the column says "3" and the dialog -- which reads the
        same files through File's permissions -- opens empty."""
        body = _source("utils", "outbox.py").split(
            "def _receipt_counts(")[1].split("\ndef ")[0]
        self.assertIn("frappe.has_permission", body)

    def test_the_doctype_is_checked_and_not_only_the_name(self):
        """"BT-0001" is a name, not an address. A File hanging on some other
        doctype whose document happens to be called that is not a receipt for
        this payment."""
        body = _source("utils", "outbox.py").split(
            "def _receipt_counts(")[1].split("\ndef ")[0]
        self.assertIn("attached_to_doctype", body)

    def test_the_purpose_lines_are_read_once_per_page(self):
        """They were read twice -- once for the count, once per row -- which
        is a regex pass per item nobody asked for."""
        body = _source("utils", "outbox.py").split("def outbox_data(")[1]
        self.assertEqual(body.count("_referenced_documents("), 1)

    def test_the_page_shows_the_count(self):
        body = _source("public", "js", "controllers", "payment_outbox.js")
        self.assertIn('key: "receipt"', body)
        self.assertIn("receiptCell", body)


class TestSendingApproves(unittest.TestCase):
    """Two buttons for one intention was a step people took by rote, and a
    step taken by rote is not an approval -- it is a click."""

    def test_a_draft_is_offered_to_the_send_button(self):
        body = _source("public", "js", "controllers", "payment_outbox.js")
        self.assertIn("outbox_only_lacks_approval", body)

    def test_the_browser_does_not_decide_it_for_itself(self):
        """It did, and that made three copies of "is this order due yet":
        outbox.py, client.py and here. The browser copy chose which drafts the
        send button offered; the server copy refused the send afterwards."""
        body = _source("public", "js", "controllers", "payment_outbox.js")
        rule = body.split("kefiya.outbox_only_lacks_approval = function")[1] \
            .split("\n};")[0]
        self.assertIn("sendable_if_approved", rule)
        for own_opinion in ("on_hold", "execution_date", "docstatus"):
            self.assertNotIn(own_opinion, rule, own_opinion)

    def test_the_server_answers_it_with_the_same_function(self):
        """Asked with the approval assumed -- so the rule that offers an order
        and the rule that blocks it cannot drift apart."""
        body = _source("utils", "outbox.py")
        self.assertIn("as_if_approved", body)
        self.assertIn('"sendable_if_approved"', body)
        held = body.split("def _blocked_reason(")[1].split("\ndef ")[0]
        self.assertIn("as_if_approved", held)

    def test_the_button_says_it_approves(self):
        body = _source("public", "js", "controllers", "payment_outbox.js")
        self.assertIn('__("Approve and send")', body)

    def test_the_confirmation_says_it_approves(self):
        body = _source("public", "js", "controllers", "payment_outbox.js")
        self.assertIn("are approved as they", body)

    def test_the_approval_happens_after_every_refusal(self):
        """The one that matters. Approving is irreversible; send_transfer_outbox
        has nine ways to refuse a batch, and every one of them used to fire
        after the browser had already locked the drafts."""
        body = _source("utils", "client.py").split(
            "def send_transfer_outbox(")[1].split("\n@frappe.whitelist()")[0]
        approval = body.index("doc.submit()")
        for refusal in ("_refuse_unsupported", "_refuse_over_limit",
                        "len(scheduling) > 1", "is held back",
                        "_not_due_yet"):
            self.assertLess(body.index(refusal), approval,
                            refusal + " must be checked before approving.")

    def test_the_browser_does_not_approve_first(self):
        body = _source("public", "js", "controllers", "payment_outbox.js")
        self.assertNotIn("approveThenSend", body)
        send = body.split("\n\tfunction send() {")[1] \
            .split("\n\tfunction handToBank(")[0]
        self.assertNotIn("approve_transfers", send)
        self.assertIn("approve_drafts", _source(
            "public", "js", "controllers", "payment_outbox.js"))

    def test_only_what_was_really_approved_is_sent(self):
        """A name the approval refused carries no end-to-end identifier and
        would take the whole collective order down with it at the bank."""
        body = _source("utils", "client.py").split(
            "def send_transfer_outbox(")[1].split("\n@frappe.whitelist()")[0]
        self.assertIn("docs = [doc for doc in docs if doc.name not in gone]",
                      body)

    def test_an_approval_that_went_through_is_reported_either_way(self):
        """A draft locked on the way to a send that then failed must be named,
        or it goes missing between two states."""
        body = _source("utils", "client.py").split(
            "def send_transfer_outbox(")[1].split("\n@frappe.whitelist()")[0]
        self.assertIn('result["approved"] = approved', body)
        page = _source("public", "js", "controllers", "payment_outbox.js")
        hand = page.split("function handToBank(")[1].split("\n\t}")[0]
        self.assertIn("m.refused", hand)


class TestTheReceiptsCanBeSeen(unittest.TestCase):
    """A filename is not a receipt. Checking that the invoice in front of you
    is the one being paid means looking at it, and a link that opens a new tab
    is a step people skip."""

    def test_the_detail_view_shows_the_file(self):
        body = _source("public", "js", "controllers", "transfer_details.js")
        self.assertIn("kefiya.receipt_preview", body)

    def test_a_photograph_and_a_pdf_are_both_shown(self):
        body = _source("public", "js", "controllers", "transfer_details.js")
        rule = body.split("kefiya.receipt_preview = function")[1] \
            .split("\n};")[0]
        self.assertIn("<img", rule)
        self.assertIn("<embed", rule)

    def test_anything_else_keeps_its_link(self):
        """Guessing at a viewer for a file type nobody sends is how a dialog
        ends up broken for the one person who does send it."""
        body = _source("public", "js", "controllers", "transfer_details.js")
        rule = body.split("kefiya.receipt_preview = function")[1] \
            .split("\n};")[0]
        self.assertIn("cannot be shown here", rule)

    def test_the_documents_behind_the_order_are_reachable(self):
        """Where the amounts come from -- the travel expense, the invoice.
        An order that shows only its own fields makes them unreachable."""
        body = _source("public", "js", "controllers", "transfer_details.js")
        self.assertIn('__("Connected documents")', body)
