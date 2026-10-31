# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Nach welchem Schema der Auftrag gebaut wird -- .03, bis die Bank es nicht
mehr fuehrt.

Gemessen am 30.10.2026 an einem comdirect-Konto, eine Echtzeitueberweisung
ueber 80,00 EUR::

    9210 VOP-Auftrag ungueltig.
    9210 Sepa-Format nicht mehr gueltig. Bitte Software-Anbieter kontaktieren.
    3945 Freigabe nach VOP-Fehler nicht mehr moeglich.

kefiya baute jede Ueberweisung fest als pain.001.001.03. python-fints selbst
nimmt .09, wo die Bank es fuehrt. Entschieden: .09 nur dort, wo .03 fehlt --
Volksbank und Sparkasse fuehren beides und laufen mit .03.
"""

import datetime
import os
import unittest

from kefiya.utils import pain_dk
from kefiya.utils.sepa_descriptor import (DEFAULT_PAIN_001, PAIN_001_03,
                                          PAIN_001_09, URN_PAIN_001_09,
                                          choose, default_for, schema_for)

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

NUR_09 = ["urn:iso:std:iso:20022:tech:xsd:pain.001.001.09",
          "urn:iso:std:iso:20022:tech:xsd:pain.008.001.08"]
BEIDE = ["sepade:xsd:pain.001.001.03_GBIC_3.xsd",
         "sepade:xsd:pain.001.001.09_GBIC_5.xsd"]


def _read(*parts):
    with open(os.path.join(WURZEL, *parts), encoding="utf-8") as handle:
        return handle.read()


class TestDasSchema(unittest.TestCase):

    def test_wo_03_steht_bleibt_es_bei_03(self):
        self.assertEqual(schema_for(BEIDE), PAIN_001_03)

    def test_wo_03_fehlt_und_09_steht_wird_09_gebaut(self):
        self.assertEqual(schema_for(NUR_09), PAIN_001_09)

    def test_eine_unbekannte_liste_aendert_nichts(self):
        for liste in (None, [], ["sepade:xsd:pain.008.001.02.xsd"]):
            self.assertEqual(schema_for(liste), PAIN_001_03)

    def test_der_name_passt_zum_schema(self):
        self.assertEqual(choose(NUR_09, schema=PAIN_001_09,
                                default=default_for(PAIN_001_09)),
                         URN_PAIN_001_09)
        self.assertEqual(choose(["sepade:xsd:pain.001.001.09_GBIC_5.xsd"],
                                schema=PAIN_001_09,
                                default=default_for(PAIN_001_09)),
                         "sepade:xsd:pain.001.001.09_GBIC_5.xsd")
        self.assertEqual(default_for(PAIN_001_03), DEFAULT_PAIN_001)


class TestDerControllerBautNeu(unittest.TestCase):
    """Quelltext: die Zeile steht da, und an der richtigen Stelle."""

    def setUp(self):
        quelle = _read("utils", "fints_controller.py")
        self.senden = quelle[quelle.index("    def submit_sepa_transfer("):]
        self.senden = self.senden[:self.senden.index(
            "    def _pain_descriptor(")]
        self.umbau = quelle[quelle.index("    def _in_the_banks_schema("):]
        self.umbau = self.umbau[:self.umbau.index("    def _refuse_unsigned(")]

    def test_neu_gebaut_wird_bevor_etwas_hinausgeht(self):
        neu = self.senden.index("self._in_the_banks_schema(pain_xml,")
        self.assertLess(self.senden.index('kwargs["pain_descriptor"] ='), neu)
        self.assertLess(neu, self.senden.index(
            "self.fints_connection.sepa_transfer("))
        self.assertLess(neu, self.senden.index(
            "self._send_scheduled_transfer("))

    def test_ohne_umbau_wird_nicht_gesendet(self):
        self.assertIn("if rebuild_pain is None:", self.umbau)
        self.assertIn("frappe.throw(", self.umbau)

    def test_der_terminauftrag_traegt_denselben_namen(self):
        self.assertIn('pain_descriptor=kwargs["pain_descriptor"])',
                      self.senden)

    def test_beide_sendewege_bauen_neu(self):
        client = _read("utils", "client.py")
        self.assertIn(
            "rebuild_pain=lambda schema: doc.build_pain001(schema=schema)[0]",
            client)
        self.assertIn("rebuild_pain=lambda schema: build_pain001_for(\n"
                      "                docs, schema=schema)[0]", client)


try:
    from lxml import etree
    from sepaxml import SepaTransfer
except ImportError:  # pragma: no cover - only on a machine without them
    SepaTransfer = None


@unittest.skipIf(SepaTransfer is None, "sepaxml/lxml not installed")
class TestDie09IstGueltig(unittest.TestCase):
    """Ausgefuehrt, nicht gelesen: die .09 nach der DK-Korrektur gegen das
    Schema, das sepaxml mitbringt."""

    def test_nach_der_korrektur_noch_schemagueltig(self):
        import sepaxml

        sepa = SepaTransfer({"name": "Muster GmbH", "currency": "EUR",
                             "IBAN": "DE89999999990001111111",
                             "batch": False}, schema=PAIN_001_09, clean=False)
        sepa.add_payment({"name": "Erika Beispiel",
                          "IBAN": "DE89370400440532013000", "amount": 8000,
                          "description": "Test",
                          "execution_date": pain_dk.IMMEDIATE_EXECUTION,
                          "endtoend_id": "E2E-1"})
        xml = pain_dk.debtor_agent_not_provided(
            sepa.export(validate=True).decode())
        self.assertIn("<Othr><Id>NOTPROVIDED</Id></Othr>", xml)
        self.assertIn("<ReqdExctnDt><Dt>1999-01-01</Dt></ReqdExctnDt>", xml)
        xsd = etree.XMLSchema(etree.parse(os.path.join(
            os.path.dirname(sepaxml.__file__), "schemas",
            PAIN_001_09 + ".xsd")))
        self.assertTrue(xsd.validate(etree.fromstring(xml.encode())),
                        xsd.error_log.last_error)
        self.assertIsInstance(pain_dk.IMMEDIATE_EXECUTION, datetime.date)


if __name__ == "__main__":
    unittest.main()
