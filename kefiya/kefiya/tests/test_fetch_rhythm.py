# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Was sich selten bewegt, wird selten abgerufen.

Die Volksbank verlangt fuer jeden Auftrag eine Freigabe in ihrer App. Der
Sammelabruf stellt ihr dreissig Zugaenge hintereinander -- und vierzehn davon
hatten am 25.09.2026 in 90 Tagen keine einzige Buchung: drei
Geschaeftsanteile, zwei Avale, ein Sparkonto, acht ruhende Girokonten. Jeder
Lauf kostete fuer sie eine Freigabe und brachte nichts. Das ist die Haelfte
des "staendig freigeben", ueber das der Nutzer sich beschwert hat.

Die Vorgabe kommt aus der Kontoart, die die BANK gemeldet hat (HIUPD, am
Kefiya Login) -- nicht aus einer Vermutung darueber, welches Konto wichtig
ist. Ueberschreiben laesst sie sich je Zugang, und die Null darin heisst
ausdruecklich "bei jedem Lauf".

Zwei Dinge haelt dieser Test besonders fest:

  * Im Zweifel wird abgerufen. Kein Datum, ein unlesbares Datum, kein
    Rhythmus -- alles fuehrt zum Abruf. Ein uebersehener Umsatz waere
    teurer als eine Freigabe zu viel.
  * Ausgelassen wird nur der Sammellauf. Wer ein Konto von Hand abruft,
    bekommt es abgerufen.
