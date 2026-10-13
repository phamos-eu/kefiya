# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The release must always have something to unlock.

What happened to Auftrag A, measured from the Error Log and the
stored state afterwards:

  1. The order reached the Sparkasse. The bank asked for the release in the
     banking app -- the push notification arrived, the user saw it.
  2. The first status query came back 9010. python-fints reports that as
     "Error during dialog initialization, could not fetch BPD. Please check
     that you passed the correct bank identifier" -- a sentence about a wrong
     URL, for a URL that was right.
  3. The attempt to park the challenge ran afterwards, on a dialog that was
     now broken, and threw. The exception came out of the send, the request
     failed, the transaction rolled back.
  4. Nothing was stored: no TAN blob, no dialog blob. The user then confirmed
     in the app and the release had nothing to unlock, because the challenge
     it belonged to existed nowhere.

Three rules come out of that, and each is asserted here:

  - Park BEFORE anything that can fail, so a failure is survivable.
  - Parking reports whether it worked, and a caller that could not park says
     so rather than carrying on as if it had.
  - The transfer path does not poll at all. python-fints documents the
     decoupled release as pause / store / resume in a LATER request, which is
     what resolve_tan_interaction does and what the box's OK button drives.
     Polling in the same request meant sending a status query on the dialog
     that had just sent the order, and that is what the bank refused.

Source-level: what broke was the ORDER of two calls, and no unit test with a
fake bank would have caught that -- the fake would have answered both ways
round.
"""

import os
import unittest


def _source(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return handle.read()


def _body(source, signature, docstring_quotes=2):
    """A method's code, past its docstring.

    The docstrings here quote the library lines they exist to correct, so a
    search of the whole method finds the very text it was written to remove.
    This suite has been fooled that way before.
    """
    method = source.split(signature)[1]
    return method.split('"""', docstring_quotes)[docstring_quotes]


def _text_der_meldung(quelltext):
    """Die aneinandergehaengten Stringliterale eines Codeausschnitts.

    Was der Nutzer liest, steht im Quelltext als Folge von Teilstrings mit
    Zeilenumbruechen dazwischen. Ein Vergleich gegen den Quelltext prueft
    die Formatierung mit; dieser hier prueft den Satz.
    """
    import re
    return "".join(re.findall(r'"((?:[^"\\\\]|\\\\.)*)"', quelltext))


#: Die TAN-Strecke wohnt seit ihrer Herausloesung in fints_tan_session.py.
#: Die Tests hier fragen nach dem Verhalten der Strecke, nicht danach, in
#: welcher Datei sie steht -- deshalb eine Konstante statt neun Literale.
TAN_STRECKE = ("utils", "fints_tan_session.py")


def _settle_tan():
    source = _source(*TAN_STRECKE)
    body = _body(source, "def _settle_tan(self, response):")
    return body.split("\n    def ")[0]


