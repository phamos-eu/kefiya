# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der Kontoauszug als Schiedsrichter -- und wer den Schiedsrichter prueft.

Die Zahlen hier sind nicht erfunden. Sie stammen aus den Auszuegen der
Instanz, und zwei davon haben einen Fehler aufgedeckt, den keine
Buchungspruefung finden konnte:

    :61:2302010221RDR50,00       Storno einer Sollbuchung
    :61:1401280128RCR400000,00   Storno einer Habenbuchung

MT940 kennt vier Kennzeichen: C (Haben), D (Soll) und ihre Stornos RC und
RD. Ein Storno geht in die Gegenrichtung seiner Buchung. Drei Stellen haben
das nicht gewusst:

    1. Die Bibliothek mt940 rechnet ``if status == 'D': amount = -amount``.
       Damit bleiben RC UND RD positiv. Bei RD stimmt das zufaellig, bei RC
       ist es falsch -- 400.000 EUR falsch, in einem einzigen Fall.
    2. import_bank_transaction verwarf jedes Kennzeichen ausser c und d.
       In den Auszuegen dieser Instanz sind das 32 Buchungen ueber
       866.612,58 EUR, die nie im System ankamen.
    3. statement_formats.mt940_entries -- der Weg, auf dem eine .sta-Datei
       eingelesen wird -- nahm den Betrag unbesehen aus der Bibliothek und
       erbte damit deren Vorzeichenfehler. Das ist der gefaehrlichste der
       drei: er verwirft nichts, er bucht in die falsche Richtung. Auf
       Konto 507 waeren beim Neueinlesen 410.000 EUR als Eingang gelandet,
       die Ausgaenge sind -- eine Verschiebung von 820.000 EUR.

