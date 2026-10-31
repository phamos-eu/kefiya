# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der Verwendungszweck stand ohne Leerzeichen im Kontoauszug.

Gemeldet am 01.10.2026, zu sehen auf fuenf Konten, auf jedem
Rechnungsabschluss der Sparkasse::

    RechnungKosten SRZDauerrechnungsnummer.20250908-BW035-00038612361Einze

Die Bank hat sechs Zeilen geschickt. Die Bibliothek mt940 fuegt die
Teilfelder ?20-?29 des Feldes :86: in ``_join_result()`` ohne Trenner
zusammen, und ``fints.utils.mt940_to_array()`` baut ``Transactions()`` ohne
Argumente -- also mit ``space=False``.

Ein Leerzeichen ueberall waere die halbe Reparatur: hart umbrochener
SEPA-Text zerfaellt davon, und auch das steht in dieser Datenbank schon::

    SVWZ+MIETE UND BETRIEBSKOST ENPAUSCHALE B UERO LUTHERST RASSE
"""

import pathlib
import unittest

from kefiya.utils import booking_fingerprint, verwendungszweck

HIER = pathlib.Path(__file__).resolve()
WURZEL = HIER.parents[3]


def quelle(pfad):
    return (WURZEL / pfad).read_text(encoding="utf-8")


#: Die sechs Teilfelder der gemeldeten Buchung, so wie die Sparkasse sie
#: geschickt hat.
GEMELDET = [
    "Rechnung",
    "Kosten SRZ",
    "Dauerrechnungsnummer.",
    "20250908-BW035-00038612361",
    "Einzelrechnungsnummer.",
    "20261001-BW035-00042748593",
]


class TestDieGemeldeteBuchung(unittest.TestCase):
    """Der Fall aus der Meldung, Zeile fuer Zeile."""

    def test_so_sah_es_aus(self):
        """Die Zeichenkette aus der Meldung, so weit sie im Bild zu sehen war."""
        self.assertTrue(
            "".join(GEMELDET).startswith(
                "RechnungKosten SRZDauerrechnungsnummer."
                "20250908-BW035-00038612361Einze"),
            "".join(GEMELDET))

    def test_und_so_sieht_es_danach_aus(self):
        self.assertEqual(
            verwendungszweck.zusammensetzen(GEMELDET),
            "Rechnung Kosten SRZ Dauerrechnungsnummer."
            " 20250908-BW035-00038612361 Einzelrechnungsnummer."
            " 20261001-BW035-00042748593")

    def test_die_woerter_stehen_wieder_einzeln(self):
        lesbar = verwendungszweck.zusammensetzen(GEMELDET)
        for wort in ("Rechnung", "Kosten SRZ", "Dauerrechnungsnummer.",
                     "Einzelrechnungsnummer."):
            self.assertIn(wort, lesbar)
        self.assertNotIn("RechnungKosten", lesbar)
        self.assertNotIn("SRZDauer", lesbar)
        self.assertNotIn("361Einzel", lesbar)


class TestGetaggterTextWirdNichtAuseinandergezogen(unittest.TestCase):
    """Hinter SVWZ+ steht EIN Feld, hart umbrochen. Ein Trenner dort
    zerlegt Woerter -- das ist der Fehler in die andere Richtung, und er
    steht auf den Buchungen von 2022 bereits in der Datenbank."""

    def test_sepa_text_bleibt_zusammen(self):
        teile = ["EREF+NOTPROVIDED KREF+NONREF",
                 " SVWZ+MIETE UND BETRIEBSKOST",
                 "ENPAUSCHALE BUERO LUTHERSTR"]
        self.assertEqual(verwendungszweck.zusammensetzen(teile),
                         "".join(teile))

    def test_das_wort_bleibt_ganz(self):
        teile = ["SVWZ+MIETE UND BETRIEBSKOST", "ENPAUSCHALE"]
        self.assertIn("BETRIEBSKOSTENPAUSCHALE",
                      verwendungszweck.zusammensetzen(teile))

    def test_jedes_dk_schluesselwort_zaehlt(self):
        for schluessel in verwendungszweck.DK_SCHLUESSEL:
            self.assertTrue(
                verwendungszweck.ist_getaggt("vorn " + schluessel + "hinten"),
                schluessel)

    def test_auch_klein_geschrieben(self):
        self.assertTrue(verwendungszweck.ist_getaggt("svwz+miete"))

    def test_text_ohne_schluesselwort_ist_nicht_getaggt(self):
        self.assertFalse(verwendungszweck.ist_getaggt("".join(GEMELDET)))

    def test_ein_plus_allein_macht_noch_keinen_schluessel(self):
        self.assertFalse(verwendungszweck.ist_getaggt("Rechnung + Zinsen"))


class TestDieEntscheidungGehtAnDieBibliothek(unittest.TestCase):
    """mit_leerzeichen() ist genau der Schalter ``space``, den die
    Bibliothek kennt -- nur je Buchung statt je Lauf."""

    def test_zeilen_der_bank_bekommen_den_trenner(self):
        self.assertTrue(verwendungszweck.mit_leerzeichen("".join(GEMELDET)))

    def test_getaggter_text_nicht(self):
        self.assertFalse(
            verwendungszweck.mit_leerzeichen("EREF+X SVWZ+Miete"))

    def test_nichts_bekommt_ihn_auch(self):
        self.assertTrue(verwendungszweck.mit_leerzeichen(""))
        self.assertTrue(verwendungszweck.mit_leerzeichen(None))


class TestLeeresUndKrummes(unittest.TestCase):

    def test_nichts_bleibt_nichts(self):
        self.assertEqual(verwendungszweck.zusammensetzen([]), "")
        self.assertEqual(verwendungszweck.zusammensetzen(None), "")

    def test_leere_teile_fallen_heraus(self):
        self.assertEqual(
            verwendungszweck.zusammensetzen(["Rechnung", "", None, "Kosten"]),
            "Rechnung Kosten")

    def test_randleerzeichen_der_teilfelder_verschwinden(self):
        """Die Bank fuellt ein Teilfeld auf die Feldbreite auf."""
        self.assertEqual(
            verwendungszweck.zusammensetzen(["Rechnung   ", "  Kosten SRZ "]),
            "Rechnung Kosten SRZ")

    def test_innere_leerzeichen_bleiben(self):
        """'Saldo.        1.138.849,73-' ist eine Zeile der Bank, und ihre
        Ausrichtung ist Teil dessen, was sie geschickt hat."""
        self.assertIn("Saldo.        1.138.849,73-",
                      verwendungszweck.zusammensetzen(
                          ["Saldo.        1.138.849,73-", "Zinssatz 4,250 %"]))

    def test_ein_einzelnes_teilfeld_bleibt_wie_es_ist(self):
        self.assertEqual(verwendungszweck.zusammensetzen(["Miete Oktober"]),
                         "Miete Oktober")


class TestDerFingerabdruckZaehltKeineLeerzeichen(unittest.TestCase):
    """Die eigentliche Absicherung. Ohne sie waere die Reparatur oben eine
    Welle Doppelbuchungen: eine Buchung, die ohne Leerzeichen gespeichert
    wurde, waere nach der Aenderung nicht wiederzuerkennen, und der naechste
    Ueberschneidungstag haette sie erneut importiert."""

    def kanonisch(self, zweck):
        return booking_fingerprint.canonical(
            "Sparkasse", "2026-10-01", 12.45, "DE02120300000000202051",
            "Stadtwerke", zweck)

    def test_vorher_und_nachher_sind_dieselbe_buchung(self):
        self.assertEqual(self.kanonisch("".join(GEMELDET)),
                         self.kanonisch(verwendungszweck.zusammensetzen(GEMELDET)))

    def test_der_alte_fall_bleibt_geloest(self):
        """Das Datum mit dem Umbruch mitten im Jahr."""
        self.assertEqual(self.kanonisch("Datum 28.02.20 26"),
                         self.kanonisch("Datum 28.02.2026"))

    def test_und_der_name_mit_dem_umbruch(self):
        links = booking_fingerprint.canonical(
            "Sparkasse", "2026-10-01", 50, None,
            "Max und Erika Muster mann", "Miete")
        rechts = booking_fingerprint.canonical(
            "Sparkasse", "2026-10-01", 50, None,
            "Max und Erika Mustermann", "Miete")
        self.assertEqual(links, rechts)

    def test_zeilenumbruch_zaehlt_wie_kein_leerzeichen(self):
        self.assertEqual(self.kanonisch("Rechnung\nKosten"),
                         self.kanonisch("RechnungKosten"))

    def test_verschiedener_text_bleibt_verschieden(self):
        self.assertNotEqual(self.kanonisch("Miete Oktober"),
                            self.kanonisch("Miete November"))

    def test_ein_anderes_konto_bleibt_eine_andere_buchung(self):
        eins = booking_fingerprint.canonical(
            "Sparkasse", "2026-10-01", 50, "DE02", "X", "Miete")
        zwei = booking_fingerprint.canonical(
            "Volksbank", "2026-10-01", 50, "DE02", "X", "Miete")
        self.assertNotEqual(eins, zwei)

    def test_tidy_laesst_keinen_leerraum_stehen(self):
        self.assertEqual(booking_fingerprint.tidy("  a \t b \n c  "), "abc")

    def test_die_alte_form_bleibt_unberuehrt(self):
        """legacy() bildet den Hash nach, den die alten Importer geschrieben
        haben. Wer daran dreht, erkennt die Vergangenheit nicht wieder."""
        self.assertEqual(
            booking_fingerprint.legacy("2026-10-01", "50.00", "Max",
                                       "UEBERWEISUNG", "Miete  Oktober"),
            booking_fingerprint.legacy("2026-10-01", "50.00", "Max",
                                       "UEBERWEISUNG", "Miete  Oktober"))

    def test_legacy_zaehlt_leerzeichen_weiterhin(self):
        self.assertNotEqual(
            booking_fingerprint.legacy("2026-10-01", "50.00", "Max", "U",
                                       "Miete Oktober"),
            booking_fingerprint.legacy("2026-10-01", "50.00", "Max", "U",
                                       "MieteOktober"))


class TestBeideLesewegeSetzenDieReparaturEin(unittest.TestCase):
    """Abruf und eingelesene .sta-Datei muessen denselben Text ergeben --
    sonst waeren es fuer den Fingerabdruck zwei verschiedene Buchungen."""

    def test_der_abruf(self):
        src = quelle("kefiya/utils/fints_controller.py")
        stelle = src.index("def get_fints_transactions")
        abschnitt = src[stelle:stelle + 2500]
        self.assertIn("ensure_the_purpose_keeps_its_spaces", abschnitt)
        self.assertLess(
            abschnitt.index("ensure_the_purpose_keeps_its_spaces"),
            abschnitt.index("_get_transactions_checked"),
            "Die Reparatur muss vor dem Parsen stehen.")

    def test_die_eingelesene_datei(self):
        src = quelle("kefiya/utils/statement_formats.py")
        stelle = src.index("def mt940_entries")
        abschnitt = src[stelle:stelle + 1500]
        self.assertIn("ensure_the_purpose_keeps_its_spaces", abschnitt)
        self.assertLess(
            abschnitt.index("ensure_the_purpose_keeps_its_spaces"),
            abschnitt.index("mt940_to_array(str(text"),
            "Die Reparatur muss vor dem Parsen stehen.")

    def test_sie_haengt_am_eintrag_nicht_am_modulnamen(self):
        """Der Eintrag in DEFAULT_PROCESSORS ist eine direkte Referenz auf
        die Funktion; ein Ersatz des Modulnamens bewirkt nichts."""
        src = quelle("kefiya/utils/mt940_compat.py")
        stelle = src.index("def ensure_the_purpose_keeps_its_spaces")
        abschnitt = src[stelle:]
        self.assertIn("DEFAULT_PROCESSORS", abschnitt)
        self.assertIn('"post_transaction_details"', abschnitt)

    def test_sie_entscheidet_je_buchung(self):
        src = quelle("kefiya/utils/mt940_compat.py")
        stelle = src.index("def ensure_the_purpose_keeps_its_spaces")
        abschnitt = src[stelle:]
        self.assertIn("verwendungszweck.mit_leerzeichen", abschnitt)
        self.assertIn("tag_dict.get(\"transaction_details\")", abschnitt)

    def test_sie_bricht_einen_abruf_nicht_ab(self):
        src = quelle("kefiya/utils/mt940_compat.py")
        stelle = src.index("def ensure_the_purpose_keeps_its_spaces")
        abschnitt = src[stelle:]
        self.assertGreaterEqual(abschnitt.count("except Exception"), 4)


if __name__ == "__main__":
    unittest.main()