class TestParkingComesFirst(unittest.TestCase):

    def test_the_decoupled_branch_parks_before_it_prompts(self):
        body = _settle_tan()
        park = body.index("_park_tan_challenge(response)")
        prompt = body.index("_publish_tan_prompt(response, decoupled=True)")
        self.assertLess(park, prompt,
                        "the challenge must be on disk before anything else")

    def test_a_failed_park_stops_the_path(self):
        """Carrying on would send on a dialog nothing can recover -- which is
        how an order ends up at the bank while the app reports a failure."""
        body = _settle_tan()
        self.assertIn("if not self._park_tan_challenge(response):", body)
        guard = body.split("if not self._park_tan_challenge(response):")[1]
        self.assertIn("self._cannot_note_the_challenge()", guard[:200])

    def test_every_caller_answers_the_same_way(self):
        """Three call sites used to give three answers to one situation: one
        raised with its own message, one carried on and raised something
        generic, one ignored it. The last was ask_for_tan -- the path where a
        TAN is typed, so a failed park meant six digits that unlock nothing.
        """
        # Ueber BEIDE Dateien: zwei Aufrufer stehen in der TAN-Strecke, der
        # dritte im Abrufweg des Controllers. Die Zusicherung gilt dem
        # Aufrufer, nicht der Datei.
        source = _source(*TAN_STRECKE) + _source("utils", "fints_controller.py")
        pruefungen = source.count("if not self._park_tan_challenge(response):")
        antworten = source.count("self._cannot_note_the_challenge()")
        self.assertEqual(pruefungen, 3)
        self.assertEqual(antworten, 3, "jede Pruefung braucht ihre Antwort")
        self.assertNotIn("        self._park_tan_challenge(response)\n", source,
                         "ein Aufrufer prueft den Rueckgabewert nicht")

    def test_the_warning_names_the_real_risk(self):
        """The order may already be with the bank. Saying "failed" and
        nothing else is how a payment gets sent twice."""
        meldung = _body(_source(*TAN_STRECKE),
                        "def _cannot_note_the_challenge(self):")
        # Die Meldung ist im Quelltext ueber mehrere Zeilen umbrochen und in
        # Teilstrings zerlegt. Verglichen wird der Satz, den der Nutzer
        # liest, nicht die Formatierung, in der er im Code steht.
        satz = _text_der_meldung(meldung.split("\n    def ")[0])
        self.assertIn("an order may have reached the bank", satz)

    def test_the_transfer_path_does_not_poll(self):
        body = _settle_tan()
        self.assertNotIn("_await_release", body)

    def test_the_fetch_path_still_polls(self):
        """It saves a user from giving one release per account, and there it
        works -- the failure was on the transfer path only."""
        source = _source("utils", "fints_controller.py")
        fetch = source.split("def _get_transactions_checked(")[1]
        self.assertIn("self._await_release(response)", fetch)
        self.assertEqual(source.count("self._await_release(response)"), 1)


class TestParkingReportsItself(unittest.TestCase):

    def test_it_answers_whether_it_worked(self):
        source = _source(*TAN_STRECKE)
        body = _body(source, "def _park_tan_challenge(self, response):")
        body = body.split("\n    def ")[0]
        self.assertIn("return False", body)
        self.assertIn("return True", body)

    def test_writing_it_down_cannot_take_the_request_down(self):
        """The one thing this method exists to guarantee was the thing that
        got lost, because persisting sat outside the guard."""
        source = _source(*TAN_STRECKE)
        body = _body(source, "def _park_tan_challenge(self, response):")
        body = body.split("\n    def ")[0]
        persisted = body.index("self._persist_fints_state(response)")
        guarded = body.index("try:")
        self.assertLess(guarded, persisted,
                        "persisting must sit inside the try, not before it")
        self.assertIn("frappe.db.commit()",
                      body[persisted:body.index("except Exception:")])


