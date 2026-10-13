# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Beim Einlesen entscheidet der Betrag, und das Datum darf wackeln.

Am 15.08.2026 hat ein CSV-Einlesen 863 Buchungen angelegt, von denen 230
schon da waren -- der Nutzer sah sie danach in der Finanzuebersicht doppelt,
mit zwei verschiedenen Texten. Die Pruefung gab es damals schon; sie
verglich Tag und Betrag auf den Tag genau. Gemessen beim Aufraeumen am
25.09.2026:

    gleicher Tag     157
    1 Tag daneben     26
    2 Tage             9
    3 Tage             8
    4 Tage             8
    5 bis 7 Tage       3
    kein Partner     633   (echte Luecken, die die Datei gefuellt hat)

73 der 230 lagen also daneben, und zwar nicht zufaellig: die Bank nennt
Buchungstag und Valuta, und welchen von beiden eine Exportdatei traegt,
entscheidet das Programm, das sie geschrieben hat.

Ein Budget und keine Menge -- der Auszug eines Kontos enthaelt neun
Gebuehren von 5,10 EUR am selben Tag. Was hier zaehlt, ist "gibt es noch
eine unverbrauchte", nicht "gibt es so eine".
"""

import datetime
import os
import unittest

from kefiya.utils import booking_budget

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

#: -80,00 EUR, wie die VOEGELI-Zahlung aus dem Bild des Nutzers.
ACHTZIG = -8000


class TestWasDasKontoSchonHat(unittest.TestCase):

    def test_gezaehlt_wird_nach_betrag_und_tag(self):
        b = booking_budget.budget_from(
            [("2026-04-20", ACHTZIG), ("2026-04-20", ACHTZIG),
             ("2026-04-20", -4500)])
        self.assertEqual(b[ACHTZIG][datetime.date(2026, 4, 20)], 2)
        self.assertEqual(b[-4500][datetime.date(2026, 4, 20)], 1)

    def test_datum_und_zeitpunkt_und_unsinn(self):
        b = booking_budget.budget_from(
            [(datetime.date(2026, 4, 20), 1),
             (datetime.datetime(2026, 4, 20, 9, 0), 1),
             ("kein Datum", 1), (None, 1)])
        self.assertEqual(b[1][datetime.date(2026, 4, 20)], 2)


class TestWasVerbrauchtWird(unittest.TestCase):

    def setUp(self):
        self.budget = booking_budget.budget_from(
            [("2026-04-20", ACHTZIG), ("2026-04-20", ACHTZIG)])

    def test_der_tag_selbst_zuerst(self):
        self.assertEqual(
            booking_budget.consume(self.budget, "2026-04-20", ACHTZIG), "tag")

    def test_jede_vorhandene_nur_einmal(self):
        """Zwei vorhandene decken zwei Zeilen -- die dritte ist neu."""
        self.assertTrue(booking_budget.consume(self.budget, "2026-04-20", ACHTZIG))
        self.assertTrue(booking_budget.consume(self.budget, "2026-04-20", ACHTZIG))
        self.assertEqual(
            booking_budget.consume(self.budget, "2026-04-20", ACHTZIG), "")

    def test_ein_paar_tage_daneben_ist_dieselbe_buchung(self):
        b = booking_budget.budget_from([("2026-03-30", 32002)])
        self.assertEqual(booking_budget.consume(b, "2026-04-01", 32002),
                         "fenster")

    def test_und_darueber_hinaus_nicht(self):
        b = booking_budget.budget_from([("2026-03-30", 32002)])
        self.assertEqual(booking_budget.consume(b, "2026-04-10", 32002), "")

    def test_ein_anderer_betrag_ist_eine_andere_buchung(self):
        self.assertEqual(
            booking_budget.consume(self.budget, "2026-04-20", -4500), "")

    def test_eingang_und_ausgang_heben_sich_nicht_auf(self):
        b = booking_budget.budget_from([("2026-04-20", 25000)])
        self.assertEqual(booking_budget.consume(b, "2026-04-20", -25000), "")

    def test_der_naechste_tag_wird_zuerst_verbraucht(self):
        """Damit das Ergebnis nicht von der Reihenfolge des Durchlaufs
        abhaengt: von zwei gleich weit entfernten der fruehere."""
        b = booking_budget.budget_from([("2026-04-18", 500), ("2026-04-22", 500)])
        self.assertEqual(booking_budget.consume(b, "2026-04-21", 500), "fenster")
        self.assertEqual(b[500][datetime.date(2026, 4, 22)], 0)
        self.assertEqual(b[500][datetime.date(2026, 4, 18)], 1)

    def test_ohne_fenster_nur_der_tag(self):
        b = booking_budget.budget_from([("2026-03-30", 7)])
        self.assertEqual(booking_budget.consume(b, "2026-04-01", 7, 0), "")

    def test_leeres_budget_haelt_nichts_an(self):
        self.assertEqual(booking_budget.consume({}, "2026-04-20", 1), "")
        self.assertEqual(
            booking_budget.consume(self.budget, "kein Datum", ACHTZIG), "")


class TestDasEinlesenBenutztEs(unittest.TestCase):
    """Quelltext-Pruefung, und sie weiss das (CLAUDE.md)."""

    def setUp(self):
        with open(os.path.join(WURZEL, "utils", "statement_import.py"),
                  encoding="utf-8") as handle:
            self.quelle = handle.read()

    def test_das_budget_kommt_von_hier(self):
        vorhanden = self.quelle.split("def _existing_budget(")[1] \
                               .split("\ndef ")[0]
        self.assertIn("booking_budget.budget_from(rows)", vorhanden)
        # Und es liest ueber den Rand der Datei hinaus, sonst koennte das
        # Fenster gar nicht greifen.
        self.assertIn("booking_budget.TOLERANZ_TAGE", vorhanden)

    def test_verbraucht_wird_beim_buchen(self):
        buchen = self.quelle.split("def book_entries(")[1].split("\ndef ")[0]
        self.assertIn("booking_budget.consume(budget, tag, cent)", buchen)
        self.assertIn("duplicates_shifted", buchen)

    def test_die_bank_selbst_behaelt_den_tag(self):
        """already_booked prueft eine Buchung, die von der Bank kommt und
        deren Tag traegt -- dort waere ein Fenster falsch."""
        genau = self.quelle.split("def already_booked(")[1].split("\ndef ")[0]
        # Nur der Rumpf: der Text darueber darf das Fenster erklaeren, die
        # Abfrage darunter darf es nicht benutzen.
        rumpf = genau.split('"""')[2]
        self.assertNotIn("booking_budget", rumpf)
        self.assertIn('"date": str(entry["date"])', rumpf)
