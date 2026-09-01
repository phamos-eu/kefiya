# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Reading what the bank said, instead of guessing from what it did not say.

The send path asked a response two questions -- "VoP mismatch?" and "TAN
wanted?" -- and treated everything else as success. Its own comment admitted
the gap:

    The dialog ended without a TAN. That is either a bank that asks for none,
    or an order that was never signed -- and those two look identical from
    here.

They never looked identical. Every FinTS response carries HIRMS lines whose
return code says outright what happened: 0xxx accepted, 3xxx accepted with a
remark, 9xxx refused. python-fints collects them in TransactionResponse.status
and .responses, and both were ignored -- so a bank refusing an order in plain
German produced "the bank did not request a TAN" in the log and "Unbekannter
Fehler" on the screen.

Pure functions over objects that only need .code, .text and .status, so the
rule can be exercised without a bank.
"""

import unittest

from kefiya.utils import fints_response as fr


class Line:
    def __init__(self, code, text, parameters=None):
        self.code = code
        self.text = text
        self.parameters = parameters


class Status:
    def __init__(self, name):
        self.name = name


class Answer:
    def __init__(self, lines=None, status=None):
        if lines is not None:
            self.responses = lines
        if status is not None:
            self.status = Status(status)


class TestTheCodeSaysWhatHappened(unittest.TestCase):

    def test_an_accepted_order(self):
        v = fr.verdict_of(Answer([Line("0010", "Nachricht entgegengenommen")]))
        self.assertEqual(v["status"], fr.SUCCESS)
        self.assertFalse(fr.refused(v))

    def test_a_refused_order(self):
        v = fr.verdict_of(Answer([Line("9010", "Verarbeitung nicht möglich")]))
        self.assertEqual(v["status"], fr.ERROR)
        self.assertTrue(fr.refused(v))

    def test_a_remark_is_not_a_refusal(self):
        """"Verwendungszweck gekürzt" is the commonest thing a German bank
        says about a transfer. Refusing on it would block half the payments in
        the country."""
        v = fr.verdict_of(Answer([Line("0010", "entgegengenommen"),
                                  Line("3050", "Verwendungszweck gekürzt")]))
        self.assertEqual(v["status"], fr.WARNING)
        self.assertFalse(fr.refused(v))

    def test_the_worst_line_decides(self):
        v = fr.verdict_of(Answer([Line("0010", "ok"), Line("3050", "gekürzt"),
                                  Line("9210", "Ungültige Auftragsdaten")]))
        self.assertEqual(v["status"], fr.ERROR)

    def test_the_librarys_own_verdict_can_only_make_it_stricter(self):
        """The lines are the source; response.status is a second opinion."""
        v = fr.verdict_of(Answer([Line("0010", "ok")], status="ERROR"))
        self.assertEqual(v["status"], fr.ERROR)

    def test_it_never_makes_it_milder(self):
        v = fr.verdict_of(Answer([Line("9010", "abgelehnt")], status="SUCCESS"))
        self.assertEqual(v["status"], fr.ERROR)


class TestAnAnswerItCannotReadBlocksNothing(unittest.TestCase):
    """A NeedTANResponse carries none of this, and a future library version
    may name it differently. Unknown must behave exactly as everything did
    before this module existed."""

    def test_a_response_without_any_of_it(self):
        v = fr.verdict_of(object())
        self.assertEqual(v["status"], fr.UNKNOWN)
        self.assertFalse(fr.refused(v))
        self.assertEqual(v["lines"], [])

    def test_none_is_not_a_crash(self):
        self.assertFalse(fr.refused(fr.verdict_of(None)))

    def test_an_empty_line_is_dropped(self):
        v = fr.verdict_of(Answer([Line(None, None), Line("0010", "ok")]))
        self.assertEqual(len(v["lines"]), 1)


class TestItSaysTheBanksOwnWords(unittest.TestCase):
    """What a person has to be able to do with this is read it out to their
    bank, and "9010" plus the institute's own sentence is what an adviser
    recognises."""

    def test_the_code_and_the_text_are_both_there(self):
        text = fr.as_text(fr.verdict_of(
            Answer([Line("9210", "Ungültige Auftragsdaten")])))
        self.assertIn("9210", text)
        self.assertIn("Ungültige Auftragsdaten", text)

    def test_the_parameters_are_kept_too(self):
        """A HIRMS line is code / reference_element / text / parameters, and
        the parameters are where an institute puts the part that helps --
        the text alone is often only "Ungültige Auftragsdaten"."""
        text = fr.as_text(fr.verdict_of(Answer([
            Line("9210", "Ungültige Auftragsdaten", "ReqdExctnDt")])))
        self.assertIn("ReqdExctnDt", text)

    def test_a_detail_that_only_repeats_the_text_is_not_doubled(self):
        text = fr.as_text(fr.verdict_of(Answer([
            Line("9010", "abgelehnt", "abgelehnt")])))
        self.assertEqual(text.count("abgelehnt"), 1)

    def test_nothing_said_is_an_empty_string(self):
        self.assertEqual(fr.as_text(fr.verdict_of(object())), "")

    def test_only_the_complaints_when_that_is_what_is_wanted(self):
        v = fr.verdict_of(Answer([Line("0010", "ok"), Line("9010", "nein")]))
        self.assertEqual([c["code"] for c in fr.complaints(v)], ["9010"])


class TestTheCodesWeHaveMetAreExplained(unittest.TestCase):
    """Only the ones this instance has actually run into. An explanation
    invented for a code nobody here has seen is a guess wearing the clothes of
    documentation."""

    def _hint_3945(self):
        return fr.advice(fr.verdict_of(Answer([
            Line("3945", "Freigabe ohne VOP-Bestätigung nicht möglich.")])))

    def test_3945_says_what_to_do(self):
        """The one that stopped a real payment: the bank checked the payee and
        will not release the order until that check is confirmed."""
        hints = self._hint_3945()
        self.assertEqual(len(hints), 1)
        self.assertIn("payee", hints[0])
        self.assertIn("NOT sent", hints[0])

    def test_3945_no_longer_sends_the_user_in_a_circle(self):
        """It used to say "fetch the account once, then send again", on the
        theory that the cached bank parameters were too old to advertise VoP.
        Reading them settled it: HIVPPS is there, it lists HKIPZ and HKCCS, and
        the state was hours old. Fetching changes nothing, and saying so cost a
        user two attempts."""
        hint = self._hint_3945()[0].lower()
        self.assertNotIn("fetch the account", hint)
        self.assertNotIn("capabilities", hint)

    def test_an_unknown_code_gets_no_invented_explanation(self):
        self.assertEqual(fr.advice(fr.verdict_of(
            Answer([Line("3076", "Starke Kundenauthentifizierung")]))), [])

    def test_a_code_seen_twice_is_explained_once(self):
        hints = fr.advice(fr.verdict_of(Answer([
            Line("9010", "nein"), Line("9010", "wieder nein")])))
        self.assertEqual(len(hints), 1)

    def test_nothing_said_needs_no_advice(self):
        self.assertEqual(fr.advice(fr.verdict_of(object())), [])
