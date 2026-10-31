# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Eine TAN fuer die Anmeldung ist kein Fehler, und sie ist nicht die TAN
fuer den Auftrag.

``trusted_client_context`` fragt die TAN beim Nutzer an und loest dann
``TanInteractionRequired`` aus, um seinen Rumpf zu ueberspringen. Das ist ein
Signal. ``send_transfer_outbox`` fing es nicht, also kam es im Browser als
roher Traceback an und die Ueberweisung liess sich gar nicht abschliessen.

Worauf es hier ankommt, und warum dieser Test existiert: die zwei TANs
verlangen das genaue Gegenteil voneinander.

    vor dem Handschlag   bestaetigen, dann ERNEUT senden -- es hat nichts
                         die Maschine verlassen
    auf den Auftrag      nie erneut senden. Die kommt hier gar nicht durch:
                         submit_sepa_transfer parkt sie und gibt
                         {"status": "tan_required"} als Wert zurueck.

Verwechselt man sie, wird entweder nie gezahlt oder zweimal.

Quelltext gelesen, weil client.py frappe oben importiert und ausserhalb
einer Bench nicht laedt.
"""

import os
import re
import unittest


def _read(*parts):
    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(pfad, encoding="utf-8") as handle:
        return handle.read()


def _function(source, name):
    """Eine def, nach Einrueckung."""
    # [ \t]* und nicht \s*: \s trifft auch Zeilenumbrueche, die Suche
    # beginnt dann auf einer Leerzeile darueber und misst die Einrueckung
    # von nichts. Dieselbe Falle steht in test_client_state_recovery.
    hit = re.search(r"^([ \t]*)def %s\(" % re.escape(name), source, re.M)
    assert hit, name
    tiefe = len(hit.group(1))
    zeilen = source[hit.start():].split("\n")
    raus = [zeilen[0]]
    for zeile in zeilen[1:]:
        if zeile.strip() and (len(zeile) - len(zeile.lstrip())) <= tiefe:
            break
        raus.append(zeile)
    return "\n".join(raus)


class TestDerAbbruchKommtNichtMehrRohAn(unittest.TestCase):

    def setUp(self):
        self.py = _function(_read("utils", "client.py"),
                            "send_transfer_outbox")

    def test_die_ausnahme_wird_ueberhaupt_erkannt(self):
        self.assertIn("TanInteractionRequired", self.py)
        self.assertIn('"status": "tan_required"', self.py)

    def test_und_als_anmeldung_benannt(self):
        """Damit der Aufrufer weiss, dass danach gesendet werden muss."""
        self.assertIn('"stage": "authentication"', self.py)

    def test_die_sperren_fallen_vorher(self):
        """Der Sendeschutz je Auftrag darf nicht ueber die TAN-Eingabe
        hinweg stehen bleiben: die Antwort kommt in einem spaeteren
        Request, und der muss den Auftrag beanspruchen koennen."""
        stelle = self.py.index('"stage": "authentication"')
        davor = self.py[:stelle]
        self.assertIn("frappe.cache().delete_value(key)", davor)


class TestNurVorDemHandschlag(unittest.TestCase):
    """Die Unterscheidung wird bewiesen, nicht angenommen: controller bleibt
    None, solange der Konstruktor laeuft. Was spaeter ausloest, faellt in das
    alte Verhalten und behauptet nichts."""

    def setUp(self):
        self.py = _function(_read("utils", "client.py"),
                            "send_transfer_outbox")

    def test_der_beweis_steht_in_der_bedingung(self):
        self.assertIn("if controller is None and isinstance("
                      "exc, TanInteractionRequired):", self.py)

    def test_und_wird_vorher_gesetzt(self):
        self.assertIn("controller = None", self.py)
        self.assertLess(self.py.index("controller = None"),
                        self.py.index("controller = FinTSController("))

    def test_alles_andere_fliegt_weiter(self):
        self.assertTrue(self.py.rstrip().endswith("raise")
                        or "\n        raise\n" in self.py, self.py[-300:])


class TestEineFehlgeschlageneAbfrageWeissNichts(unittest.TestCase):
    """Der Logeintrag behauptete "the order it belonged to is NOT sent" --
    als Tatsache, aus einer Abfrage, die gescheitert ist. Am 09.10.2026 war
    danebengeschrieben, was die Bank gesagt hatte, und beides passte nicht
    zusammen. Was nicht bekannt ist, wird nicht behauptet."""

    def setUp(self):
        self.py = _function(_read("utils", "fints_controller.py"),
                            "__note_the_failed_release")

    def test_es_sagt_dass_es_nichts_weiss(self):
        # Ueber zwei Quellzeilen zusammengesetzt, deshalb in Stuecken
        # geprueft -- der Satz lautet "... is NOT known from here."
        self.assertIn("is NOT known from", self.py)
        self.assertIn(" here.", self.py)

    def test_und_behauptet_nicht_mehr_das_gegenteil(self):
        self.assertNotIn("it belonged to is NOT sent", self.py)

    def test_der_auftrag_gilt_trotzdem_nicht_als_gesendet(self):
        """Nicht wissen heisst nicht gutschreiben. Die sichere Richtung
        bleibt die sichere Richtung."""
        self.assertIn("not marked as sent", self.py)

    def test_und_nennt_wer_es_entscheidet(self):
        self.assertIn("online banking", self.py)


if __name__ == "__main__":
    unittest.main()