"""

import datetime
import os
import unittest

from kefiya.utils import fetch_rhythm

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

JETZT = datetime.datetime(2026, 9, 25, 12, 0, 0)


class TestWelcherRhythmus(unittest.TestCase):

    def test_die_kontoart_der_bank_gibt_die_vorgabe(self):
        self.assertEqual(fetch_rhythm.interval_days("Cooperative Shares"), 30)
        self.assertEqual(fetch_rhythm.interval_days("Guarantee / Credit Line"), 30)
        self.assertEqual(fetch_rhythm.interval_days("Loan"), 30)

    def test_ein_zahlungskonto_bleibt_bei_jedem_lauf(self):
        self.assertEqual(fetch_rhythm.interval_days("Current Account"), 0)
        self.assertEqual(fetch_rhythm.interval_days("Savings"), 0)
        self.assertEqual(fetch_rhythm.interval_days(None), 0)
        self.assertEqual(fetch_rhythm.interval_days("was Neues"), 0)

    def test_am_zugang_eingestellt_schlaegt_die_vorgabe(self):
        self.assertEqual(fetch_rhythm.interval_days("Cooperative Shares", 7), 7)
        self.assertEqual(fetch_rhythm.interval_days("Current Account", 14), 14)

    def test_die_null_ist_keine_angabe(self):
        """Und das ist der Unterschied, an dem die Regel zuerst wirkungslos
        blieb: ein Int-Feld in Frappe kennt kein "nicht gesetzt", die
        Migration schreibt die 0 in jede bestehende Zeile. Haette die 0
        "bei jedem Lauf" geheissen, stuende sie nach dem Deploy an allen 44
        Zugaengen -- und genau so kam es: 60 Volksbank-Importe am 26.09.,
        zweimal dreissig, unveraendert."""
        self.assertEqual(fetch_rhythm.interval_days("Loan", 0), 30)
        self.assertEqual(fetch_rhythm.interval_days("Loan", "0"), 30)
        self.assertEqual(fetch_rhythm.interval_days("Current Account", 0), 0)

    def test_leer_heisst_auch_vorgabe(self):
        self.assertEqual(fetch_rhythm.interval_days("Loan", None), 30)
        self.assertEqual(fetch_rhythm.interval_days("Loan", ""), 30)

    def test_wer_es_oft_will_traegt_eins_ein(self):
        self.assertEqual(fetch_rhythm.interval_days("Cooperative Shares", 1), 1)

    def test_unsinn_ueberlaesst_es_der_kontoart(self):
        self.assertEqual(fetch_rhythm.interval_days("Loan", "bald"), 30)
        self.assertEqual(fetch_rhythm.interval_days("Loan", -5), 30)
        self.assertEqual(fetch_rhythm.interval_days("Current Account", "bald"), 0)


class TestWannWiederFaellig(unittest.TestCase):

    def test_frisch_abgerufen_wird_ausgelassen(self):
        self.assertFalse(fetch_rhythm.due("2026-09-20 08:00:00", 30, JETZT))

    def test_lange_her_kommt_dran(self):
        self.assertTrue(fetch_rhythm.due("2026-08-01 08:00:00", 30, JETZT))

    def test_genau_faellig_kommt_dran(self):
        self.assertTrue(fetch_rhythm.due("2026-08-26 12:00:00", 30, JETZT))

    def test_ohne_rhythmus_immer(self):
        self.assertTrue(fetch_rhythm.due("2026-09-25 11:59:00", 0, JETZT))

    def test_im_zweifel_wird_abgerufen(self):
        """Ein uebersehener Umsatz ist teurer als eine Freigabe zu viel."""
        self.assertTrue(fetch_rhythm.due(None, 30, JETZT))
        self.assertTrue(fetch_rhythm.due("", 30, JETZT))
        self.assertTrue(fetch_rhythm.due("kein Datum", 30, JETZT))
        self.assertTrue(fetch_rhythm.due("2026-09-24", "bald", JETZT))

    def test_auch_als_datum_und_zeitpunkt(self):
        self.assertFalse(fetch_rhythm.due(
            datetime.datetime(2026, 9, 24, 9, 0), 30, JETZT))
        self.assertFalse(fetch_rhythm.due(
            datetime.date(2026, 9, 24), 30, JETZT))

    def test_die_zeile_aus_der_datenbank(self):
        ruhend = {"account_kind": "Cooperative Shares",
                  "last_fetch_attempt": "2026-09-24 09:00:00"}
        self.assertFalse(fetch_rhythm.due_now(ruhend, JETZT))
        giro = {"account_kind": "Current Account",
                "last_fetch_attempt": "2026-09-25 09:53:00"}
        self.assertTrue(fetch_rhythm.due_now(giro, JETZT))
        # Und was am Zugang steht, gilt auch hier -- ab 1.
        self.assertTrue(fetch_rhythm.due_now(
            dict(ruhend, fetch_interval_days=1,
                 last_fetch_attempt="2026-09-23 09:00:00"), JETZT))
        # Eine Zeile ohne alles: abrufen.
        self.assertTrue(fetch_rhythm.due_now({}, JETZT))


class TestNurDerSammellauf(unittest.TestCase):

    def _quelle(self, *teile):
        with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
            return handle.read()

    def setUp(self):
        self.client = self._quelle("utils", "client.py")
        self.gruppen = self.client.split("def get_fetch_groups(")[1] \
                                  .split("\n@frappe.whitelist")[0]

    def test_die_gruppen_lassen_aus_was_nicht_faellig_ist(self):
        self.assertIn("fetch_rhythm.due_now(row)", self.gruppen)
        self.assertIn("account_kind", self.gruppen)
        self.assertIn("fetch_interval_days", self.gruppen)
        self.assertIn("last_fetch_attempt", self.gruppen)

    def test_der_einzelabruf_fragt_nicht_nach_dem_rhythmus(self):
        """Wer ein Konto von Hand abruft, bekommt es abgerufen -- sonst
        waere aus der Schonung eine Sperre geworden."""
        einzeln = self.client.split("def fetch_all(")[1].split("\ndef ")[0]
        self.assertNotIn("fetch_rhythm", einzeln)
        gruppe = self.client.split("def fetch_group(")[1].split("\ndef ")[0]
        self.assertNotIn("fetch_rhythm", gruppe)

    def test_das_feld_steht_am_zugang(self):
        import json
        pfad = os.path.join(WURZEL, "kefiya", "doctype", "kefiya_login",
                            "kefiya_login.json")
        with open(pfad, encoding="utf-8") as handle:
            felder = {f["fieldname"]: f for f in json.load(handle)["fields"]
                      if f.get("fieldname")}
        self.assertIn("fetch_interval_days", felder)
        self.assertEqual(felder["fetch_interval_days"]["fieldtype"], "Int")
        # Neben "Vom Abruf ausnehmen": beides entscheidet, ob abgerufen wird.
        self.assertIn("skip_fetch", felder)


class TestAuchInDerNacht(unittest.TestCase):
    """Der naechtliche Zeitplan muss denselben Rhythmus kennen.

    Am Morgen des 27.09.2026 -- der Rhythmus war am Abend zuvor ausgerollt --
    standen wieder dreissig frische Volksbank-Importe da, angelegt von
    Administrator zwischen 06:01:07 und 08:00:59. Der Sammelabruf war es
    nicht: nachgerechnet haette er an diesem Tag 14 abgerufen und 16
    geschont. Es war `scheduled_import_fints_payments`, das seine eigene
    Tabelle mitbrachte:

        Kefiya-Schedule-Zeilen: 57
        Frequenzen gesamt: {'(Daily, 1)': 57}

    Alle 57 Zeilen "Daily", alle aktiv, die 36 Volksbank-Zeilen auf 06, 07
    und 08 Uhr verteilt. Zwei Regeln fuer dieselbe Frage -- und die aeltere
    gewann jede Nacht. Seither entscheidet der groessere der beiden
    Abstaende.
    """

    def _quelle(self, *teile):
        with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
            return handle.read()

    def test_der_groessere_abstand_gewinnt(self):
        # Ein Geschaeftsanteil bleibt ein Geschaeftsanteil, auch wenn im
        # Plan "taeglich" steht.
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Daily", "Cooperative Shares"), 30)
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Daily", "Guarantee / Credit Line"),
            30)
        # Und ein monatlicher Plan wird durch ein Girokonto nicht taeglich.
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Monthly", "Current Account"), 30)
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Weekly", "Current Account"), 7)

    def test_ein_zahlungskonto_bleibt_taeglich(self):
        """Ohne eigene Angabe aendert sich fuer die arbeitenden Konten
        nichts: "Daily" heisst weiter ein Tag Abstand."""
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Daily", "Current Account"), 1)
        self.assertEqual(fetch_rhythm.schedule_interval_days("Daily", None), 1)

    def test_die_angabe_am_zugang_gilt_auch_hier(self):
        """Die neun ruhenden Girokonten stehen auf 7 Tage. Das galt bisher
        nur fuer den Sammelabruf -- die Nacht holte sie trotzdem taeglich."""
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Daily", "Current Account", 7), 7)
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Weekly", "Current Account", 30), 30)

    def test_eine_null_hebt_nichts_auf(self):
        """Die 0 heisst "keine eigene Angabe" -- hier wie dort. Sie darf den
        Plan nicht unterbieten."""
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Weekly", "Current Account", 0), 7)
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("Monthly", "Cooperative Shares", 0),
            30)

    def test_unbekanntes_heisst_ein_tag(self):
        """Wie bisher: was nicht in der Tabelle steht, bekommt einen Tag."""
        self.assertEqual(fetch_rhythm.schedule_interval_days(None, None), 1)
        self.assertEqual(fetch_rhythm.schedule_interval_days("", None), 1)
        self.assertEqual(
            fetch_rhythm.schedule_interval_days("was Neues", None), 1)

    def test_der_zeitplan_fragt_den_rhythmus(self):
        quelle = self._quelle("kefiya", "doctype", "kefiya_schedule",
                              "kefiya_schedule.py")
        self.assertIn("fetch_rhythm.schedule_interval_days(", quelle)
        # Und er holt sich die beiden Felder, die dafuer noetig sind.
        self.assertIn("account_kind", quelle)
        self.assertIn("fetch_interval_days", quelle)

    def test_die_alte_tabelle_steht_nur_noch_an_einer_stelle(self):
        """Zwei Tabellen waren der Fehler. FREQUENCY_GAP_DAYS ist nach
        fetch_rhythm gewandert und darf im Zeitplan nicht wieder
        auftauchen."""
        quelle = self._quelle("kefiya", "doctype", "kefiya_schedule",
                              "kefiya_schedule.py")
        self.assertNotIn("FREQUENCY_GAP_DAYS", quelle)
        self.assertEqual(fetch_rhythm.SCHEDULE_DAYS,
                         {"Daily": 1, "Weekly": 7, "Monthly": 30})

    def test_von_hand_bleibt_von_hand(self):
        """`manual=1` ueberspringt das Gatter weiterhin komplett -- der
        Rhythmus schont, er sperrt nicht."""
        quelle = self._quelle("kefiya", "doctype", "kefiya_schedule",
                              "kefiya_schedule.py")
        gatter = quelle.split("if not manual:")[1].split("# Default fetch")[0]
        self.assertIn("fetch_rhythm.schedule_interval_days(", gatter)
