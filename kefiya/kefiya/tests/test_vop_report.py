# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der Name, den die Bank haelt -- aus ihrem eigenen Bericht gelesen.

Die Volksbank antwortet auf die Empfaengerpruefung nicht mit dem kleinen
Ergebnisfeld, das python-fints kennt, sondern mit einem vollstaendigen
pain.002-Dokument (ihre Bankparameter sagen report_complete='V'). Das las
niemand: Der Dialog zeigte "Diese Bank liefert ihr Pruefergebnis in einem
Format, das diese App nicht lesen kann" und bat um die Freigabe von Geld.

Diese Regel laeuft ohne Bank und ohne Bench.
"""

import os
import unittest

from kefiya.utils.vop_report import read

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

NS = "urn:iso:std:iso:20022:tech:xsd:pain.002.001.10"

EINE = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="{ns}">
 <CstmrPmtStsRpt>
  <GrpHdr><MsgId>M1</MsgId></GrpHdr>
  <OrgnlPmtInfAndSts>
   <OrgnlPmtInfId>P1</OrgnlPmtInfId>
   <TxInfAndSts>
    <OrgnlEndToEndId>KEF-TRF-2026-00001-1</OrgnlEndToEndId>
    <TxSts>ACCP</TxSts>
    <StsRsnInf>
     <Rsn><Prtry>RVMC</Prtry></Rsn>
     <AddtlInf>Max Mustermann</AddtlInf>
    </StsRsnInf>
    <OrgnlTxRef><Cdtr><Nm>Max Mustermann</Nm></Cdtr></OrgnlTxRef>
   </TxInfAndSts>
  </OrgnlPmtInfAndSts>
 </CstmrPmtStsRpt>
</Document>""".format(ns=NS)

#: Dieselbe Nachricht ohne Namensraum -- eine pain.002 traegt je nach
#: Auspraegung verschiedene, und ein Leser, der auf einen besteht, findet
#: bei der naechsten Bank nichts.
OHNE_NS = EINE.replace(' xmlns="{0}"'.format(NS), "")

ZWEI = EINE.replace(
    "</OrgnlPmtInfAndSts>",
    "<TxInfAndSts>"
    "<OrgnlEndToEndId>KEF-TRF-2026-00001-2</OrgnlEndToEndId>"
    "<TxSts>RJCT</TxSts>"
    "<StsRsnInf><Rsn><Cd>RVNM</Cd></Rsn></StsRsnInf>"
    "</TxInfAndSts></OrgnlPmtInfAndSts>")


class TestWasDieBankSagt(unittest.TestCase):

    def test_der_name_und_das_urteil(self):
        gelesen = read(EINE)
        self.assertEqual(gelesen["status"], "ACCP")
        self.assertEqual(gelesen["result"], "RVMC")
        self.assertEqual(gelesen["bank_name"], "Max Mustermann")

    def test_auch_ohne_namensraum(self):
        self.assertEqual(read(OHNE_NS)["bank_name"], "Max Mustermann")

    def test_auch_als_bytes(self):
        self.assertEqual(read(EINE.encode("utf-8"))["result"], "RVMC")

    def test_ein_code_gilt_wo_kein_eigener_steht(self):
        ohne_prtry = EINE.replace("<Prtry>RVMC</Prtry>", "<Cd>RVNA</Cd>")
        self.assertEqual(read(ohne_prtry)["result"], "RVNA")

    def test_der_zusatz_wiederholt_den_namen_nicht(self):
        self.assertEqual(read(EINE)["detail"], "")
        mit = EINE.replace(
            "</StsRsnInf>",
            "<AddtlInf>Pruefung nach EU 2024/886</AddtlInf></StsRsnInf>")
        self.assertEqual(read(mit)["detail"], "Pruefung nach EU 2024/886")


class TestWasKeinNameIst(unittest.TestCase):
    """AddtlInf traegt bei manchen Instituten den Namen und bei anderen
    einen Satz ueber die Pruefung. Ein Code ist kein Name."""

    def test_ein_code_wird_nicht_als_name_gezeigt(self):
        code = EINE.replace("<AddtlInf>Max Mustermann</AddtlInf>",
                            "<AddtlInf>NOMATCH</AddtlInf>")
        # Der Rueckfall auf OrgnlTxRef traegt hier denselben Namen; ohne ihn
        # bliebe das Feld leer.
        ohne = code.replace(
            "<OrgnlTxRef><Cdtr><Nm>Max Mustermann</Nm></Cdtr>"
            "</OrgnlTxRef>", "")
        self.assertEqual(read(ohne)["bank_name"], "")
        self.assertIn("NOMATCH", read(ohne)["detail"])


class TestWennNichtsZuLesenIst(unittest.TestCase):
    """Nie eine Ausnahme auf dem Zahlungsweg -- und nie geraten."""

    def test_nichts_ist_nichts(self):
        for eingabe in (None, b"", "", b"kein xml", "<Document/>"):
            gelesen = read(eingabe)
            self.assertEqual(gelesen, {"status": "", "result": "",
                                       "bank_name": "", "detail": ""},
                             str(eingabe)[:20])

    def test_mehrere_zahlungen_ohne_angabe_werden_nicht_geraten(self):
        """Eine von zweien zu zeigen waere geraten."""
        self.assertEqual(read(ZWEI)["result"], "")

    def test_mit_angabe_die_richtige(self):
        self.assertEqual(
            read(ZWEI, "KEF-TRF-2026-00001-2")["status"], "RJCT")
        self.assertEqual(
            read(ZWEI, "KEF-TRF-2026-00001-1")["result"], "RVMC")


class TestDerControllerZeigtEs(unittest.TestCase):

    def _quelle(self, *teile):
        with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
            return handle.read()

    def test_der_bericht_wird_gelesen_wo_das_urteil_fehlt(self):
        quelle = self._quelle("utils", "fints_controller.py")
        antwort = quelle.split("def _vop_answer(")[1].split("\n    def ")[0]
        self.assertIn('vop_report.read(', antwort)
        self.assertIn('getattr(vop_result, "payment_status_report", None)',
                      antwort)
        # Das gelesene Feld gewinnt nie gegen das der Bibliothek.
        self.assertIn('vop_rule.result_from(vop_result) or bericht["result"]',
                      antwort)

    def test_der_dialog_zeigt_was_da_ist(self):
        js = self._quelle("public", "js", "controllers", "vop_prompt.js")
        block = js.split("function answerBlock(")[1].split("\n    function ")[0]
        self.assertIn("answer.bank_name", block)
        self.assertIn("answer.status", block)
        self.assertIn("answer.detail", block)
        # Und den Satz "nicht lesbar" nur noch, wenn wirklich nichts da ist.
        self.assertIn("saidSomething", block)