Beide sind an der Saldodifferenz der Bank aufgefallen, nicht an einer
Zaehlung. Deshalb steht in diesem Modul auch der Test, dass die Pruefung
nach dem SALDO fragt und nicht nach der Anzahl.
"""

import os
import unittest
from decimal import Decimal

from kefiya.utils.auszug_pruefung import (VORZEICHEN, abschnitte, bewegung_aus,
                                          blaetter, blinde_jahre, fenster,
                                          kette, summenprobe, urteil,
                                          vergleiche)

HIER = os.path.dirname(os.path.abspath(__file__))

#: Ein Auszug mit zwei Blaettern, die aneinanderpassen. Betraege und Aufbau
#: sind den echten .sta-Dateien nachgebildet.
ZWEI_BLAETTER = (
    ":20:REFSTARMONEY\n"
    ":25:99999999/9219498\n"
    ":28C:00000\n"
    ":60F:C231230EUR1000,00\n"
    ":61:2401020102CR250,00NMSCNONREF\n"
    ":86:051?00UEBERWEISUNGSGUTSCHRIFT\n"
    ":61:2401050105D100,00NMSCNONREF\n"
    ":86:020?00AUFTRAG\n"
    ":62F:C241230EUR1150,00\n"
    "-\n"
    ":20:REFSTARMONEY\n"
    ":25:99999999/9219498\n"
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
    return (":20:REF\n:25:99999999/9219498\n:28C:00000\n"
            ":60F:{0}\n{1}:62F:{2}\n-\n".format(anfang, "".join(zeilen), ende))


#: So datiert StarMoney: der Anfangssaldo traegt den Tag der ERSTEN
#: Buchung, nicht den Vortag. Nachgebildet dem Auszug von 7777777,
#: ``:60F:C210104EUR1991,03`` mit sechs Buchungen vom 04.01.2021 darunter.
STARMONEY_DATIERT = (
    ":20:REFSTARMONEY\n"
    ":25:88888888/7777777\n"
    ":28C:00000/001\n"
    ":60F:C210104EUR1000,00\n"
    ":61:2101040104CR250,00NMSCNONREF\n"
    ":86:051?00GUTSCHRIFT\n"
    ":61:2101040104D100,00NMSCNONREF\n"
    ":86:020?00AUFTRAG\n"
    ":61:2103150315D50,00NMSCNONREF\n"
    ":86:020?00AUFTRAG\n"
    ":62F:C211230EUR1100,00\n"
    "-\n"
    ":20:REFSTARMONEY\n"
    ":25:88888888/7777777\n"
    ":28C:00000/001\n"
    ":60F:C220103EUR1100,00\n"
    ":61:2201030103D100,00NMSCNONREF\n"
    ":86:020?00AUFTRAG\n"
    ":62F:C221230EUR1000,00\n"
    "-\n"
)

#: Ein Blatt in zwei Bloecken: der erste endet mit einem Zwischensaldo
#: (:62M:), der zweite nimmt ihn mit :60M: auf. So exportiert StarMoney
#: den Jahrgang 2020 von Konto 7777777 -- 221 Buchungen, die vorher
#: verloren gingen.
GETEILTES_BLATT = (
    ":20:REFSTARMONEY\n"
    ":25:88888888/7777777\n"
    ":28C:00000\n"
    ":60F:C200813EUR0,00\n"
    ":61:2008130813CR2230,04NMSCNONREF\n"
    ":86:051?00GUTSCHRIFT\n"
    ":62M:C200813EUR2230,04\n"
    "-\n"
    ":20:REFSTARMONEY\n"
    ":25:88888888/7777777\n"
    ":28C:00000/001\n"
    ":60M:C200813EUR2230,04\n"
    ":61:2012300102D239,01NMSCNONREF\n"
    ":86:020?00AUFTRAG\n"
    ":62F:C201230EUR1991,03\n"
    "-\n"
)


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
            ":20:REF\n:25:99999999/9280049\n:28C:00000\n"
            ":60F:C231230EUR0,00\n:61:2401020102C10,00NMSCNONREF\n"
            ":62F:C241230EUR10,00\n-\n")
        konten = {blatt.konto for blatt in blaetter(zwei)}
        self.assertEqual(konten, {"9219498", "9280049"})


class TestEinGeteiltesBlatt(unittest.TestCase):
    """:62M: ist kein Blattende, :60M: kein Blattanfang."""

    def test_zwei_bloecke_sind_ein_blatt(self):
        blatt, = blaetter(GETEILTES_BLATT)
        self.assertEqual(blatt.anfang_tag, "2020-08-13")
        self.assertEqual(blatt.ende_tag, "2020-12-30")
        self.assertEqual(blatt.zeilen, 2)
        self.assertEqual(blatt.summe, Decimal("1991.03"))
        self.assertEqual(summenprobe([blatt]), [])

    def test_der_zwischensaldo_wird_nachgerechnet_nicht_geglaubt(self):
        """Ein falscher :62M: aendert das Blatt nicht -- die Summenprobe
        ueber das ganze Blatt ist die Pruefung, nicht der Zwischensaldo."""
        gelogen = GETEILTES_BLATT.replace(":62M:C200813EUR2230,04",
                                          ":62M:C200813EUR9999,99")
        blatt, = blaetter(gelogen)
        self.assertEqual(blatt.summe, Decimal("1991.03"))
        self.assertEqual(summenprobe([blatt]), [])

    def test_eine_fortsetzung_ohne_anfang_ist_kein_blatt(self):
        nur_zweiter = GETEILTES_BLATT.split("-\n", 1)[1]
        self.assertEqual(blaetter(nur_zweiter), [])

    def test_der_buchungstag_ueber_den_jahreswechsel(self):
        """Valuta 30.12.2020, gebucht 02.01. -- das ist der 02.01.2021."""
        blatt, = blaetter(GETEILTES_BLATT)
        # erster_tag ist der frueheste Buchungstag, nicht die Valuta.
        self.assertEqual(blatt.erster_tag, "2020-08-13")
        spaet, = blaetter(_ein_blatt([":61:2012300102D239,01NMSCNONREF\n"],
                                     anfang="C201230EUR1000,00",
                                     ende="C210102EUR760,99"))
        self.assertEqual(spaet.erster_tag, "2021-01-02")


class TestDieFenster(unittest.TestCase):
    """von < Buchungstag <= bis -- und der erste Tag geht nicht verloren."""

    def test_ein_blatt_beginnt_wo_das_vorige_endet(self):
        eins, zwei = blaetter(ZWEI_BLAETTER)
        self.assertEqual(fenster([eins, zwei]),
                         [("2023-12-30", "2024-12-30"),
                          ("2024-12-30", "2025-12-30")])

    def test_ein_anfangssaldo_vom_vortag_bleibt_ausgeschlossen(self):
        """ZWEI_BLAETTER datiert den Anfangssaldo auf den 30.12., die erste
        Buchung ist vom 02.01.: das Fenster beginnt hinter dem 30.12."""
        eins, _zwei = blaetter(ZWEI_BLAETTER)
        self.assertEqual(fenster([eins])[0][0], "2023-12-30")

    def test_starmoney_datiert_auf_den_ersten_buchungstag(self):
        """Zwei Buchungen vom 04.01. auf einem Blatt, dessen Anfangssaldo
        den 04.01. traegt. Das Fenster muss VOR dem 04.01. beginnen, sonst
        fehlen sie -- und die Bank meldet 150,00, die man nie findet."""
        eins, zwei = blaetter(STARMONEY_DATIERT)
        self.assertEqual(eins.erster_tag, "2021-01-04")
        self.assertEqual(fenster([eins, zwei]),
                         [("2021-01-03", "2021-12-30"),
                          ("2021-12-30", "2022-12-30")])

    def test_der_erste_tag_zaehlt_beim_vergleich_mit(self):
        """Das ist die Zahl, an der es auffiel: sechs von sechs Jahrgaengen
        wichen ab, weil jeder erste Tag aus jedem Fenster fiel."""
        gelesen = blaetter(STARMONEY_DATIERT)
        gerufen = []

        def bewegung(a, b):
            gerufen.append((a, b))
            return {("2021-01-03", "2021-12-30"): Decimal("100.00"),
                    ("2021-12-30", "2022-12-30"): Decimal("-100.00")}[(a, b)]

        self.assertEqual(vergleiche(gelesen, bewegung), [])
        self.assertEqual(gerufen, [("2021-01-03", "2021-12-30"),
                                   ("2021-12-30", "2022-12-30")])

    def test_die_bewegung_aus_eintraegen(self):
        """Der zweite Leser: dieselbe Frage, gestellt an gelesene
        Eintraege statt an die Instanz."""
        eintraege = [{"date": "2021-01-04", "amount": 250.0},
                     {"date": "2021-01-04", "amount": -100.0},
                     {"date": "2021-03-15", "amount": -50.0},
                     {"date": "2022-01-03", "amount": -100.0}]
        lesung = bewegung_aus(eintraege)
        self.assertEqual(lesung("2021-01-03", "2021-12-30"), Decimal("100.0"))
        self.assertEqual(lesung("2021-01-04", "2021-12-30"), Decimal("-50.0"))
        self.assertEqual(vergleiche(blaetter(STARMONEY_DATIERT), lesung), [])

    def test_das_urteil_nennt_die_fenster_als_einen_zeitraum(self):
        gefaellt = urteil(blaetter(STARMONEY_DATIERT),
                          bewegung_aus([{"date": "2021-01-04", "amount": 100.0},
                                        {"date": "2022-01-03", "amount": -100.0}]))
        self.assertEqual(gefaellt["spricht_fuer"],
                         [("2021-01-03", "2022-12-30")])
        # Und einzeln, fuer das Ersetzen je Blatt.
        self.assertEqual(gefaellt["fenster"],
                         [("2021-01-03", "2021-12-30"),
                          ("2021-12-30", "2022-12-30")])
        self.assertEqual(gefaellt["abweichungen"], [])


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


class TestDerEinleseweg(unittest.TestCase):
    """statement_formats.mt940_entries korrigiert das Vorzeichen selbst.

    Ohne diesen Test kaeme der Fehler beim Neueinlesen zurueck, und zwar
    stiller als beim ersten Mal: nichts fehlt, es steht nur alles auf der
    falschen Seite.
    """

    def _quelltext(self):
        pfad = os.path.join(os.path.dirname(os.path.dirname(HIER)),
                            "utils", "statement_formats.py")
        with open(pfad, encoding="utf-8") as handle:
            return handle.read()

    def test_der_betrag_wird_nicht_unbesehen_uebernommen(self):
        text = self._quelltext()
        self.assertIn("_mit_richtigem_vorzeichen(amount, data.get(\"status\"))",
                      text)

    def test_die_korrektur_kommt_aus_der_einen_tabelle(self):
        text = self._quelltext()
        self.assertIn("from kefiya.utils.auszug_pruefung import VORZEICHEN",
                      text)

    def test_die_korrektur_rechnet_richtig(self):
        """Die Funktion selbst, wirklich ausgefuehrt -- nicht nur gelesen.

        Sie steht in einem Modul ohne frappe, also laesst sie sich hier
        aufrufen. Die drei Faelle sind die drei Stornos aus dem Auszug von
        Konto 507, mit den Werten, die die Bibliothek dafuer liefert.
        """
        from kefiya.utils.statement_formats import _mit_richtigem_vorzeichen

        # Was mt940 liefert -> was daraus werden muss
        self.assertEqual(_mit_richtigem_vorzeichen(10000.0, "RC"), -10000.0)
        self.assertEqual(_mit_richtigem_vorzeichen(400000.0, "RC"), -400000.0)
        self.assertEqual(_mit_richtigem_vorzeichen(3000.0, "RD"), 3000.0)
        # Und die beiden gewoehnlichen bleiben, wie sie sind.
        self.assertEqual(_mit_richtigem_vorzeichen(-250.0, "D"), -250.0)
        self.assertEqual(_mit_richtigem_vorzeichen(250.0, "C"), 250.0)

    def test_ein_unbekanntes_kennzeichen_bleibt_unangetastet(self):
        """Lieber der Wert der Bibliothek als ein geratenes Vorzeichen."""
        from kefiya.utils.statement_formats import _mit_richtigem_vorzeichen

        self.assertEqual(_mit_richtigem_vorzeichen(42.0, "XY"), 42.0)
        self.assertEqual(_mit_richtigem_vorzeichen(42.0, None), 42.0)
