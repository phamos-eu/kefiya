# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Eine geparkte Freigabe muss denselben Gateway wiederfinden.

Die Volksbank verteilt hinter einer URL auf mehrere Gateways. Innerhalb
eines Laufs bleibt man bei einem -- python-fints haelt eine
``requests.session()`` je Verbindung, und deren Cookies halten einen dort.
Die Wiederaufnahme eines geparkten Dialogs geschieht aber in einer
spaeteren Anfrage, mit einer neuen Verbindung und leerem Cookie-Glas. Landet
sie beim anderen Gateway, kennt der den Dialog nicht::

    9800  FGW Gatewaywechsel A/B in Dialog/Nachricht BR6091007011049/3

kefiya meldet "No TAN status received", verwirft die geparkte Anfrage, und
die Freigabe, die der Nutzer in seiner Banking-App gerade gegeben hat, ist
verloren. Er gibt sie erneut -- Konto fuer Konto.

Belegt statt vermutet: alle zehn Vorkommen dieses Fehlers im
Fehlerprotokoll der Instanz stehen auf dem Wiederaufnahme-Weg
("the parked release could not be answered"), keines innerhalb eines
laufenden Dialogs.

Der erste Teil laeuft ohne Bank, ohne fints und ohne Bench. Der zweite
liest Quelltext und weiss das (CLAUDE.md).
"""

import json
import os
import unittest

from kefiya.utils import gateway_session

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))


class Glas(dict):
    """Ein requests-Cookie-Glas, soweit es hier gebraucht wird."""

    def get_dict(self):
        return dict(self)

    def set(self, name, wert):
        self[name] = wert


class Verbindung:
    def __init__(self, cookies=None):
        self.session = type("S", (), {})()
        if cookies is not None:
            self.session.cookies = cookies


class Client:
    def __init__(self, cookies=None, verbindung=True):
        if verbindung:
            self.connection = Verbindung(cookies)


def _quelle(*teile):
    with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
        return handle.read()


class TestWasGemerktWird(unittest.TestCase):

    def test_die_cookies_der_verbindung(self):
        client = Client(Glas({"BIGipServer": "A", "JSESSIONID": "x"}))
        self.assertEqual(gateway_session.cookies_of(client),
                         {"BIGipServer": "A", "JSESSIONID": "x"})

    def test_auch_ein_glas_ohne_get_dict(self):
        client = Client({"BIGipServer": "B"})
        self.assertEqual(gateway_session.cookies_of(client),
                         {"BIGipServer": "B"})

    def test_keine_verbindung_ist_kein_fehler(self):
        """Eine Bibliotheksfassung, die anders gebaut ist, kostet die
        Klebrigkeit -- nicht den Abruf."""
        self.assertEqual(gateway_session.cookies_of(Client(verbindung=False)),
                         {})
        self.assertEqual(gateway_session.cookies_of(Client(None)), {})
        self.assertEqual(gateway_session.cookies_of(None), {})

    def test_ohne_cookies_wird_nichts_aufgehoben(self):
        self.assertIsNone(gateway_session.to_blob({}))
        self.assertIsNone(gateway_session.to_blob(None))

    def test_hin_und_zurueck(self):
        cookies = {"BIGipServer": "A", "JSESSIONID": "x"}
        blob = gateway_session.to_blob(cookies)
        self.assertIsInstance(blob, bytes)
        self.assertEqual(gateway_session.from_blob(blob), cookies)

    def test_unlesbares_haelt_nichts_an(self):
        for kaputt in (b"kein json", "{", json.dumps([1, 2]), b"", None):
            self.assertEqual(gateway_session.from_blob(kaputt), {})


class TestWasZurueckgelegtWird(unittest.TestCase):

    def test_auf_eine_frische_verbindung(self):
        glas = Glas()
        client = Client(glas)
        self.assertTrue(gateway_session.restore(client, {"BIGipServer": "A"}))
        self.assertEqual(glas, {"BIGipServer": "A"})

    def test_auch_ohne_set(self):
        glas = {}
        client = Client(glas)
        self.assertTrue(gateway_session.restore(client, {"a": "1"}))
        self.assertEqual(glas, {"a": "1"})

    def test_nichts_zu_legen(self):
        glas = Glas()
        self.assertFalse(gateway_session.restore(Client(glas), {}))
        self.assertEqual(glas, {})

    def test_keine_verbindung(self):
        self.assertFalse(
            gateway_session.restore(Client(verbindung=False), {"a": "1"}))


class TestJederPausierteDialogWirdWiedergefunden(unittest.TestCase):

    def _zeilen(self, quelle, was):
        return [i for i, zeile in enumerate(quelle.splitlines())
                if was in zeile and not zeile.strip().startswith("#")]

    def test_wo_pausiert_wird_wird_der_gateway_gemerkt(self):
        quelle = _quelle("utils", "fints_controller.py")
        pausen = self._zeilen(quelle, ".pause_dialog()")
        merken = self._zeilen(quelle, "stored_gateway_blob = gateway_session")
        self.assertEqual(len(pausen), len(merken),
                         "Jeder pausierte Dialog braucht seinen Gateway")
        for pause, gemerkt in zip(pausen, merken):
            self.assertLess(pause, gemerkt)

    def test_wo_fortgesetzt_wird_wird_er_zurueckgelegt(self):
        treffer = []
        for datei in (("utils", "fints_controller.py"),
                      ("utils", "fints_tan_session.py")):
            quelle = _quelle(*datei)
            zurueck = self._zeilen(quelle, "self._return_to_the_same_gateway()")
            fortsetzen = self._zeilen(
                quelle, "self.fints_connection.resume_dialog(")
            self.assertEqual(len(zurueck), len(fortsetzen), datei)
            for legen, weiter in zip(zurueck, fortsetzen):
                self.assertLess(legen, weiter, datei)
            treffer += fortsetzen
        self.assertTrue(treffer, "Es gibt keine Wiederaufnahme mehr?")

    def test_ohne_gemerkten_gateway_geht_es_weiter_wie_bisher(self):
        strecke = _quelle("utils", "fints_tan_session.py")
        methode = strecke.split("    def _return_to_the_same_gateway(")[1] \
                         .split("\n    def ")[0]
        self.assertIn("except Exception:", methode)
        self.assertIn("pass", methode)

    def test_das_feld_gehoert_dem_dialog(self):
        """stored_* -- damit ein zweiter Zugang desselben Hauses es nicht
        erbt (siehe login_siblings.belongs_to_one_dialog)."""
        with open(os.path.join(WURZEL, "kefiya", "doctype", "kefiya_login",
                               "kefiya_login.json"), encoding="utf-8") as handle:
            felder = [f["fieldname"] for f in json.load(handle)["fields"]
                      if f.get("fieldname")]
        self.assertIn("stored_gateway_state", felder)

    def test_geloescht_wird_es_mit_dem_rest(self):
        doc = _quelle("kefiya", "doctype", "kefiya_login", "kefiya_login.py")
        leeren = doc.split("    def clear_fints_caches(")[1] \
                    .split("\n    @")[0]
        self.assertIn("self.stored_gateway_blob = None", leeren)
