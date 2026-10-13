# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein Auftrag, der sich nicht parken laesst, geht gar nicht erst zur Bank.

29.09.2026, 19:19 Uhr. Auftrag C, 20.000,00 EUR auf ein eigenes Konto, der
erste Terminauftrag -- einer, den die BANK bis zum 30.09. halten sollte. Die Volksbank hatte die pain-Nachricht schon und fragte
nach der Bestaetigung des Empfaengers. Beim Aufschreiben dieser Anforderung::

    File "kefiya/utils/fints_controller.py", line 1850, in _persist_vop_state
      self.kefiya_login.stored_vop_blob = response.get_data()
    File "fints/client.py", line 1125, in get_data
      return compress_datablob(DATA_BLOB_MAGIC_RETRY, 1, data)
    File "fints/utils.py", line 40, in compress_datablob
      serialized = json.dumps(data).encode('utf-8')
    TypeError: Object of type function is not JSON serializable

Die Ursache stand in _send_scheduled_transfer: der Wiederaufnahme-Punkt war
eine Closure. Die Bibliothek merkt sich diesen Punkt als NAMEN --

    if hasattr(resume_method, '__func__'):
        self.resume_method = resume_method.__func__.__name__

-- und sucht ihn spaeter mit ``getattr(self, challenge.resume_method)``
wieder. Eine Closure hat kein ``__func__``; sie bleibt als Funktionsobjekt
stehen, und json.dumps kennt keine Funktionen. Selbst wenn es sich hatte
schreiben lassen, waere es beim Wiederfinden zerbrochen: eine Closure
ueberlebt die Runde durch die Datenbank nie.

Was das kostete: die Ausnahme nahm die Transaktion mit, und damit die
geparkte Anforderung. Die Bank kannte den Auftrag, der Nutzer hatte nichts,
was er haette freigeben koennen. Freigegeben wurde er nicht -- kein TAN, kein
HKVPA -- aber genau das ist die Lage, in der niemand mehr weiss, was gilt.

Deshalb wird jetzt vorher gefragt, bevor der Dialog ueberhaupt aufgeht.
"""

import os
import unittest

from kefiya.utils import resume_point

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


class _EinClient:
    def _continue_sepa_transfer(self, command_seg, response):
        return None


def _eine_closure():
    laufzeit = 1

    def _resume(command_seg, response):
        return laufzeit
    return _resume


def _eine_freie_funktion(command_seg, response):
    return None


class TestWasSichAufschreibenLaesst(unittest.TestCase):

    def test_eine_gebundene_methode_ja(self):
        """Sie hat ein __func__, aus dem die Bibliothek den Namen nimmt, und
        ein __self__, das belegt, dass der Name spaeter am Client auch
        gefunden wird."""
        self.assertTrue(resume_point.can_be_written_down(
            _EinClient()._continue_sepa_transfer))

    def test_eine_closure_nein(self):
        """Das war der Fehler. Kein __func__, also bleibt das Funktions-
        objekt stehen -- und json.dumps kennt keine Funktionen."""
        self.assertFalse(resume_point.can_be_written_down(_eine_closure()))

    def test_eine_freie_funktion_nein(self):
        """Auch sie hat keinen Namen AM CLIENT, unter dem getattr sie
        wiederfaende."""
        self.assertFalse(
            resume_point.can_be_written_down(_eine_freie_funktion))

    def test_ein_name_ja(self):
        """So steht er nach dem Wiedereinlesen im geparkten Zustand. Ein
        zweites Parken darf daran nicht scheitern."""
        self.assertTrue(
            resume_point.can_be_written_down("_continue_sepa_transfer"))

    def test_nichts_nein(self):
        self.assertFalse(resume_point.can_be_written_down(None))
        self.assertFalse(resume_point.can_be_written_down(""))
        self.assertFalse(resume_point.can_be_written_down(42))

    def test_die_regel_braucht_keine_bench(self):
        quelle = _lies("utils", "resume_point.py")
        self.assertNotIn("import frappe", quelle)


def _lies(*teile):
    with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
        return handle.read()


class TestDerTerminauftrag(unittest.TestCase):

    def setUp(self):
        self.quelle = _lies("utils", "fints_controller.py")
        self.termin = self.quelle.split("def _send_scheduled_transfer(")[1] \
                                 .split("\n    def ")[0]

    def test_keine_closure_mehr(self):
        """Die Zeile, die 20.000 EUR gekostet haette: `def _resume(` mitten
        im Terminauftrag."""
        self.assertNotIn("def _resume(", self.termin)

    def test_eine_gebundene_methode_wird_uebergeben(self):
        self.assertIn('getattr(conn, "_continue_scheduled_transfer", None)',
                      self.termin)
        self.assertIn("or conn._continue_sepa_transfer", self.termin)
        self.assertIn("_send_pay_with_possible_retry(dialog, seg, resume)",
                      self.termin)

    def test_gefragt_wird_vor_dem_dialog(self):
        """Ein Auftrag, der sich nicht parken laesst, darf die Bank nicht
        einmal sehen. Also steht die Frage VOR conn._get_dialog()."""
        self.assertIn("resume_point.can_be_written_down(resume)", self.termin)
        vorher = self.termin.split("with conn._get_dialog()")[0]
        self.assertIn("resume_point.can_be_written_down(resume)", vorher)

    def test_die_ablehnung_sagt_dass_nichts_gesendet_wurde(self):
        absage = self.termin.split("can_be_written_down(resume)")[1] \
                            .split("with conn._get_dialog()")[0]
        self.assertIn("NOT sent", absage)
        self.assertIn("nothing has reached the bank", absage)

    def test_resume_point_ist_importiert(self):
        """Der Umzug, bei dem Importe zurueckbleiben -- siehe CLAUDE.md."""
        self.assertIn("from kefiya.utils import resume_point", self.quelle)


class TestDieMethodeAmClient(unittest.TestCase):

    def setUp(self):
        self.quelle = _lies("utils", "fints_vop_client.py")

    def test_sie_steht_an_der_klasse(self):
        """An der Klasse und nicht irgendwo: nur so findet
        getattr(self, name) sie nach dem Wiedereinlesen."""
        self.assertIn(
            "        def _continue_scheduled_transfer(self, command_seg,"
            " response):", self.quelle)

    def test_sie_tut_dasselbe_wie_die_closure_vorher(self):
        methode = self.quelle.split(
            "def _continue_scheduled_transfer(")[1].split("\n        def ")[0]
        self.assertIn("self._continue_sepa_transfer(command_seg, response)",
                      methode)
        self.assertIn("read_task_id(response)", methode)
        self.assertIn('result.data["task_id"] = task_id', methode)

    def test_der_wiederaufnahme_punkt_wird_am_client_gesucht(self):
        """Der Gegenpart, der die ganze Regel erzwingt."""
        self.assertIn("getattr(self, challenge.resume_method)", self.quelle)
