# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein Abruf, der nichts bringt, ist nicht dasselbe wie ein Abruf, der nicht
stattgefunden hat.

Drei Fehlalarme haben das gekostet. ``ax_bank_transaction_freshness`` meldete
am 29./23.09.2026 "Bankabruf ohne frische Daten" fuer Privatkonto,
Geschaeftskonto und ein Volksbank-Konto. In allen drei Faellen lief der
Abruf taeglich und einwandfrei -- es lag nur nichts auf den Konten. Der
Waechter hatte nichts, woran er das haette sehen koennen: am Konto stand kein
Zeitpunkt eines erfolgreichen Abrufs, nur ``last_fetch_attempt`` am Login, und
das wird auf beiden Wegen gestempelt.

Diese Tests halten die drei Saetze fest, auf die es dabei ankommt.
"""

import pathlib
import unittest

from kefiya.utils import fetch_outcome, fints_response

HIER = pathlib.Path(__file__).resolve()
WURZEL = HIER.parents[3]


def quelle(pfad):
    return (WURZEL / pfad).read_text(encoding="utf-8")


def urteil(*codes):
    """Ein Urteil, wie fints_response.verdict_of() es baut."""
    return {"status": fints_response._worst(
        object(), [{"code": c, "text": "", "detail": ""} for c in codes]),
        "lines": [{"code": c, "text": "", "detail": ""} for c in codes]}


class TestKeineBuchungIstEineAntwort(unittest.TestCase):
    """Der Satz, um den es geht. Ein Konto, auf dem eine Woche nichts
    passiert, ist kein Konto, das nicht abgerufen wurde."""

    def test_eine_leere_liste_ist_eine_antwort(self):
        self.assertTrue(
            fetch_outcome.the_bank_answered([]),
            "Die Bank hat gesagt: in diesem Fenster liegt nichts. Das ist"
            " eine Auskunft, kein Ausfall.")

    def test_ein_leeres_tupel_auch(self):
        self.assertTrue(fetch_outcome.the_bank_answered(()))

    def test_umsaetze_erst_recht(self):
        self.assertTrue(fetch_outcome.the_bank_answered([{"date": "2026-09-30"}]))

    def test_gar_nichts_ist_keine_antwort(self):
        self.assertFalse(
            fetch_outcome.the_bank_answered(None),
            "None heisst: es kam nichts zurueck, nicht 'nichts gefunden'.")


class TestEineRueckfrageIstKeineAntwort(unittest.TestCase):
    """Eine TAN-/Freigabe-Aufforderung beantwortet die Frage nicht. Wer sie
    als Erfolg stempelt, behauptet Daten, die es nicht gibt -- und nimmt dem
    Waechter genau das weg, wofuer er da ist."""

    def test_eine_tan_aufforderung_zaehlt_nicht(self):
        self.assertFalse(fetch_outcome.the_bank_answered([], challenged=True))

    def test_auch_nicht_wenn_daten_dabei_waeren(self):
        self.assertFalse(
            fetch_outcome.the_bank_answered([{"date": "2026-09-30"}],
                                            challenged=True),
            "Eine geparkte Challenge ist kein abgeschlossener Abruf.")


class TestEinNeunTausenderIstKeineAntwort(unittest.TestCase):
    """Dass die Antwort die Bibliothek erreicht hat, heisst nicht, dass die
    Bank geliefert hat. Dieselbe Unterscheidung, die fints_response fuer die
    Sendestrecke eingefuehrt hat."""

    def test_eine_ablehnung_zaehlt_nicht(self):
        self.assertFalse(
            fetch_outcome.the_bank_answered([], verdict=urteil("9010")))

    def test_eine_warnung_zaehlt(self):
        self.assertTrue(
            fetch_outcome.the_bank_answered([], verdict=urteil("3050")),
            "Eine Warnung ist eine angenommene Antwort mit Anmerkung --"
            " genauso wie in fints_response.refused().")

    def test_ein_erfolg_zaehlt(self):
        self.assertTrue(
            fetch_outcome.the_bank_answered([], verdict=urteil("0010")))

    def test_die_schlimmste_zeile_entscheidet(self):
        self.assertFalse(
            fetch_outcome.the_bank_answered([], verdict=urteil("0010", "9210")))

    def test_ohne_urteil_wird_nicht_blockiert(self):
        """Der Normalfall: die Umsatzabfrage liefert MT940-Daten, kein
        HIRMS-tragendes Antwortobjekt. Es gibt dann gar kein Urteil."""
        self.assertTrue(fetch_outcome.the_bank_answered([], verdict=None))

    def test_ein_unlesbares_urteil_blockiert_nicht(self):
        """Dieselbe Haltung wie fints_response: was nicht gelesen werden kann,
        ist UNKNOWN und blockiert nichts."""
        self.assertTrue(
            fetch_outcome.the_bank_answered([], verdict=urteil("xyz")))
        self.assertTrue(
            fetch_outcome.the_bank_answered([], verdict={"status": "unknown",
                                                         "lines": []}))


class TestDieZahlEntscheidetNichts(unittest.TestCase):
    """how_many() beschreibt, es urteilt nicht -- sonst waere der ruhige Fall
    wieder ein Fehlerfall."""

    def test_gezaehlt_wird_was_da_ist(self):
        self.assertEqual(fetch_outcome.how_many([1, 2, 3]), 3)
        self.assertEqual(fetch_outcome.how_many([]), 0)

    def test_nichts_ist_null(self):
        self.assertEqual(fetch_outcome.how_many(None), 0)

    def test_etwas_ohne_laenge_ist_eins(self):
        self.assertEqual(fetch_outcome.how_many(object()), 1)

    def test_null_umsaetze_bleiben_trotzdem_ein_erfolg(self):
        self.assertEqual(fetch_outcome.how_many([]), 0)
        self.assertTrue(fetch_outcome.the_bank_answered([]))


class TestDasFeldStehtAnEinerStelle(unittest.TestCase):
    """Der Feldname wird nicht an zwei Orten getippt."""

    def test_fetch_outcome_nennt_es(self):
        self.assertEqual(fetch_outcome.STAMP_FIELD,
                         "custom_last_successful_fetch")

    def test_fetch_persistence_nimmt_es_von_dort(self):
        src = quelle("kefiya/utils/fetch_persistence.py")
        self.assertIn("fetch_outcome.STAMP_FIELD", src)
        self.assertNotIn(
            '"custom_last_successful_fetch"', src,
            "Der Feldname gehoert nach fetch_outcome, nicht ein zweites Mal"
            " hierher.")


class TestGestempeltWirdErstWennEsInDerDatenbankSteht(unittest.TestCase):
    """Der Zeitpunkt soll heissen: die Bank hat geantwortet UND was sie
    geschickt hat, steht da. Ein Stempel direkt nach der Bankantwort wuerde
    einen spaeter gescheiterten Import als Erfolg ausgeben -- und genau darauf
    soll sich der Frischewaechter stuetzen."""

    def setUp(self):
        self.src = quelle("kefiya/utils/fints_controller.py")

    def test_der_trichter_stempelt(self):
        self.assertIn("note_successful_fetch", self.src)

    def test_erst_nach_dem_submit(self):
        submit = self.src.index("curr_doc.submit()")
        stempel = self.src.index("note_successful_fetch")
        self.assertLess(
            submit, stempel,
            "Gestempelt wird nach curr_doc.submit(), nicht davor.")

    def test_und_nach_dem_haltbarmachen(self):
        dauerhaft = self.src.index(
            "# Make the import durable before reconciliation runs")
        stempel = self.src.index("note_successful_fetch")
        self.assertLess(dauerhaft, stempel)

    def test_die_gelieferten_umsaetze_reisen_mit(self):
        stelle = self.src.index("note_successful_fetch")
        self.assertIn("delivered=tansactions",
                      self.src[stelle:stelle + 200])

    def test_der_import_liegt_innerhalb_der_methode(self):
        """Ringimport: fetch_persistence haengt ueber statement_import an der
        Importstrecke, fints_controller wird von dort gelesen."""
        stelle = self.src.index("note_successful_fetch")
        davor = self.src[max(0, stelle - 400):stelle]
        self.assertIn("from kefiya.utils import fetch_persistence as _persist",
                      davor)


class TestEinFehlschlagLandetImErrorLog(unittest.TestCase):
    """Der Weg ueber den Zeitplan protokollierte schon, der interaktive nicht:
    dort endete ein Fehlschlag in einer Bildschirmmeldung, die niemand
    wiederfindet."""

    def setUp(self):
        self.src = quelle("kefiya/utils/fints_controller.py")

    def test_der_trichter_protokolliert(self):
        self.assertIn("log_failed_fetch", self.src)

    def test_vor_dem_werfen(self):
        protokoll = self.src.index("log_failed_fetch")
        wurf = self.src.index('"Error parsing transactions<br>{0}"')
        self.assertLess(
            protokoll, wurf,
            "frappe.throw beendet die Methode -- danach wird nichts mehr"
            " geschrieben.")

    def test_eine_tan_rueckfrage_ist_kein_fehlschlag(self):
        """TanInteractionRequired wird vom except-Zweig darueber unveraendert
        durchgelassen und darf nicht als Fehler protokolliert werden."""
        tan = self.src.index("        except TanInteractionRequired:")
        protokoll = self.src.index("log_failed_fetch")
        self.assertLess(
            tan, protokoll,
            "Der TAN-Zweig steht vor dem allgemeinen -- sonst faengt der"
            " allgemeine die Rueckfrage mit ab.")


class TestDieBeidenSchreibenNieEinenFehlerNach(unittest.TestCase):
    """Ein Zeitstempel, der nicht gesetzt werden konnte, darf einen Abruf,
    der seine Buchungen schon geschrieben hat, nicht scheitern lassen -- und
    eine fehlgeschlagene Protokollierung darf keinen Durchlauf beenden."""

    def setUp(self):
        self.src = quelle("kefiya/utils/fetch_persistence.py")
        anfang = self.src.index("def note_successful_fetch")
        ende = self.src.index("# Balance")
        self.abschnitt = self.src[anfang:ende]

    def test_beide_sind_gekapselt(self):
        self.assertGreaterEqual(self.abschnitt.count("except Exception"), 2)

    def test_geschrieben_wird_mit_db_set(self):
        self.assertIn("db_set", self.abschnitt)
        self.assertNotIn(
            "account.save(", self.abschnitt,
            "save() laeuft durch update_default_bank_account() und sperrt alle"
            " Konten der Firma -- das ist der Deadlock 1213 aus store_balance.")

    def test_das_fehlende_feld_wird_geprueft(self):
        self.assertIn("meta.has_field", self.abschnitt)

    def test_der_titel_wird_geschnitten(self):
        self.assertIn(
            "title[:140]", self.abschnitt,
            "method im Error Log ist eine Data-Spalte mit 140 Zeichen; ein"
            " zu langer Titel hat schon einmal aus dem except-Block heraus"
            " CharacterLengthExceededError geworfen.")

    def test_titel_und_meldung_bleiben_getrennt(self):
        self.assertIn("message=", self.abschnitt)
        self.assertIn("title=", self.abschnitt)

    def test_die_bank_kommt_mit_ihren_eigenen_worten(self):
        self.assertIn("fints_response.as_text", self.abschnitt)
        self.assertIn("fints_response.advice", self.abschnitt)


class TestDerVersuchBleibtWasErWar(unittest.TestCase):
    """last_fetch_attempt am Login wird weiter auf beiden Wegen gestempelt --
    es bremst die 20-Minuten-Wiederholschleife. Der neue Zeitpunkt am Konto
    ersetzt es nicht, er sagt etwas anderes."""

    def test_der_zeitplan_stempelt_den_versuch_weiter(self):
        src = quelle("kefiya/kefiya/doctype/kefiya_schedule/kefiya_schedule.py")
        self.assertIn("_record_fetch_attempt", src)
        self.assertIn("last_fetch_attempt", src)

    def test_und_der_neue_zeitpunkt_steht_am_bankkonto(self):
        src = quelle("kefiya/utils/fetch_persistence.py")
        stelle = src.index("def note_successful_fetch")
        abschnitt = src[stelle:stelle + 3000]
        self.assertIn('"Bank Account"', abschnitt)

    def test_und_ruehrt_den_versuch_nicht_an(self):
        """Erwaehnen darf der Docstring ihn -- er erklaert ja den Unterschied.
        Geschrieben werden darf er hier nicht."""
        src = quelle("kefiya/utils/fetch_persistence.py")
        stelle = src.index("def note_successful_fetch")
        abschnitt = src[stelle:stelle + 3000]
        for geschrieben in ('"last_fetch_attempt"', "'last_fetch_attempt'",
                            ".last_fetch_attempt ="):
            self.assertNotIn(
                geschrieben, abschnitt,
                "Die beiden Felder sagen Verschiedenes und werden nicht"
                " vermischt.")


if __name__ == "__main__":
    unittest.main()
