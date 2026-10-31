# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Nach einer Freigabe bekommt der Nutzer Luft.

Gemessen am 28.09.2026 am Sammelabruf von Hand, 12:15 bis 12:19. Dreissig
Volksbank-Konten haengen an EINEM Zugang und laufen als EINE Kette; vierzehn
waren an der Reihe, sieben haben eine Freigabe in der App verlangt:

    12:15:22  Konto 1
    12:15:46  Konto 2    24 s spaeter
    12:17:02  Konto 3    76 s
    12:17:14  Konto 4    12 s
    12:17:29  Konto 5    15 s
    12:17:55  Konto 6    26 s
    12:19:25  Konto 7    90 s

Die Abstaende sind die Reaktionszeit des Nutzers, nicht die der Bank: kefiya
wartet auf die Freigabe und geht in derselben Sekunde zum naechsten Konto,
das sofort die naechste Anfrage stellt. Zwoelf Sekunden zwischen zwei
Anfragen, waehrend das Telefon noch in der Hand liegt.

Drei Dinge haelt dieser Test fest:

  * Gewartet wird nur, wo wirklich freigegeben wurde. Ein Lauf ohne
    Freigabe -- der naechtliche zum Beispiel, der alle dreissig Konten in
    Sekunden holt -- haelt nirgends an.
  * Im Zweifel wird nicht gewartet. Eine Pause zu wenig kostet
    Bequemlichkeit, eine Pause zu viel kostet einen Abruf, der in der
    Job-Zeit nicht mehr fertig wird.
  * Ausgelassen wird nichts. "Wenn ich den Abruf wieder will, soll er
    ausgefuehrt werden."
