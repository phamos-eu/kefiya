# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Rohe camt-Dokumente sind keine Buchungen.

Die Volksbank bietet MT940 nicht mehr an -- ihre Bankparameter kennen nur
noch HICAZS, kein HIKAZS -- und verlangt vor jedem Abruf eine Freigabe in
ihrer App. Die Wiederaufnahme nach dieser Freigabe kehrt in python-fints
eine Ebene unterhalb von get_transactions() zurueck, und dort ist das
Ergebnis noch das rohe Paar::

    ([b"<?xml ... Document ...>"], [])

kefiya reichte das an json.dumps weiter: "Object of type bytes is not JSON
serializable". Drei Konten liessen sich damit ueberhaupt nicht abrufen.

Die Regel laeuft hier ohne Bank und ohne Bibliothek.
"""

import os
import unittest

from kefiya.utils.camt_shape import streams_in

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

DOK = b"<?xml version='1.0'?><Document><BkToCstmrStmt/></Document>"
DOK2 = b"<?xml version='1.0'?><Document><BkToCstmrStmt>2</BkToCstmrStmt></Document>"


class Buchung(tuple):
    """Ein fints.models.Transaction ist ein namedtuple mit einem Feld."""

    def __new__(cls, data):
        return super().__new__(cls, (data,))


class TestWasDokumenteSind(unittest.TestCase):

    def test_das_paar_der_bibliothek(self):
        self.assertEqual(streams_in(([DOK], [])), [DOK])

    def test_mehrere_dokumente_in_ihrer_reihenfolge(self):
        self.assertEqual(streams_in(([DOK, DOK2], [])), [DOK, DOK2])

    def test_vorgemerkte_nur_auf_verlangen(self):
        self.assertEqual(streams_in(([DOK], [DOK2])), [DOK])
        self.assertEqual(streams_in(([DOK], [DOK2]), include_pending=True),
                         [DOK, DOK2])

    def test_ein_fehlender_vorgemerkt_block_ist_kein_dokument(self):
        """python-fints haengt statement_pending auch dann an, wenn die Bank
        keinen sendet -- [None] ist wahr, und der Parser stirbt daran."""
        self.assertEqual(streams_in(([DOK], [None]), include_pending=True),
                         [DOK])
        self.assertEqual(streams_in(([DOK], None), include_pending=True),
                         [DOK])

    def test_auch_schon_dekodiert(self):
        self.assertEqual(streams_in((["<Document/>"], [])), ["<Document/>"])

    def test_ein_leerer_abruf_bleibt_ein_abruf(self):
        self.assertEqual(streams_in(([], [])), [])


class TestWasKeineDokumenteSind(unittest.TestCase):
    """Alles andere bleibt, wie es ist -- eine MT940-Sammlung, eine Liste
    von Buchungen, oder was eine spaetere Bibliotheksfassung liefert."""

    def test_eine_liste_von_buchungen(self):
        self.assertIsNone(streams_in([Buchung({"amount": 1}),
                                      Buchung({"amount": 2})]))

    def test_auch_genau_zwei_buchungen(self):
        """Die verfaengliche Laenge: zwei Buchungen sind kein Paar."""
        zwei = [Buchung({"date": "2026-09-01"}), Buchung({"date": "2026-09-02"})]
        self.assertIsNone(streams_in(zwei))

    def test_eine_mt940_sammlung(self):
        self.assertIsNone(streams_in(object()))
        self.assertIsNone(streams_in(None))

    def test_eine_liste_falscher_laenge(self):
        self.assertIsNone(streams_in([DOK]))
        self.assertIsNone(streams_in(([DOK], [], [])))


class TestDerAbrufBenutztEsAnBeidenStellen(unittest.TestCase):
    """Der erste Abruf und die Wiederaufnahme nach der Freigabe kommen aus
    derselben Tiefe zurueck -- ausgepackt wurde bisher nur der erste."""

    def _quelle(self):
        with open(os.path.join(WURZEL, "utils", "fints_controller.py"),
                  encoding="utf-8") as handle:
            return handle.read()

    def _methode(self, kopf):
        quelle = self._quelle()
        return quelle.split(kopf)[1].split("\n    def ")[0]

    def test_der_erste_abruf(self):
        roh = self._methode("    def _get_transactions_raw(")
        self.assertIn("self._as_transactions(result, include_pending)", roh)

    def test_die_wiederaufnahme_nach_der_freigabe(self):
        geprueft = self._methode("    def _get_transactions_checked(")
        self.assertIn("return self._as_transactions(released)", geprueft)

    def test_ausgepackt_wird_an_einer_stelle(self):
        auspacken = self._methode("    def _as_transactions(")
        self.assertIn("camt_shape.streams_in(result, include_pending)",
                      auspacken)
        self.assertIn("if streams is None:", auspacken)
        self.assertIn("return result", auspacken)
        self.assertIn("camt053_to_dict(stream)", auspacken)
        # Und nur dort: eine zweite Kopie waere die naechste, die eine
        # Bibliotheksfassung ueberholt.
        self.assertEqual(self._quelle().count("camt053_to_dict(stream)"), 1)
