# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Mehrere Auftraege sind nicht von selbst eine Sammelueberweisung.

Die Ausgangsseite schickte jede Mehrfachauswahl als HKCCM: ein einziger
Auftrag bei der Bank, ueber die Summe, mit einer Freigabe fuer alles. Das
ist eine andere Zahlung als mehrere einzelne -- sie steht anders im
Kontoauszug, sie faellt anders unter Tageslimits, und der Empfaenger sieht
etwas anderes. Der Nutzer erfuhr davon erst von der Bank.

Jetzt wird gefragt. Vorbelegt wird die Antwort aus den Einstellungen
(Kefiya Settings: "Default for several orders"), entschieden wird sie
dort nicht -- die Frage kommt so oder so.

Quelltext-Pruefung, und sie weiss das (CLAUDE.md): sie sagt, dass die
Zeile da steht, nicht, dass sie laeuft.
"""

import os
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


def _js(name):
    pfad = os.path.join(WURZEL, "public", "js", "controllers", name)
    with open(pfad, encoding="utf-8") as handle:
        return handle.read()


def _funktion(quelle, kopf):
    """Der Rumpf einer JS-Funktion bis zur naechsten auf derselben Ebene."""
    rest = quelle.split(kopf)[1]
    ende = rest.find("\n\tfunction ")
    return rest[:ende if ende > 0 else len(rest)]


class TestGefragtWirdVorher(unittest.TestCase):

    def setUp(self):
        self.senden = _funktion(_js("payment_outbox.js"), "\tfunction send() {")

    def test_ein_einzelner_auftrag_wird_nicht_gefragt(self):
        """Bei einem Auftrag gibt es nichts zu entscheiden."""
        self.assertIn("if (rows.length === 1) {", self.senden)
        self.assertIn("frappe.confirm(message,", self.senden)

    def test_mehrere_fuehren_zur_frage(self):
        self.assertIn("askHowToSend(rows, message, toApprove,", self.senden)
        # Und nicht daran vorbei: der Sammelweg wird nur noch aus der Frage
        # heraus oder fuer den einzelnen Auftrag aufgerufen.
        sammel = self.senden.count("handToBank(")
        self.assertEqual(sammel, 1, "send() reicht nur noch den einzelnen"
                                    " Auftrag direkt weiter")

    def test_die_frage_kennt_beide_wege(self):
        quelle = _js("payment_outbox.js")
        wege = quelle.split("const SEND_WAYS = [")[1].split("];")[0]
        self.assertIn("One after another", wege)
        self.assertIn("As one collective order", wege)
        frage = _funktion(quelle, "\tfunction askHowToSend(")
        self.assertIn("handToBank(rows.map(", frage)
        self.assertIn("sendOneByOne(rows, toApprove)", frage)

    def test_die_vorauswahl_kommt_aus_den_einstellungen(self):
        """Vorbelegt, nie entschieden: gefragt wird so oder so."""
        frage = _funktion(_js("payment_outbox.js"),
                          "\tfunction askHowToSend(")
        self.assertIn('fieldname: "how", reqd: 1', frage)
        self.assertIn("way.stored === view.sendDefault", frage)
        self.assertIn("default: preset.label", frage)
        # Und ohne Treffer der erste angebotene Weg -- einzeln, nicht
        # zusammen.
        self.assertIn("|| ways[0]", frage)
        quelle = _js("payment_outbox.js")
        wege = quelle.split("const SEND_WAYS = [")[1].split("];")[0]
        self.assertLess(wege.index("One after another"),
                        wege.index("As one collective order"))

    def test_die_seite_holt_den_vorgabewert_vom_server(self):
        quelle = _js("payment_outbox.js")
        self.assertIn('view.sendDefault = m.send_default', quelle)
        with open(os.path.join(WURZEL, "utils", "outbox.py"),
                  encoding="utf-8") as handle:
            server = handle.read()
        self.assertIn('"send_default":', server)
        self.assertIn('"Kefiya Settings", "default_send_mode"', server)


class TestEinzelnHeisstEinzeln(unittest.TestCase):

    def setUp(self):
        self.einzeln = _funktion(_js("payment_outbox.js"),
                                 "\tfunction sendOneByOne(")

    def test_jeder_auftrag_geht_fuer_sich(self):
        self.assertIn('method: "kefiya.utils.client.submit_kefiya_transfer"',
                      self.einzeln)
        self.assertIn("names[index]", self.einzeln)
        self.assertNotIn("send_transfer_outbox", self.einzeln)

    def test_jeder_auftrag_von_seinem_eigenen_konto(self):
        """Ueber Konten hinweg ist das der einzige Weg, und jeder Auftrag
        muss die Bank erreichen, von der er wirklich zahlt."""
        self.assertIn("user_scope: orders[index].kefiya_login",
                      self.einzeln)
        self.assertIn("login: orders[index].kefiya_login", self.einzeln)

    def test_der_naechste_erst_nach_der_freigabe_des_vorigen(self):
        self.assertIn("step(index + 1, sent + 1)", self.einzeln)
        self.assertIn("kefiya.await_release(", self.einzeln)

    def test_die_antwort_der_bank_wird_an_einer_stelle_gedeutet(self):
        """sendOutcome, wie der Sammelweg auch -- sonst kennt ein neuer
        Status die eine Stelle und die andere nicht."""
        self.assertIn("switch (sendOutcome(m))", self.einzeln)
        quelle = _js("payment_outbox.js")
        self.assertEqual(quelle.count('status === "tan_required"'), 1)

    def test_eine_getippte_tan_haelt_an_statt_zu_raten(self):
        """Die TAN wird in ihrem eigenen Fenster beantwortet, und nichts
        sagt dieser Schleife, wann das geschehen ist."""
        self.assertIn('case "tan":', self.einzeln)
        self.assertIn("stay selected", self.einzeln)
        # Die Auswahl bleibt stehen, damit "Senden" weitermacht.
        halt = self.einzeln.split("function finish(")[1]
        self.assertIn("if (why) {", halt)
        self.assertLess(halt.index("if (why) {"),
                        halt.index("view.selected = {};"))

    def test_gesagt_wird_wie_weit_es_kam(self):
        self.assertIn("Sent so far: {0} of {1}.", self.einzeln)

    def test_jeder_entwurf_wird_erst_an_seiner_reihe_freigegeben(self):
        """Nicht alle vorab: ein Auftrag, der wegen eines frueheren Halts nie
        hinausgeht, darf nicht freigegeben zurueckbleiben -- die Freigabe
        sperrt Betraege und Empfaenger."""
        self.assertIn('method: "kefiya.utils.client.approve_transfers"',
                      self.einzeln)
        self.assertIn("JSON.stringify([name])", self.einzeln)
        schritt = self.einzeln.split("function step(")[1]
        self.assertIn("isDraft[name]", schritt)
        self.assertLess(schritt.index("approve_transfers"),
                        schritt.index("submit_kefiya_transfer"))

    def test_eine_abgelehnte_freigabe_haelt_den_lauf_an(self):
        self.assertIn("if (refused) {", self.einzeln)
        self.assertIn("return false;", self.einzeln)
