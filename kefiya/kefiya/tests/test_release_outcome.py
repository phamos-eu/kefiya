# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Gesendet heisst: die Bank hat es gesagt. Nicht: nichts ist explodiert.

Auftrag A, 03.09.2026, 12:45. Die Sparkasse bat um die Freigabe in
der App (3955), sagte auf die erste Statusabfrage "noch ausstehend" (3956)
und auf die zweite 9050/9010 "Der Auftrag wurde nicht ausgefuehrt".
python-fints warf daraufhin eine Exception, der Konstruktor des Controllers
fing sie, raeumte auf und gab sie nicht weiter -- und send_transfer_tan
schloss aus dem durchgelaufenen Konstruktor auf die Freigabe und schrieb
"Gesendet". Im Online-Banking gab es die Ueberweisung nicht, im
Fehlerprotokoll stand nichts.

Die Regel selbst laeuft hier ohne Bench. Der Rest ist Quelltext-Pruefung
und weiss das (CLAUDE.md): sie sagt, dass die Zeile da steht, nicht, dass
sie laeuft. test_every_python_file_compiles sagt, dass die Namen aufloesen.
"""

import os
import unittest

from kefiya.utils import release_outcome

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


def _py(*parts):
    with open(os.path.join(WURZEL, *parts), encoding="utf-8") as handle:
        return handle.read()


def _funktion(quelle, kopf):
    start = quelle.index(kopf)
    rest = quelle[start + len(kopf):]
    ende = rest.find("\ndef ")
    return kopf + (rest if ende < 0 else rest[:ende])


class TestDasEineWort(unittest.TestCase):
    """Nur RELEASED erlaubt "Gesendet". Alles andere heisst nein."""

    def test_nur_die_freigabe_erlaubt_gesendet(self):
        self.assertTrue(release_outcome.may_mark_sent(release_outcome.RELEASED))

    def test_alles_andere_nicht(self):
        for wort in (release_outcome.NOTHING_PARKED, release_outcome.PENDING,
                     release_outcome.FAILED, None, "", "submitted", True):
            self.assertFalse(release_outcome.may_mark_sent(wort), wort)

    def test_vier_verschiedene_woerter(self):
        woerter = {release_outcome.NOTHING_PARKED, release_outcome.PENDING,
                   release_outcome.RELEASED, release_outcome.FAILED}
        self.assertEqual(len(woerter), 4)


class TestDerControllerSagtEs(unittest.TestCase):
    """Der Konstruktor schreibt das Wort, an jeder Stelle, an der sich das
    Schicksal der Freigabe entscheidet."""

    def setUp(self):
        self.controller = _py("utils", "fints_controller.py")
        self.session = _py("utils", "fints_tan_session.py")

    def _block(self):
        return self.controller.split("stored_tan_blob \\")[1].split(
            "\n        # After successful login")[0]

    def test_ohne_geparkte_anforderung_ist_nichts_freigegeben(self):
        """Vor dem Block gesetzt, damit ein Aufrufer immer ein Wort findet."""
        davor = self.controller.split("stored_tan_blob \\")[0]
        self.assertIn("self.release_outcome = release_outcome.NOTHING_PARKED",
                      davor)

    def test_das_wiederaufnehmen_beginnt_als_ausstehend(self):
        block = self._block()
        self.assertLess(
            block.index("self.release_outcome = release_outcome.PENDING"),
            block.index("self._resume_and_answer_the_parked_tan(tan)"))

    def test_der_verschluckte_fehler_heisst_jetzt_gescheitert(self):
        """Weiter verschluckt -- der Abruf laeuft mit frischem Login weiter --
        aber als FAILED, mit den Worten der Bank, und im Fehlerprotokoll."""
        gescheitert = self._block().split("except Exception:")[1]
        self.assertIn("self.release_outcome = release_outcome.FAILED",
                      gescheitert)
        self.assertIn("self.__note_the_failed_release()", gescheitert)
        notiz = _funktion(self.controller, "    def __note_the_failed_release(")
        self.assertIn("what_the_bank_said(self.fints_connection)", notiz)
        self.assertIn("frappe.log_error(", notiz)
        # Hier stand "is NOT sent". Das war eine Behauptung aus einer
        # Abfrage, die gescheitert ist -- und am 09.10.2026 stand daneben,
        # was die Bank gesagt hatte, und beides passte nicht zusammen. Die
        # sichere Richtung bleibt: nicht als gesendet verbuchen. Nur das
        # wird noch zugesagt, und das Nichtwissen wird benannt.
        self.assertIn("not marked as sent", notiz)
        self.assertIn("is NOT known from", notiz)
        self.assertNotIn("it belonged to is NOT sent", notiz)

    def test_freigegeben_erst_nach_der_pruefung_der_antwort(self):
        antwort = _funktion(self.session,
                            "    def _resume_and_answer_the_parked_tan(")
        self.assertLess(
            antwort.index("self._refuse_a_refused_order()"),
            antwort.index("self.release_outcome = release_outcome.RELEASED"))


class TestSendTransferTanLiestDasWort(unittest.TestCase):

    def setUp(self):
        self.quelle = _py("utils", "client.py")
        self.senden = _funktion(self.quelle, "def send_transfer_tan(")

    def test_gesendet_nur_nach_dem_wort(self):
        self.assertIn("controller = FinTSController(", self.senden)
        pruefung = self.senden.index(
            "release_outcome.may_mark_sent(controller.release_outcome)")
        self.assertLess(pruefung, self.senden.index("_mark_sent(docs, {}"))

    def test_sonst_ein_fehler_mit_den_worten_der_bank(self):
        self.assertIn('return {"status": "error",', self.senden)
        self.assertIn("_release_not_given(controller, kefiya_login)",
                      self.senden)
        erklaerung = _funktion(self.quelle, "def _release_not_given(")
        self.assertIn("What the bank said", erklaerung)
        self.assertIn("NOT sent", erklaerung)
        # Und der Fall, in dem gar nichts geparkt war: frueher "submitted".
        self.assertIn("nothing was marked as sent", erklaerung)
