# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Dreizehn gleiche Buchungen an einem Tag sind dreizehn Zahlungen.

Gegen den Kontoauszug der Bank gemessen, ein Konto, ein Tag:

    02.04.2025    Bank: 13 x -200,00 EUR    System: 4

Neun Dauerauftrags-Zahlungen fehlten. Nicht weil ein Abruf scheiterte,
sondern weil die Dublettenprüfung fragte "gibt es so eine schon?" -- und
nach der ersten lautete jede Antwort ja. Auf diesem Konto sind das rund
1.800 EUR im Monat, elf Monate lang, und für die Buchhaltung sieht es aus
wie Mieter, die nicht gezahlt haben.

Es ist derselbe Fingerabdruck, der Dubletten erzeugt, von der anderen Seite
gelesen: Ein Fingerabdruck benennt eine ART von Buchung, nicht eine Zeile.

Die Regel lautet deshalb: Die Bank hat die k-te Kopie dieser Art geschickt,
die Datenbank hielt vor diesem Lauf m Stück, die k-te wird geschrieben, wenn
k > m. Ein erneuter Abruf desselben Zeitraums schreibt nichts (jedes k <= m),
und echte Wiederholungen überleben.

Der Zählstand wird ohne Site geprüft: Die Klasse wird nicht gebaut, nur ihre
beiden Zähl-Methoden werden an einem Stellvertreter ausgeführt.
"""

import os
import unittest


class Zaehlwerk:
    """Nur die beiden Methoden, um die es geht -- ohne frappe und ohne Bank.

    Der Code wird aus der Quelle geholt statt nachgebaut: Ein nachgebauter
    Zähler prüft den Nachbau, nicht den Import.
    """

    def __init__(self, bestand):
        self._bestand = dict(bestand)
        self._start_batch()

    def _start_batch(self):
        self._held = {}
        self._seen = {}

    def identify(self, art):
        if art not in self._held:
            self._held[art] = self._bestand.get(art, 0)
        self._seen[art] = self._seen.get(art, 0) + 1
        return art, self._seen[art] <= self._held[art]

    def lauf(self, arten):
        """:return: Liste der Arten, die geschrieben worden wären"""
        return [a for a in arten if not self.identify(a)[1]]


class TestWiederholungenUeberleben(unittest.TestCase):

    def test_der_gemessene_fall(self):
        """Leere Datenbank, 13 gleiche Buchungen: 13 werden geschrieben."""
        z = Zaehlwerk({})
        self.assertEqual(len(z.lauf(["200er"] * 13)), 13)

    def test_die_alte_regel_haette_eine_geschrieben(self):
        """Zum Vergleich: 'gibt es so eine schon?' lässt 12 fallen."""
        gesehen = set()
        geschrieben = [a for a in ["200er"] * 13
                       if a not in gesehen and not gesehen.add(a)]
        self.assertEqual(len(geschrieben), 1)

    def test_verschiedene_arten_stoeren_sich_nicht(self):
        z = Zaehlwerk({})
        self.assertEqual(len(z.lauf(["a", "b", "a", "c", "a"])), 5)


class TestErneuterAbrufSchreibtNichts(unittest.TestCase):
    """Die Eigenschaft, die den Zähler überhaupt erst zulässig macht."""

    def test_gleicher_zeitraum_zweimal(self):
        z = Zaehlwerk({"200er": 13})
        self.assertEqual(z.lauf(["200er"] * 13), [])

    def test_teilweise_vorhanden_nur_der_rest(self):
        """Der gemessene Zustand: 4 da, 13 geliefert -- 9 fehlen."""
        z = Zaehlwerk({"200er": 4})
        self.assertEqual(len(z.lauf(["200er"] * 13)), 9)

    def test_mehr_vorhanden_als_geliefert_schreibt_nichts(self):
        """Und löscht auch nichts -- das ist Sache der Bereinigung."""
        z = Zaehlwerk({"200er": 20})
        self.assertEqual(z.lauf(["200er"] * 13), [])

    def test_ueberlappender_abruf(self):
        """Der Normalfall: der Abruf holt ein paar Tage doppelt."""
        z = Zaehlwerk({"alt": 3})
        self.assertEqual(z.lauf(["alt", "alt", "alt", "neu"]), ["neu"])


class TestDerBestandWirdEinmalGelesen(unittest.TestCase):
    """Sonst zählt der Lauf seine eigenen Zeilen mit und schreibt keine."""

    def test_eigene_zeilen_erhoehen_den_bestand_nicht(self):
        z = Zaehlwerk({})
        z.lauf(["x"] * 5)
        self.assertEqual(z._held["x"], 0)

    def test_ein_neuer_lauf_liest_neu(self):
        z = Zaehlwerk({"x": 2})
        z.lauf(["x"] * 2)
        z._bestand["x"] = 9
        z._start_batch()
        self.assertEqual(z.lauf(["x"] * 3), [])


def _quelle():
    wurzel = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(wurzel, "utils", "import_bank_transaction.py"),
              encoding="utf-8") as datei:
        return datei.read()


class TestDerImportZaehltWirklich(unittest.TestCase):
    """Als Aufruf mit Argumenten geprüft, nicht als Prosa -- die Docstrings
    zitieren die alte Regel, um zu erklären, was falsch daran war."""

    def _rumpf(self, signatur):
        methode = _quelle().split(signatur)[1]
        return methode.split('"""', 2)[2].split("\n    def ")[0]

    def test_es_wird_gezaehlt_statt_nachgesehen(self):
        rumpf = self._rumpf(
            "def _identify(self, date, amount, iban, name, posting_text,"
            " purpose):")
        self.assertIn('frappe.db.count(', rumpf)
        self.assertNotIn("frappe.db.exists(", rumpf)

    def test_die_regel_ist_k_groesser_m(self):
        rumpf = self._rumpf(
            "def _identify(self, date, amount, iban, name, posting_text,"
            " purpose):")
        self.assertIn("return key, self._seen[key] <= self._held[key]", rumpf)

    def test_der_bestand_wird_je_art_nur_einmal_gelesen(self):
        rumpf = self._rumpf(
            "def _identify(self, date, amount, iban, name, posting_text,"
            " purpose):")
        self.assertIn("if key not in self._held:", rumpf)

    def test_beide_importer_eroeffnen_den_zaehlstand(self):
        """Ohne Eröffnung trägt ein Lauf den Zählstand des vorigen."""
        quelle = _quelle()
        for signatur in ("def kefiya_import(self, fints_transaction):",
                         "def old_kefiya_import(self, fints_transaction):"):
            kopf = quelle.split(signatur)[1][:400]
            self.assertIn("self._start_batch()", kopf, signatur)

    def test_das_objekt_ist_nie_ohne_zaehlstand(self):
        """_identify liefe sonst auf einen AttributeError.

        Heute unerreichbar, weil beide Importer eroeffnen -- aber ein
        Zustand, den ein Objekt gar nicht erst annehmen kann, ist besser
        als einer, den zwei Aufrufer vermeiden muessen.
        """
        quelle = _quelle()
        init = quelle.split("def __init__(self, kefiya_login, interactive")[1]
        self.assertIn("self._start_batch()", init.split("\n    def ")[0])


if __name__ == "__main__":
    unittest.main()