"""

import datetime
import os
import unittest

from kefiya.utils import release_pacing

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

JETZT = datetime.datetime(2026, 9, 28, 12, 17, 14)


class TestWieLangeLuft(unittest.TestCase):

    def test_frisch_freigegeben_heisst_warten(self):
        """12:17:02 freigegeben, 12:17:14 stuende das naechste Konto an --
        also bleiben von den 45 Sekunden noch 33."""
        self.assertEqual(
            release_pacing.seconds_to_wait(
                datetime.datetime(2026, 9, 28, 12, 17, 2), JETZT),
            33.0)

    def test_laenger_her_heisst_weiter(self):
        self.assertEqual(
            release_pacing.seconds_to_wait(
                datetime.datetime(2026, 9, 28, 12, 15, 0), JETZT),
            0.0)
        self.assertEqual(
            release_pacing.seconds_to_wait(
                datetime.datetime(2026, 9, 28, 12, 16, 29), JETZT),
            0.0)

    def test_ohne_freigabe_keine_pause(self):
        """Der naechtliche Lauf holt alle dreissig Konten ohne eine einzige
        Freigabe. Er darf davon nicht langsamer werden."""
        self.assertEqual(release_pacing.seconds_to_wait(None, JETZT), 0.0)
        self.assertEqual(release_pacing.seconds_to_wait("", JETZT), 0.0)

    def test_unlesbares_heisst_nicht_warten(self):
        self.assertEqual(release_pacing.seconds_to_wait("gestern", JETZT), 0.0)
        self.assertEqual(release_pacing.seconds_to_wait(object(), JETZT), 0.0)

    def test_eine_uhr_die_zurueckspringt_verdoppelt_nichts(self):
        """Ein Zeitpunkt in der Zukunft ergibt null und nicht das Doppelte
        der Pause."""
        self.assertEqual(
            release_pacing.seconds_to_wait(
                datetime.datetime(2026, 9, 28, 12, 20, 0), JETZT),
            0.0)

    def test_pause_null_schaltet_ab(self):
        self.assertEqual(
            release_pacing.seconds_to_wait(
                datetime.datetime(2026, 9, 28, 12, 17, 2), JETZT, breather=0),
            0.0)
        self.assertEqual(
            release_pacing.seconds_to_wait(
                datetime.datetime(2026, 9, 28, 12, 17, 2), JETZT,
                breather=None),
            0.0)

    def test_ein_text_aus_der_datenbank_wird_gelesen(self):
        self.assertEqual(
            release_pacing.seconds_to_wait("2026-09-28 12:17:02", JETZT),
            33.0)

    def test_die_pause_ist_nicht_laenger_als_sie_sein_muss(self):
        """Fuenfundvierzig Sekunden: lang genug, um das Telefon wegzulegen,
        kurz genug, dass sieben davon den Lauf um fuenf Minuten verlaengern
        und nicht um eine halbe Stunde."""
        self.assertEqual(release_pacing.BREATHER_SECONDS, 45)
        self.assertLessEqual(7 * release_pacing.BREATHER_SECONDS, 360)


class TestWerDieUhrzeitNotiert(unittest.TestCase):
    """Die beiden Zeilen, die frappe brauchen, holen es erst beim Aufruf --
    sonst liesse sich die Regel oben ohne Bench nicht pruefen."""

    def _quelle(self, *teile):
        with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
            return handle.read()

    def test_das_modul_laedt_ohne_bench(self):
        quelle = self._quelle("utils", "release_pacing.py")
        kopf = quelle.split("import datetime")[0]
        self.assertNotIn("import frappe", kopf)

    def test_merken_wirft_nie(self):
        """Der Abruf, den die Freigabe eben freigeschaltet hat, darf nicht
        daran scheitern, dass die Uhrzeit nicht notiert werden konnte."""
        release_pacing.note_release()          # ohne Site: faellt still durch
        self.assertIsNone(release_pacing.last_release())

    def test_die_tan_strecke_notiert_die_freigabe(self):
        quelle = self._quelle("utils", "fints_tan_session.py")
        self.assertIn("release_pacing.note_release()", quelle)
        self.assertIn("from kefiya.utils import release_pacing", quelle)
        # Und zwar auf dem Ast, auf dem die Freigabe angekommen ist.
        warten = quelle.split("def _await_release(")[1].split("\n    def ")[0]
        self.assertIn("release_pacing.note_release()", warten)

    def test_die_kette_laesst_luft(self):
        quelle = self._quelle("utils", "client.py")
        gruppe = quelle.split("def fetch_group(")[1].split("\n@frappe.whitelist")[0]
        self.assertIn("release_pacing.seconds_to_wait(", gruppe)
        self.assertIn("time.sleep(", gruppe)
        # time muss importiert sein -- genau der Import, der beim Verschieben
        # von Code zurueckbleibt.
        self.assertIn("\nimport time\n", quelle)

    def test_der_einzelabruf_wird_nicht_ausgelassen(self):
        """"Wenn ich den Abruf wieder will, soll er ausgefuehrt werden."
        Die Pause verzoegert, sie ueberspringt nicht."""
        quelle = self._quelle("utils", "client.py")
        gruppe = quelle.split("def fetch_group(")[1].split("\n@frappe.whitelist")[0]
        luft = gruppe.split("luft = ")[1].split("try:")[0]
        self.assertNotIn("continue", luft)


class TestWieLangeAufEineFreigabeGewartetWird(unittest.TestCase):

    def _quelle(self, *teile):
        with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
            return handle.read()

    def test_fuenf_minuten_statt_zwei(self):
        """Zwei Minuten waren zu knapp, sobald ein Lauf mehrere Freigaben
        verlangt: sieben davon in vier Minuten, und wer dazwischen das
        Telefon sucht, hat die zweite verpasst. Eine verpasste Freigabe
        kostet den ganzen Zugang -- die Kette haelt an."""
        quelle = self._quelle("utils", "fints_tan_session.py")
        self.assertIn("DECOUPLED_MAX_WAIT_SECONDS = 300", quelle)

    def test_nicht_ueber_die_obergrenze_hinaus(self):
        from kefiya.utils import decoupled_budget

        quelle = self._quelle("utils", "fints_tan_session.py")
        zahl = int(quelle.split("DECOUPLED_MAX_WAIT_SECONDS = ")[1]
                         .split("\n")[0].strip())
        self.assertLessEqual(
            zahl, decoupled_budget.DECOUPLED_WAIT_CEILING_SECONDS)
