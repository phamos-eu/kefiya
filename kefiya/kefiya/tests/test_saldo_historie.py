# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Mit einem Saldo laesst sich nichts vergleichen.

store_balance() schrieb den HKSAL-Saldo auf das Bankkonto und ueberschrieb
ihn bei jedem Abruf; das von der Bank mitgelieferte ``balance_date`` wurde
gar nicht erst gespeichert. Damit stand genau ein Wert ohne Datum da.

Diese Tests halten die Reihe fest, aus der die Differenz spaeter kommt --
und vor allem die Falle, in die ein naiver Fortschreiber laeuft.
"""

import datetime
import pathlib
import unittest

from kefiya.utils import saldo_historie

HIER = pathlib.Path(__file__).resolve()
WURZEL = HIER.parents[3]


def stand(balance_at=None, as_of=None, previous=None, previous_as_of=None):
    return {"balance_at": balance_at, "as_of": as_of,
            "previous": previous, "previous_as_of": previous_as_of}


class TestDerErsteStand(unittest.TestCase):

    def test_er_wird_zum_anfang_der_reihe(self):
        self.assertEqual(
            saldo_historie.naechster_stand(stand(), 1000.0, "2026-10-01"),
            {"as_of": "2026-10-01", "balance_at": 1000.0})

    def test_und_bildet_noch_kein_intervall(self):
        neu = saldo_historie.naechster_stand(stand(), 1000.0, "2026-10-01")
        self.assertIsNone(saldo_historie.differenz(neu))
        self.assertEqual(saldo_historie.fenster(neu), (None, None))


class TestZweimalAmSelbenTag(unittest.TestCase):
    """Die Falle. Die Konten werden mehrmals taeglich abgerufen -- der
    Zeitplan nachts, der Sammelabruf, ein Abruf von Hand. Wer bei jedem
    Abruf den bisherigen Stand nach hinten schiebt, macht beim zweiten Abruf
    eines Tages 'vorher' und 'jetzt' gleich. Die Differenz waere dann fuer
    immer 0,00 EUR, und zwar ohne dass es auffaellt."""

    def test_das_intervall_bleibt_stehen(self):
        vorhanden = stand(balance_at=1200.0, as_of="2026-10-01",
                          previous=1000.0, previous_as_of="2026-09-28")
        weiter = saldo_historie.naechster_stand(vorhanden, 1250.0, "2026-10-01")
        self.assertNotIn("previous", weiter)
        self.assertNotIn("previous_as_of", weiter)

    def test_der_heutige_stand_wird_nachgefuehrt(self):
        vorhanden = stand(balance_at=1200.0, as_of="2026-10-01",
                          previous=1000.0, previous_as_of="2026-09-28")
        weiter = saldo_historie.naechster_stand(vorhanden, 1250.0, "2026-10-01")
        self.assertEqual(weiter["balance_at"], 1250.0)
        self.assertEqual(weiter["as_of"], "2026-10-01")

    def test_und_die_differenz_bleibt_eine_echte(self):
        vorhanden = stand(balance_at=1200.0, as_of="2026-10-01",
                          previous=1000.0, previous_as_of="2026-09-28")
        weiter = saldo_historie.naechster_stand(vorhanden, 1250.0, "2026-10-01")
        vorhanden.update(weiter)
        self.assertEqual(saldo_historie.differenz(vorhanden), 250.0)
        self.assertEqual(saldo_historie.fenster(vorhanden),
                         ("2026-09-28", "2026-10-01"))


class TestEinNeuerTagSchiebt(unittest.TestCase):

    def test_der_bisherige_stand_wird_zum_vorherigen(self):
        vorhanden = stand(balance_at=1000.0, as_of="2026-09-28")
        weiter = saldo_historie.naechster_stand(vorhanden, 1200.0, "2026-10-01")
        self.assertEqual(weiter, {"as_of": "2026-10-01", "balance_at": 1200.0,
                                  "previous_as_of": "2026-09-28",
                                  "previous": 1000.0})

    def test_und_das_fenster_spannt_sich_auf(self):
        vorhanden = stand(balance_at=1000.0, as_of="2026-09-28")
        vorhanden.update(
            saldo_historie.naechster_stand(vorhanden, 1200.0, "2026-10-01"))
        self.assertEqual(saldo_historie.differenz(vorhanden), 200.0)
        self.assertEqual(saldo_historie.fenster(vorhanden),
                         ("2026-09-28", "2026-10-01"))

    def test_auch_eine_abnahme(self):
        vorhanden = stand(balance_at=1000.0, as_of="2026-09-28")
        vorhanden.update(
            saldo_historie.naechster_stand(vorhanden, 760.0, "2026-10-01"))
        self.assertEqual(saldo_historie.differenz(vorhanden), -240.0)


class TestEineAntwortDieZurueckfaellt(unittest.TestCase):
    """Eine aeltere Antwort darf die Reihe nicht rueckwaerts drehen."""

    def test_sie_aendert_nichts(self):
        vorhanden = stand(balance_at=1200.0, as_of="2026-10-01",
                          previous=1000.0, previous_as_of="2026-09-28")
        self.assertEqual(
            saldo_historie.naechster_stand(vorhanden, 900.0, "2026-09-20"), {})


class TestOhneDatumKeineReihe(unittest.TestCase):
    """Ein Saldo ohne Datum laesst sich nicht einordnen. Er wird weiterhin
    als aktueller Saldo geschrieben -- das tut store_balance() --, aber er
    wird keine Geschichte."""

    def test_kein_datum(self):
        self.assertEqual(
            saldo_historie.naechster_stand(stand(), 1000.0, None), {})
        self.assertEqual(
            saldo_historie.naechster_stand(stand(), 1000.0, ""), {})

    def test_kein_saldo(self):
        self.assertEqual(
            saldo_historie.naechster_stand(stand(), None, "2026-10-01"), {})

    def test_ein_saldo_von_null_ist_ein_saldo(self):
        self.assertEqual(
            saldo_historie.naechster_stand(stand(), 0.0, "2026-10-01"),
            {"as_of": "2026-10-01", "balance_at": 0.0})


class TestDatumInJederGestalt(unittest.TestCase):
    """Die Bank liefert ein date, die Datenbank eine Zeichenkette, python
    -fints gelegentlich ein datetime."""

    def test_date(self):
        weiter = saldo_historie.naechster_stand(
            stand(), 10.0, datetime.date(2026, 10, 1))
        self.assertEqual(weiter["as_of"], "2026-10-01")

    def test_datetime(self):
        weiter = saldo_historie.naechster_stand(
            stand(), 10.0, datetime.datetime(2026, 10, 1, 6, 1, 2))
        self.assertEqual(weiter["as_of"], "2026-10-01")

    def test_zeichenkette_mit_uhrzeit(self):
        weiter = saldo_historie.naechster_stand(
            stand(), 10.0, "2026-10-01 06:01:02")
        self.assertEqual(weiter["as_of"], "2026-10-01")

    def test_gemischt_vergleicht_richtig(self):
        """Gespeichert als Zeichenkette, geliefert als date -- und trotzdem
        derselbe Tag."""
        vorhanden = stand(balance_at=1000.0, as_of="2026-10-01")
        weiter = saldo_historie.naechster_stand(
            vorhanden, 1100.0, datetime.date(2026, 10, 1))
        self.assertNotIn("previous", weiter)


class TestDieDifferenzIstNurMitIntervallEchtt(unittest.TestCase):

    def test_ohne_vorherigen_stand_keine_differenz(self):
        self.assertIsNone(saldo_historie.differenz(
            stand(balance_at=1000.0, as_of="2026-10-01")))

    def test_ohne_datum_des_vorherigen_auch_nicht(self):
        self.assertIsNone(saldo_historie.differenz(
            stand(balance_at=1200.0, as_of="2026-10-01", previous=1000.0)))

    def test_leeres_bleibt_leer(self):
        self.assertIsNone(saldo_historie.differenz(None))
        self.assertIsNone(saldo_historie.differenz({}))
        self.assertEqual(saldo_historie.fenster(None), (None, None))


class TestDerLaufendeSaldoIstKeinZeuge(unittest.TestCase):
    """Der bank_balance an jeder Buchung wird von apply_running_balance()
    rueckwaerts aus genau diesem HKSAL-Saldo errechnet. Ein Abgleich dagegen
    geht per Konstruktion immer auf -- darauf bin ich am 30.09.2026
    hereingefallen."""

    def test_das_modul_sagt_es(self):
        self.assertIn("apply_running_balance", saldo_historie.__doc__)
        self.assertIn("zirkul", saldo_historie.__doc__)

    def test_und_rechnet_nur_mit_bankwerten(self):
        quelle = (WURZEL / "kefiya/utils/saldo_historie.py").read_text(
            encoding="utf-8")
        anfang = quelle.index("def differenz")
        ende = quelle.index("def fenster")
        self.assertNotIn("bank_balance", quelle[anfang:ende])


class TestDieBuergschaftBekommtKeineReihe(unittest.TestCase):
    """Eine Buergschaft nennt die eingeraeumte Linie, keinen Kontostand.
    Eine Differenz darauf hiesse nichts."""

    def test_store_balance_schliesst_sie_aus(self):
        quelle = (WURZEL / "kefiya/utils/fetch_persistence.py").read_text(
            encoding="utf-8")
        stelle = quelle.index("saldo_historie.naechster_stand")
        davor = quelle[max(0, stelle - 900):stelle]
        self.assertIn("not is_a_line", davor)

    def test_und_das_fehlende_feld_wird_geprueft(self):
        quelle = (WURZEL / "kefiya/utils/fetch_persistence.py").read_text(
            encoding="utf-8")
        stelle = quelle.index("saldo_historie.naechster_stand")
        herum = quelle[max(0, stelle - 900):stelle + 1200]
        self.assertIn('meta.has_field("custom_balance_as_of")', herum)


if __name__ == "__main__":
    unittest.main()
