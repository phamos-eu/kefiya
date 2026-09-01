# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der Kontoauszug als Schiedsrichter -- und wer den Schiedsrichter prueft.

Die Zahlen hier sind nicht erfunden. Sie stammen aus den Auszuegen der
Instanz, und zwei davon haben einen Fehler aufgedeckt, den keine
Buchungspruefung finden konnte:

    :61:2302010221RDR50,00       Storno einer Sollbuchung
    :61:1401280128RCR400000,00   Storno einer Habenbuchung

MT940 kennt vier Kennzeichen: C (Haben), D (Soll) und ihre Stornos RC und
RD. Ein Storno geht in die Gegenrichtung seiner Buchung. Zwei Stellen haben
das nicht gewusst:

    1. Die Bibliothek mt940 rechnet ``if status == 'D': amount = -amount``.
       Damit bleiben RC UND RD positiv. Bei RD stimmt das zufaellig, bei RC
       ist es falsch -- 400.000 EUR falsch, in einem einzigen Fall.
    2. import_bank_transaction verwarf jedes Kennzeichen ausser c und d.
       In den Auszuegen dieser Instanz sind das 32 Buchungen ueber
       866.612,58 EUR, die nie im System ankamen.

Beide sind an der Saldodifferenz der Bank aufgefallen, nicht an einer
Zaehlung. Deshalb steht in diesem Modul auch der Test, dass die Pruefung
nach dem SALDO fragt und nicht nach der Anzahl.
"""

import os
import unittest
from decimal import Decimal

from kefiya.utils.auszug_pruefung import (VORZEICHEN, abschnitte, blaetter,
                                          blinde_jahre, kette, summenprobe,
                                          urteil, vergleiche)

HIER = os.path.dirname(os.path.abspath(__file__))

#: Ein Auszug mit zwei Blaettern, die aneinanderpassen. Betraege und Aufbau
#: sind den echten .sta-Dateien nachgebildet.
ZWEI_BLAETTER = (
    ":20:REFSTARMONEY\n"
    ":25:67250020/9219498\n"
    ":28C:00000\n"
    ":60F:C231230EUR1000,00\n"
    ":61:2401020102CR250,00NMSCNONREF\n"
    ":86:051?00UEBERWEISUNGSGUTSCHRIFT\n"
    ":61:2401050105D100,00NMSCNONREF\n"
    ":86:020?00AUFTRAG\n"
    ":62F:C241230EUR1150,00\n"
    "-\n"
    ":20:REFSTARMONEY\n"
    ":25:67250020/9219498\n"
    ":28C:00000\n"
    ":60F:C241230EUR1150,00\n"
    ":61:2501030103D50,00NMSCNONREF\n"
    ":86:020?00AUFTRAG\n"
    ":62F:C251230EUR1100,00\n"
    "-\n"
)


#: Derselbe Auszug, aber das zweite Blatt steht 750,00 hoeher -- Anfangs- UND
#: Schlusssaldo verschoben. Damit bricht die Kette, und nur sie: das Blatt
#: geht in sich weiter auf. Wer nur :60F: verschiebt, bricht auch die
#: Summenprobe und prueft dann zwei Dinge auf einmal.
KETTE_GEBROCHEN = (ZWEI_BLAETTER
                   .replace(":60F:C241230EUR1150,00", ":60F:C241230EUR1900,00")
                   .replace(":62F:C251230EUR1100,00", ":62F:C251230EUR1850,00"))


def _ein_blatt(zeilen, anfang="C231230EUR1000,00", ende="C241230EUR1000,00"):
    return (":20:REF\n:25:67250020/9219498\n:28C:00000\n"
            ":60F:{0}\n{1}:62F:{2}\n-\n".format(anfang, "".join(zeilen), ende))


class TestVorzeichen(unittest.TestCase):
    """Vier Kennzeichen, vier Richtungen -- und zwei davon drehen um."""

    def test_die_vier_kennzeichen(self):
        self.assertEqual(VORZEICHEN["C"], 1)
        self.assertEqual(VORZEICHEN["D"], -1)

    def test_ein_storno_geht_in_die_gegenrichtung(self):
        """RC storniert eine Gutschrift, das Geld geht also hinaus."""
        self.assertEqual(VORZEICHEN["RC"], -VORZEICHEN["C"])
        self.assertEqual(VORZEICHEN["RD"], -VORZEICHEN["D"])

    def test_es_gibt_genau_diese_vier(self):
        """Ein fuenftes Kennzeichen waere ein stiller Verlust: der Import
        verwirft, was er nicht kennt. Faellt dieser Test, ist zu entscheiden,
        was das neue Kennzeichen bedeutet -- nicht, es durchzuwinken."""
        self.assertEqual(set(VORZEICHEN), {"C", "D", "RC", "RD"})


class TestDasBlattLesen(unittest.TestCase):

    def test_anfang_ende_und_summe(self):
        eins, zwei = blaetter(ZWEI_BLAETTER)
        self.assertEqual(eins.konto, "9219498")
        self.assertEqual(eins.anfang_tag, "2023-12-30")
        self.assertEqual(eins.ende_tag, "2024-12-30")
        self.assertEqual(eins.anfang, Decimal("1000.00"))
        self.assertEqual(eins.ende, Decimal("1150.00"))
        self.assertEqual(eins.summe, Decimal("150.00"))
        self.assertEqual(eins.zeilen, 2)
        self.assertEqual(zwei.summe, Decimal("-50.00"))

    def test_ein_storno_zaehlt_mit_seinem_eigenen_vorzeichen(self):
        """Die Zeile, an der es aufgefallen ist -- ohne die 400.000."""
        auszug = _ein_blatt([":61:2302010221RDR50,00NMSCNONREF\n"],
                            ende="C241230EUR1050,00")
        blatt, = blaetter(auszug)
        self.assertEqual(blatt.summe, Decimal("50.00"))
        self.assertEqual(summenprobe([blatt]), [])

    def test_storno_einer_gutschrift_nimmt_geld_weg(self):
        auszug = _ein_blatt([":61:1401280128RCR400000,00NMSCNONREF\n"],
                            anfang="C231230EUR400000,00",
                            ende="C241230EUR0,00")
        blatt, = blaetter(auszug)
        self.assertEqual(blatt.summe, Decimal("-400000.00"))
        self.assertEqual(summenprobe([blatt]), [])

    def test_mehrere_konten_in_einer_datei(self):
        """Die Sammelexporte enthalten neun Konten. Jedes Blatt traegt sein
        eigenes, sonst zaehlt eine Datei fremdes Geld mit."""
        zwei = ZWEI_BLAETTER + (
            ":20:REF\n:25:67250020/9280049\n:28C:00000\n"
            ":60F:C231230EUR0,00\n:61:2401020102C10,00NMSCNONREF\n"
            ":62F:C241230EUR10,00\n-\n")
        konten = {blatt.konto for blatt in blaetter(zwei)}
        self.assertEqual(konten, {"9219498", "9280049"})


class TestFrageEinsDieKette(unittest.TestCase):

    def test_lueckenlos_meldet_nichts(self):
        self.assertEqual(kette(blaetter(ZWEI_BLAETTER)), [])

    def test_ein_sprung_wird_benannt(self):
        kaputt = ZWEI_BLAETTER.replace(":60F:C241230EUR1150,00",
                                       ":60F:C241230EUR1900,00")
        brueche = kette(blaetter(kaputt))
        self.assertEqual(len(brueche), 1)
        self.assertEqual(brueche[0].erwartet, Decimal("1150.00"))
        self.assertEqual(brueche[0].gefunden, Decimal("1900.00"))
        self.assertEqual(brueche[0].fehlt, Decimal("750.00"))


class TestFrageZweiDieSummenprobe(unittest.TestCase):

    def test_ein_vollstaendiges_blatt_geht_auf(self):
        self.assertEqual(summenprobe(blaetter(ZWEI_BLAETTER)), [])

    def test_eine_fehlende_buchung_faellt_auf(self):
        """Die Kette haelt, das Blatt nicht -- genau der Fall, den Frage 1
        allein nicht sieht."""
        ohne = ZWEI_BLAETTER.replace(
            ":61:2401050105D100,00NMSCNONREF\n:86:020?00AUFTRAG\n", "")
        self.assertEqual(kette(blaetter(ohne)), [])
        daneben = summenprobe(blaetter(ohne))
        self.assertEqual(len(daneben), 1)
        self.assertEqual(daneben[0].fehlt, Decimal("-100.00"))

    def test_das_blinde_jahr_wird_benannt(self):
        ohne = ZWEI_BLAETTER.replace(
            ":61:2401050105D100,00NMSCNONREF\n:86:020?00AUFTRAG\n", "")
        self.assertEqual(blinde_jahre(summenprobe(blaetter(ohne))), {"2024"})


class TestFrageDreiBankGegenSystem(unittest.TestCase):

    def test_stimmt_die_buchhaltung_meldet_sie_nichts(self):
        gelesen = blaetter(ZWEI_BLAETTER)
        bewegung = {("2023-12-30", "2024-12-30"): Decimal("150.00"),
                    ("2024-12-30", "2025-12-30"): Decimal("-50.00")}
        self.assertEqual(
            vergleiche(gelesen, lambda a, b: bewegung[(a, b)]), [])

    def test_eine_fehlende_buchung_im_system(self):
        gelesen = blaetter(ZWEI_BLAETTER)
        bewegung = {("2023-12-30", "2024-12-30"): Decimal("50.00"),
                    ("2024-12-30", "2025-12-30"): Decimal("-50.00")}
        abweichungen = vergleiche(gelesen, lambda a, b: bewegung[(a, b)])
        self.assertEqual(len(abweichungen), 1)
        self.assertEqual(abweichungen[0].fehlt, Decimal("100.00"))
        self.assertEqual(abweichungen[0].bank, Decimal("150.00"))
        self.assertEqual(abweichungen[0].system, Decimal("50.00"))

    def test_ein_blindes_jahr_wird_nicht_beurteilt(self):
        """Ein loechriger Auszug darf keine richtige Buchhaltung anklagen."""
        ohne = ZWEI_BLAETTER.replace(
            ":61:2401050105D100,00NMSCNONREF\n:86:020?00AUFTRAG\n", "")
        gelesen = blaetter(ohne)
        bewegung = {("2023-12-30", "2024-12-30"): Decimal("150.00"),
                    ("2024-12-30", "2025-12-30"): Decimal("-50.00")}
        self.assertEqual(
            vergleiche(gelesen, lambda a, b: bewegung[(a, b)],
                       ausser={"2024"}),
            [])

    def test_gefragt_wird_nach_dem_saldo_nicht_nach_der_anzahl(self):
        """Die Zaehlung hat sich geirrt, der Saldo nicht.

        Ein Auszug mit weniger Zeilen als das System kann trotzdem stimmen.
        Die Pruefung darf daran nichts festmachen -- sie fragt nach der
        Bewegung, und die Zeilenzahl kommt in vergleiche() gar nicht vor.
        """
        gelesen = blaetter(ZWEI_BLAETTER)
        gerufen = []

        def bewegung(a, b):
            gerufen.append((a, b))
            return Decimal("150.00") if a == "2023-12-30" \
                else Decimal("-50.00")

        self.assertEqual(vergleiche(gelesen, bewegung), [])
        self.assertEqual(gerufen, [("2023-12-30", "2024-12-30"),
                                   ("2024-12-30", "2025-12-30")])


class TestDasUrteil(unittest.TestCase):

    def test_ein_bruch_teilt_den_auszug_statt_ihn_zu_verwerfen(self):
        """Der Anlass steht in abschnitte(): der Auszug des groessten Kontos
        hat genau einen Bruch, zwischen einem Fragment von 2004 und dem Jahr
        2005. Wer daraufhin alles verwirft, wirft 21 lueckenlose Jahre weg.
        """
        gelesen = blaetter(KETTE_GEBROCHEN)
        self.assertEqual(len(abschnitte(gelesen)), 2)
        self.assertEqual(summenprobe(gelesen), [],
                         "Diese Vorlage bricht nur die Kette.")

        gefaellt = urteil(gelesen, lambda a, b: Decimal("150.00")
                          if a == "2023-12-30" else Decimal("-50.00"))
        self.assertTrue(gefaellt["brauchbar"])
        self.assertEqual(len(gefaellt["kette"]), 1)
        # Beide Abschnitte sprechen -- jeder fuer seinen eigenen Zeitraum.
        self.assertEqual(gefaellt["spricht_fuer"],
                         [("2023-12-30", "2024-12-30"),
                          ("2024-12-30", "2025-12-30")])
        self.assertEqual(gefaellt["abweichungen"], [])

    def test_der_bruch_selbst_wird_nicht_ueberbrueckt(self):
        """Ueber die Sprungstelle hinweg wird nichts verrechnet: das zweite
        Blatt beginnt mit dem Saldo, den es nennt, nicht mit dem, der
        vorher stand."""
        gefaellt = urteil(blaetter(KETTE_GEBROCHEN),
                          lambda a, b: Decimal("150.00")
                          if a == "2023-12-30" else Decimal("-50.00"))
        # Der Sprung von 750,00 taucht als Bruch auf und NICHT als
        # Abweichung der Buchhaltung -- die hat damit nichts zu tun.
        self.assertEqual(gefaellt["kette"][0].fehlt, Decimal("750.00"))
        self.assertEqual(gefaellt["abweichungen"], [])

    def test_ohne_beurteilbaren_abschnitt_schweigt_das_urteil(self):
        """Ein leeres "abweichungen" heisst nicht Freispruch. spricht_fuer
        sagt, ob ueberhaupt geprueft wurde."""
        ohne = ZWEI_BLAETTER.replace(
            ":61:2401050105D100,00NMSCNONREF\n:86:020?00AUFTRAG\n", "")
        ohne = ohne.replace(
            ":61:2501030103D50,00NMSCNONREF\n:86:020?00AUFTRAG\n", "")
        gefaellt = urteil(blaetter(ohne), lambda a, b: Decimal("0.00"))
        self.assertEqual(gefaellt["spricht_fuer"], [])
        self.assertFalse(gefaellt["brauchbar"])
        self.assertEqual(gefaellt["abweichungen"], [])

    def test_ein_sauberer_auszug_urteilt(self):
        gefaellt = urteil(blaetter(ZWEI_BLAETTER),
                          lambda a, b: Decimal("150.00")
                          if a == "2023-12-30" else Decimal("-50.00"))
        self.assertTrue(gefaellt["brauchbar"])
        self.assertEqual(gefaellt["kette"], [])
        self.assertEqual(gefaellt["summenprobe"], [])
        self.assertEqual(gefaellt["blind"], [])
        self.assertEqual(gefaellt["abweichungen"], [])
        self.assertEqual(gefaellt["spricht_fuer"],
                         [("2023-12-30", "2025-12-30")])

    def test_ein_lueckenloser_auszug_ist_ein_abschnitt(self):
        self.assertEqual(len(abschnitte(blaetter(ZWEI_BLAETTER))), 1)

    def test_ohne_blaetter_kein_abschnitt(self):
        self.assertEqual(abschnitte([]), [])


class TestDerImportVerwirftKeinStornoMehr(unittest.TestCase):
    """Ein Quelltext-Test, und er weiss, dass er einer ist.

    Der Import braucht frappe und laeuft hier nicht. Was hier geprueft wird,
    ist deshalb nur, dass die Entscheidung ueber die Richtung aus derselben
    Tabelle kommt wie oben -- nicht, dass sie zur Laufzeit greift. Die
    Tabelle selbst wird oben wirklich ausgefuehrt.
    """

    def _quelltext(self):
        pfad = os.path.join(os.path.dirname(os.path.dirname(HIER)),
                            "utils", "import_bank_transaction.py")
        with open(pfad, encoding="utf-8") as handle:
            return handle.read()

    def test_der_waechter_kennt_die_stornos(self):
        text = self._quelltext()
        self.assertNotIn("if status not in ['c', 'd']:", text,
                         "Dieser Waechter hat jedes Storno verworfen.")
        self.assertIn("if status not in RICHTUNG:", text)

    def test_die_richtung_kommt_aus_der_einen_tabelle(self):
        text = self._quelltext()
        self.assertIn("from kefiya.utils.auszug_pruefung import VORZEICHEN",
                      text)
        self.assertIn("if RICHTUNG[status] > 0:", text)

    def test_kleingeschrieben_wie_der_parser_sie_liefert(self):
        """RICHTUNG wird aus VORZEICHEN abgeleitet; der Parser gibt
        kleingeschriebene Kennzeichen. Beides muss zusammenpassen, sonst
        verwirft der Waechter wieder alles."""
        abgeleitet = {k.lower(): v for k, v in VORZEICHEN.items()}
        self.assertEqual(set(abgeleitet), {"c", "d", "rc", "rd"})
        self.assertEqual(abgeleitet["rd"], 1)
        self.assertEqual(abgeleitet["rc"], -1)
