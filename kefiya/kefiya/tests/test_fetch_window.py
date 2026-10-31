# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der naechste Abruf faengt vor der letzten Buchung an, nicht auf ihr.

30.09.2026, Musterverwaltung GmbH Volksbank
(DE19888888880004444444). Der Ablauf, Zeile fuer Zeile aus der Instanz:

    29.09. 18:42   ein Abruf von Hand holt zwei Zeilen mit Datum 30.09.
                   (Stadtkasse 91,65; Rundfunk 3,46 -- Valuta voraus)
    29.09. abends  20.000,00 EUR gehen von 5555555 ein
    30.09. 06:01   der Nachtlauf rechnet: juengste Buchung = 30.09.
                   und fragt die Bank nur nach dem 30.09.

Die Gutschrift traegt den 29.09. Sie lag vor dem Fenster und wurde nie
wieder erfragt -- am naechsten Morgen ist die juengste Buchung wieder gleich
alt oder juenger. Auf dem Gegenkonto stand die Belastung sauber da: dort lief
das Fenster vom 29.09. bis zum 30.09.

Die Obergrenze "nie in der Zukunft anfangen" gab es schon. Sie half nicht:
aus dem Datum von morgen wird ueber Nacht das Datum von heute.

Was dieser Test festhaelt:

  * Der Anfang liegt vier Tage vor der juengsten Buchung -- und damit vor
    der Luecke, die ein Datum aus der Zukunft aufreisst.
  * Die Ueberschneidung kostet nichts, weil der Import Doppelte am Inhalt
    erkennt (booking_fingerprint.of_row). Ohne die waere sie teuer.
  * Im Zweifel mehr statt weniger: eine Zeile doppelt zu holen erkennt der
    Import, eine ungeholte Zeile merkt niemand.
"""

import datetime
import os
import unittest

from kefiya.utils import booking_budget, fetch_window

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

HEUTE = datetime.date(2026, 9, 30)


def _lies(*teile):
    with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
        return handle.read()


class TestDerFall(unittest.TestCase):

    def test_die_zwanzigtausend_waeren_im_fenster_gewesen(self):
        """Juengste Buchung 30.09. (Valuta voraus, am 29.09. geholt), heute
        der 30.09. Vorher fing der Abruf am 30.09. an; die Gutschrift vom
        29.09. lag davor."""
        anfang = fetch_window.start_of_window(
            datetime.date(2026, 9, 30), HEUTE, 90)
        self.assertEqual(anfang, datetime.date(2026, 9, 26))
        self.assertLess(anfang, datetime.date(2026, 9, 29))

    def test_das_gegenkonto_verlor_nichts(self):
        """Dort war die juengste Buchung der 29.09. -- auch da faengt es
        jetzt frueher an, und die Belastung bleibt im Fenster."""
        self.assertEqual(
            fetch_window.start_of_window(datetime.date(2026, 9, 29), HEUTE, 90),
            datetime.date(2026, 9, 25))


class TestDieDreiGrenzen(unittest.TestCase):

    def test_nie_in_der_zukunft(self):
        """Ein Datum aus der Zukunft darf den Anfang nicht mitnehmen --
        sonst waere from_date groesser als to_date."""
        self.assertEqual(
            fetch_window.start_of_window(datetime.date(2026, 10, 9), HEUTE, 90),
            datetime.date(2026, 9, 26))

    def test_nicht_weiter_zurueck_als_erlaubt(self):
        """Weiter zurueck gibt die Bank ohnehin nichts heraus."""
        self.assertEqual(
            fetch_window.start_of_window(datetime.date(2026, 7, 3), HEUTE, 90),
            datetime.date(2026, 7, 2))
        self.assertEqual(
            fetch_window.start_of_window(datetime.date(2020, 1, 1), HEUTE, 90),
            datetime.date(2026, 7, 2))

    def test_ohne_historie_das_ganze_fenster(self):
        self.assertEqual(fetch_window.start_of_window(None, HEUTE, 90),
                         datetime.date(2026, 7, 2))
        self.assertEqual(fetch_window.start_of_window("", HEUTE, 30),
                         datetime.date(2026, 8, 31))

    def test_unlesbares_datum_heisst_ganzes_fenster(self):
        """Im Zweifel mehr: eine ungeholte Zeile merkt niemand."""
        self.assertEqual(fetch_window.start_of_window("gestern", HEUTE, 90),
                         datetime.date(2026, 7, 2))

    def test_texte_und_zeitstempel_werden_gelesen(self):
        self.assertEqual(
            fetch_window.start_of_window("2026-09-30", HEUTE, 90),
            datetime.date(2026, 9, 26))
        self.assertEqual(
            fetch_window.start_of_window(
                datetime.datetime(2026, 9, 30, 6, 1), HEUTE, 90),
            datetime.date(2026, 9, 26))


class TestDieUeberschneidung(unittest.TestCase):

    def test_vier_tage_wie_beim_auszug(self):
        """Dieselbe Zahl wie booking_budget.TOLERANZ_TAGE: ein Wochenende
        mit Feiertag."""
        self.assertEqual(fetch_window.OVERLAP_DAYS, 4)
        self.assertEqual(fetch_window.OVERLAP_DAYS,
                         booking_budget.TOLERANZ_TAGE)

    def test_sie_laesst_sich_abschalten(self):
        self.assertEqual(
            fetch_window.start_of_window(
                datetime.date(2026, 9, 30), HEUTE, 90, overlap=0),
            datetime.date(2026, 9, 30))

    def test_die_regel_braucht_keine_bench(self):
        quelle = _lies("utils", "fetch_window.py")
        self.assertNotIn("import frappe", quelle)


class TestDerAbrufFragtSie(unittest.TestCase):

    def setUp(self):
        self.quelle = _lies("utils", "import_bank_transaction.py")
        self.rumpf = self.quelle.split(
            "def resolve_incremental_from_date(")[1].split("\ndef ")[0]

    def test_das_fenster_kommt_aus_fetch_window(self):
        self.assertIn("fetch_window.start_of_window(", self.rumpf)
        self.assertIn("from kefiya.utils import fetch_window", self.quelle)

    def test_nur_uebermittelte_zeilen_bestimmen_den_anfang(self):
        """Ein Entwurf oder eine stornierte Zeile darf den naechsten Abruf
        nicht verschieben."""
        self.assertIn('"docstatus": 1', self.rumpf)

    def test_kein_zurueckgebliebener_import(self):
        """Der Umzug, bei dem Importe zurueckbleiben -- diesmal andersherum:
        add_days wurde hier nicht mehr gebraucht."""
        self.assertNotIn("add_days", self.quelle)
