# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein Konto neu aufbauen heisst loeschen. Was dabei nicht passieren darf.

auszug_einlesen braucht frappe und laeuft hier nicht. Die Regeln, die es
einhalten muss, lassen sich trotzdem am Quelltext festhalten -- und dieser
Test weiss, dass er das tut (siehe CLAUDE.md: ein gruener Quelltext-Test
heisst "die Zeile steht da", nicht "der Code laeuft"). Was sich ohne
Bench ausfuehren laesst, wird ausgefuehrt: die Fenster, der zweite Leser,
die Bewegung aus Eintraegen stehen in auszug_pruefung und werden in
test_auszug_pruefung wirklich aufgerufen.
"""

import ast
import os
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


def _py(*parts):
    with open(os.path.join(WURZEL, *parts), encoding="utf-8") as handle:
        return handle.read()


def _funktion(quelle, name):
    baum = ast.parse(quelle)
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.FunctionDef) and knoten.name == name:
            return ast.get_source_segment(quelle, knoten)
    raise AssertionError("keine Funktion {0}".format(name))


class TestWasGeschuetztIst(unittest.TestCase):
    """An einer Buchung, an der Geld haengt, wird nicht geruehrt."""

    def setUp(self):
        self.quelle = _py("utils", "auszug_einlesen.py")

    def test_zugeordnetes_geld_schuetzt(self):
        self.assertIn("bt.allocated_amount > 0", self.quelle)

    def test_abgestimmte_buchungen_sind_geschuetzt(self):
        self.assertIn("bt.status IN ('Reconciled', 'Settled')", self.quelle)

    def test_ein_beleg_in_der_kindtabelle_schuetzt(self):
        self.assertIn("`tabBank Transaction Payments`", self.quelle)

    def test_geloescht_wird_nur_was_nicht_geschuetzt_ist(self):
        ersetzen = _funktion(self.quelle, "ersetzen")
        self.assertIn("if not z.geschuetzt]", ersetzen)
        self.assertIn("_loeschen(weg)", ersetzen)

    def test_stornierte_buchungen_zaehlen_nicht_zum_bestand(self):
        bestand = _funktion(self.quelle, "_bestand")
        self.assertIn("bt.docstatus < 2", bestand)


class TestDieReihenfolge(unittest.TestCase):
    """pruefe -> einlesen -> pruefe, und nichts davon faellt weg."""

    def setUp(self):
        self.quelle = _py("utils", "auszug_einlesen.py")
        self.ersetzen = _funktion(self.quelle, "ersetzen")

    def test_erst_der_plan_dann_das_loeschen(self):
        self.assertLess(self.ersetzen.index("planen(file_url"),
                        self.ersetzen.index("_loeschen(weg)"))

    def test_nach_dem_einlesen_wird_noch_einmal_geurteilt(self):
        self.assertLess(self.ersetzen.index("book_entries("),
                        self.ersetzen.index("nachher = auszug_pruefung.urteil("))
        self.assertIn("stimmt=nachher[\"brauchbar\"] and not nachher[\"abweichungen\"]",
                      self.ersetzen)

    def test_ein_konto_ohne_einigkeit_wird_uebersprungen(self):
        """Der zweite Leser widerspricht: kein Fenster wird angefasst."""
        self.assertIn("if not konto[\"einlesbar\"]:", self.ersetzen)
        planen = _funktion(self.quelle, "planen")
        self.assertIn("gefaellt[\"brauchbar\"] and not uneinig", planen)

    def test_gebucht_wird_ueber_den_einen_weg(self):
        """Kein zweiter Konstruktor: dieselbe book_entries wie jeder
        Dateiimport, mit dry_run=False ausdruecklich."""
        self.assertIn("book_entries(_im_fenster(je_konto[name], nach, bis),",
                      self.ersetzen)
        self.assertIn("dry_run=False)", self.ersetzen)

    def test_nur_die_fenster_fuer_die_der_auszug_spricht(self):
        """Je Blatt, nicht je Lauf: das Urteil liefert beide, und das
        Ersetzen nimmt die Blattfenster -- ein Jahrgang je Festschreibung."""
        planen = _funktion(self.quelle, "planen")
        self.assertIn("for nach, bis in gefaellt[\"fenster\"]:", planen)

    def test_festgeschrieben_wird_je_fenster(self):
        self.assertIn("frappe.db.commit()", self.ersetzen)


class TestDerZweiteLeser(unittest.TestCase):

    def test_die_bibliothek_wird_gegen_das_blatt_gehalten(self):
        quelle = _py("utils", "auszug_einlesen.py")
        zweitleser = _funktion(quelle, "_zweitleser")
        self.assertIn("auszug_pruefung.bewegung_aus(eintraege)", zweitleser)
        self.assertIn("auszug_pruefung.vergleiche(lauf, lesung", zweitleser)

    def test_nur_mt940_darf_ein_konto_neu_aufbauen(self):
        """Eine CSV traegt keine Salden. Ohne Salden kein Urteil, ohne
        Urteil kein Ersetzen."""
        planen = _funktion(_py("utils", "auszug_einlesen.py"), "planen")
        self.assertIn("if profil != \"mt940\":", planen)


class TestDerWegVonDerOberflaeche(unittest.TestCase):

    def test_das_ersetzen_laeuft_im_hintergrund(self):
        start = _funktion(_py("utils", "auszug_einlesen.py"), "start")
        self.assertIn("frappe.enqueue(", start)
        self.assertIn("queue=\"long\"", start)
        # Und nicht ohne Plan: ein Auszug, der nicht spricht, wird nicht
        # eingeplant.
        self.assertIn("if not planen(file_url, bank_account)[\"einlesbar\"]:",
                      start)

    def test_das_formular_fragt_erst_und_bestaetigt_dann(self):
        js = _py("kefiya", "doctype", "kefiya_bank_statement_import",
                 "kefiya_bank_statement_import.js")
        self.assertIn("plan_rebuild", js)
        self.assertIn("frappe.confirm(", js)
        self.assertLess(js.index("plan_rebuild"), js.index("start_rebuild"))

    def test_nur_eine_sta_datei_bekommt_den_knopf(self):
        js = _py("kefiya", "doctype", "kefiya_bank_statement_import",
                 "kefiya_bank_statement_import.js")
        self.assertIn("/\\.sta$/i.test(frm.doc.import_file)", js)


class TestZweiGleicheBuchungen(unittest.TestCase):
    """30.03.2022, zweimal PayPal 6,42 EUR mit demselben Text: zwei
    Buchungen, nicht eine und ihre Wiederholung."""

    def test_die_zweite_bekommt_ihre_eigene_referenz(self):
        from kefiya.utils.statement_formats import wiederholung
        self.assertEqual(wiederholung("abc", 1), "abc")
        self.assertNotEqual(wiederholung("abc", 2), "abc")
        self.assertNotEqual(wiederholung("abc", 2), wiederholung("abc", 3))
        # Und dieselbe bei jedem Lauf, sonst erkennt der zweite Import sie
        # nicht wieder.
        self.assertEqual(wiederholung("abc", 2), wiederholung("abc", 2))

    def test_die_erste_behaelt_ihre_referenz(self):
        """Nichts, was schon geschrieben ist, wechselt seine Identitaet."""
        from kefiya.utils.statement_formats import wiederholung
        self.assertEqual(wiederholung("abc", 0), "abc")

    def test_der_import_zaehlt_statt_zu_verwerfen(self):
        quelle = _py("utils", "statement_import.py")
        book = _funktion(quelle, "book_entries")
        self.assertNotIn("reference in seen", book)
        self.assertIn("seen[reference] = seen.get(reference, 0) + 1", book)
        self.assertIn("reference = wiederholung(reference, seen[reference])",
                      book)


class TestDasBerichtsformat(unittest.TestCase):
    """Die Pruefung sagt "nach", nicht "von": das Fenster beginnt hinter
    diesem Tag. Wer "von" liest, zaehlt den Tag mit."""

    def test_kontenpruefung_meldet_nach(self):
        quelle = _py("utils", "kontenpruefung.py")
        self.assertIn("\"spricht_fuer\": [{\"nach\": a, \"bis\": b}", quelle)

    def test_der_plan_meldet_nach(self):
        quelle = _py("utils", "auszug_einlesen.py")
        self.assertIn("\"nach\": nach, \"bis\": bis,", quelle)