class TestTheBankGetsQuoted(unittest.TestCase):
    """python-fints replaces the bank's words with its own sentence, and that
    sentence is regularly about something else."""

    def test_the_client_remembers_what_it_heard(self):
        source = _source("utils", "fints_vop_client.py")
        body = _body(source, "def _remember_what_the_bank_said(self, message):")
        self.assertIn("fints_response.lines_in_message(message)", body)
        self.assertIn("self.last_said = (self.last_said + (said,))[-40:]",
                      body)

    def test_it_is_written_down_per_message_not_per_line(self):
        """The whole point: the library raises on the first 9010 it meets,
        and it meets the message-level lines first. A note-taker that is
        called once per line therefore never sees the segment-level ones --
        which are the lines that say what was actually wrong."""
        source = _source("utils", "fints_vop_client.py")
        self.assertIn("def process_response_message(self, dialog, message,",
                      source)
        self.assertNotIn("def _process_response(self, dialog, segment,",
                         source)
        body = _body(source,
                     "def process_response_message(self, dialog, message,")
        self.assertLess(body.index("self._remember_what_the_bank_said(message)"),
                        body.index("return super().process_response_message("))

    def test_it_changes_nothing(self):
        """A note-taker that alters the flow is not a note-taker."""
        source = _source("utils", "fints_vop_client.py")
        body = _body(source,
                     "def process_response_message(self, dialog, message,")
        self.assertIn(
            "dialog, message, internal_send=internal_send)", body)

    def test_the_piece_check_covers_it(self):
        """Or the override lands on a library that has moved."""
        source = _source("utils", "fints_vop_client.py")
        needed = source.split("needed = (")[1].split(")")[0]
        self.assertIn('"process_response_message"', needed)

    def test_what_went_out_is_written_down_too(self):
        """A complaint about a message is unreadable without the message."""
        source = _source("utils", "fints_vop_client.py")
        body = _body(source, "def send_tan(self, challenge, tan):")
        self.assertLess(
            body.index("self._remember_what_we_sent(vop_seg + [tan_seg])"),
            body.index("response = dialog.send(*(vop_seg + [tan_seg]))"))

    def test_only_the_segment_types_are_written_down(self):
        """The message it describes carries a TAN."""
        source = _source("utils", "fints_vop_client.py")
        body = _body(source, "def _remember_what_we_sent(self, segments):")
        self.assertIn("type(segment).__name__", body)

    def test_the_failure_log_quotes_what_went_out(self):
        source = _source("utils", "fints_controller.py")
        body = _body(source, "def __note_the_failed_release(self):")
        self.assertIn("fints_vop_client.what_was_sent(self.fints_connection)",
                      body)
        self.assertIn("What was sent:", body)

    def test_the_failure_log_quotes_it(self):
        source = _source(*TAN_STRECKE)
        body = _body(source, "def _await_release(self, challenge):")
        self.assertIn("fints_vop_client.what_the_bank_said(self.fints_connection)",
                      body)


class TestWhatTheBankSaid(unittest.TestCase):

    def test_it_reads_the_record(self):
        from kefiya.utils.fints_vop_client import what_the_bank_said

        class Connection:
            last_said = (("3040", "Es liegen weitere Informationen vor."),
                         ("9010", "Verarbeitung nicht moeglich."))

        said = what_the_bank_said(Connection())
        self.assertIn("9010", said)
        self.assertIn("Verarbeitung nicht moeglich.", said)

    def test_an_empty_record_says_so_rather_than_lying(self):
        from kefiya.utils.fints_vop_client import what_the_bank_said

        class Connection:
            last_said = ()

        self.assertIn("nothing recorded", what_the_bank_said(Connection()))
        self.assertIn("nothing recorded", what_the_bank_said(object()))

    def test_it_names_the_segment_where_the_bank_named_one(self):
        """Two lines of one message can carry the same code about different
        segments, and that difference is the whole diagnosis."""
        from kefiya.utils.fints_vop_client import what_the_bank_said

        class Connection:
            last_said = (("9050", "Die Nachricht enthaelt Fehler.", ""),
                         ("9210", "Ungueltige Auftragsdaten.", "2"))

        said = what_the_bank_said(Connection())
        self.assertIn("[Segment 2]", said)
        # The message-level line names none, and must not grow one.
        self.assertNotIn("Fehler. [Segment", said)

    def test_it_never_raises(self):
        from kefiya.utils.fints_vop_client import what_the_bank_said

        class Connection:
            last_said = "not a sequence of pairs"

        self.assertIsInstance(what_the_bank_said(Connection()), str)


class TestWhatWasSent(unittest.TestCase):

    def test_it_names_the_segments(self):
        from kefiya.utils.fints_vop_client import what_was_sent

        class Connection:
            last_sent = ("HKVPA1", "HKTAN7")

        self.assertEqual("HKVPA1 + HKTAN7", what_was_sent(Connection()))

    def test_an_empty_record_says_so_rather_than_lying(self):
        from kefiya.utils.fints_vop_client import what_was_sent

        class Connection:
            last_sent = ()

        self.assertIn("nothing recorded", what_was_sent(Connection()))
        self.assertIn("nothing recorded", what_was_sent(object()))


if __name__ == "__main__":
    unittest.main()
