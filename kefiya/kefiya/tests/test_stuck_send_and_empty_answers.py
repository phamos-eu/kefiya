# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Three things found on 18.11.2026.

* Pressing Send again while the bank waited for a release in its app gave
  "Transfer failed: A transfer for this document is already in progress."
* Every fetch of one bank came back empty from 16.11. on, and nothing said why.
* A booking whose counter account the bank names by number, not IBAN, was
  rejected by ERPNext and lost on every fetch.
"""

import os
import unittest


def _read(*parts):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _body(source, name):
    return source.split("def " + name + "(")[1].split("\ndef ")[0]


class TestASendStillUnderWay(unittest.TestCase):

    def setUp(self):
        self.client = _read("utils", "client.py")

    def test_both_send_paths_explain_instead_of_failing(self):
        single = _body(self.client, "submit_kefiya_transfer")
        self.assertIn("return _already_on_its_way(doc.kefiya_login,"
                      " transfer_name)", single)
        batch = _body(self.client, "send_transfer_outbox")
        self.assertIn("_already_on_its_way(kefiya_login,", batch)
        # The old refusal as code -- the docstring may still quote it.
        self.assertNotIn('_(\n            "A transfer for this document is'
                         ' already in progress."', self.client)

    def test_an_open_release_is_waited_for_again(self):
        body = _body(self.client, "_already_on_its_way")
        self.assertIn('"status": "tan_required", "decoupled": True', body)
        self.assertIn('"still_waiting": True', body)
        self.assertIn("stored_tan_state_decoupled", body)

    def test_the_pages_say_it(self):
        for path in (("public", "js", "controllers", "fints_transfer_flow.js"),
                     ("public", "js", "controllers", "payment_outbox.js")):
            self.assertIn("still_waiting", _read(*path))


class TestTheReleaseNoteIsNotRepeatedWhileWaiting(unittest.TestCase):

    def test_once_per_quarter_hour(self):
        body = _read("utils", "fints_tan_session.py").split(
            "def _note_release_request(")[1].split("\n    def ")[0]
        self.assertIn("ex=900, nx=True", body)
        self.assertIn("if attended and fresh:", body)


class TestAnEmptyAnswerIsWrittenDown(unittest.TestCase):

    def test_what_the_bank_said_goes_to_the_timeline(self):
        controller = _read("utils", "fints_controller.py")
        body = controller.split("def _note_empty_answer(")[1].split(
            "\n    def ")[0]
        self.assertIn("last_said", body)
        self.assertIn("note_on_login(", body)
        self.assertIn("ex=86400", body)
        self.assertIn("self._note_empty_answer(curr_doc.from_date,"
                      " curr_doc.to_date)", controller)


class TestANumberInTheIbanField(unittest.TestCase):

    def setUp(self):
        source = _read("utils", "import_bank_transaction.py")
        start = source.index("def iban_or_number(")
        end = source.index("\nclass ", start)
        namespace = {}
        exec("import re\n" + source[start:end], namespace)
        self.split = namespace["iban_or_number"]

    def test_a_valid_iban_stays_an_iban(self):
        self.assertEqual(self.split("DE89 9999 9999 0001 1111 11"),
                         ("DE89999999990001111111", None))

    def test_an_account_number_becomes_a_number(self):
        self.assertEqual(self.split("1234567890"), (None, "1234567890"))

    def test_a_wrong_checksum_is_not_an_iban(self):
        self.assertEqual(self.split("DE98370400440532013000"),
                         (None, "DE98370400440532013000"))

    def test_nothing_is_nothing(self):
        self.assertEqual(self.split(None), (None, None))

    def test_both_import_paths_use_it(self):
        source = _read("utils", "import_bank_transaction.py")
        self.assertEqual(source.count(
            "applicant_iban, kontonummer = iban_or_number(applicant_iban)"), 2)


class TestTheWaitIsVisible(unittest.TestCase):
    """18.11.2026: the bank's app asked for the release while the page
    showed nothing -- an eight-second alert, gone, and on a phone not even
    that."""

    def setUp(self):
        self.js = _read("public", "js", "controllers", "tan_prompt.js")
        self.body = self.js.split("function awaitRelease(opts) {")[1].split(
            "kefiya.await_release = awaitRelease;")[0]

    def test_a_box_stays_while_the_page_waits(self):
        self.assertIn("waitingBox(login, names, ceiling,", self.body)
        self.assertIn("waitBox.close()", self.body)

    def test_not_a_second_box_where_one_is_open(self):
        self.assertIn("(openBoxes[scope] || openBoxes[login]) ? null",
                      self.body)

    def test_the_box_says_what_and_how_long(self):
        box = self.js.split("function waitingBox(")[1]
        self.assertIn("The bank is waiting for your release in its app.",
                      box)
        self.assertIn("This page keeps asking for {0} more seconds.", box)
