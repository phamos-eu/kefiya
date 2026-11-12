# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein Limit, das die Bank nicht nennt, wird von Hand gepflegt.

Die Maschinerie dafuer war vollstaendig: transfer_limit liest das Limit aus
HIUPD, check_batch prueft einen Stapel als Ganzes vor dem Senden, und eine zu
grosse Zahlung laesst sich ueber Tage aufteilen. Nur konnte niemand eine Zahl
hineinschreiben -- die drei Felder standen auf ``read_only``, und die Bank
nennt fuer die meisten Konten gar kein Limit. Gemessen an einer Instanz: 24
von 59 Zugaengen geprueft und alle leer.

Was dann passiert, hat einen Auftrag ueber 5.000 EUR vier Tage lang gekostet.
Die Bank lehnt wegen des Tageslimits ab und sagt dazu nur::

    9050  Die Nachricht enthaelt Fehler.
    9010  Der Auftrag wurde nicht ausgefuehrt.

Kein Wort von einem Limit, und die TAN war dafuer schon ausgegeben. Gesucht
wurde daraufhin am Zeichensatz, am Echtzeit-Kennzeichen und am
Dialogzustand -- an allem ausser der Ursache.

WER GEWINNT, ist abgesprochen und ist NICHT "der letzte Schreiber": ein Wert,
den die Bank MELDET, ersetzt den eingetragenen; eine Bank, die nichts oder
Null meldet, laesst ihn stehen. Diese Tests halten beide Haelften fest --
dass die Felder beschreibbar sind, und dass der Abruf sie nicht aus Versehen
leert.

Ohne Bench: transfer_limit importiert frappe am Dateikopf (siehe
test_transfer_limit, das deshalb nur auf der Instanz laeuft). Das Doctype
wird als JSON gelesen, die Regel als Quelltext -- ein gruener Test hier
heisst "die Zeile steht da", nicht "der Code laeuft".
"""

import json
import os
import re
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
APP = os.path.normpath(os.path.join(HIER, "..", ".."))

#: Die drei Felder, die ein Mensch fuellen koennen muss.
VON_HAND = ("transfer_limit_amount", "transfer_limit_type",
            "transfer_limit_days")


def _read(*parts):
    with open(os.path.join(APP, *parts), encoding="utf-8") as handle:
        return handle.read()


def _felder():
    meta = json.loads(_read("kefiya", "doctype", "kefiya_login",
                            "kefiya_login.json"))
    return {f["fieldname"]: f for f in meta["fields"]}


def _block(source, header):
    """One def, by indentation."""
    # [ \t]* und nicht \s*: \s trifft auch Zeilenumbrueche, und die Suche
    # begann dann in einer Leerzeile und mass deren Einrueckung.
    hit = re.search(r"^([ \t]*)def %s\(" % re.escape(header), source, re.M)
    assert hit, header
    indent = len(hit.group(1))
    lines = source[hit.start():].split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
    return "\n".join(out)


class TestDasLimitLaesstSichEintragen(unittest.TestCase):

    def test_die_drei_felder_sind_beschreibbar(self):
        felder = _felder()
        for name in VON_HAND:
            self.assertIn(name, felder)
            self.assertFalse(
                felder[name].get("read_only"),
                "%s ist read_only -- dann kann niemand das Limit eintragen,"
                " und bei einer Bank, die keines nennt, erfaehrt man es erst"
                " von der Ablehnung nach der TAN." % name)

    def test_der_zeitstempel_bleibt_ein_protokoll(self):
        """Wann die Bank gefragt wurde, stellt niemand ein."""
        felder = _felder()
        self.assertTrue(
            felder["transfer_limit_checked_on"].get("read_only"))

    def test_der_abschnitt_sagt_die_regel(self):
        """Wer die Zahl eintraegt, muss ohne Rueckfrage wissen, was beim
        naechsten Abruf damit passiert."""
        beschreibung = _felder()["transfer_limit_section"].get(
            "description") or ""
        self.assertIn("enter the limit here", beschreibung)
        self.assertIn("replaces", beschreibung)
        self.assertIn("zero, leaves it alone", beschreibung)
        self.assertNotIn("(from the bank)",
                         _felder()["transfer_limit_section"].get("label", ""))


class TestDerAbrufUeberschreibtNurMitEinemWert(unittest.TestCase):
    """Die andere Haelfte der Abrede. Ohne sie waere das Eintragen sinnlos:
    der naechste Nachtlauf leerte das Feld wieder."""

    def setUp(self):
        self.quelle = _read("utils", "transfer_limit.py")

    def test_eine_null_wird_kein_treffer(self):
        koerper = _block(self.quelle, "pick_binding_limit")
        self.assertIn('flt(r.get("amount")) > 0', koerper)
        self.assertIn("return None", koerper)

    def test_und_ohne_treffer_bleibt_das_eingetragene_stehen(self):
        koerper = _block(self.quelle, "refresh_transfer_limit")
        ohne_treffer = koerper.split("if not chosen:")[1].split("return")[0]
        self.assertIn("login.transfer_limit_checked_on = now_datetime()",
                      ohne_treffer)
        for name in VON_HAND:
            self.assertNotIn(
                "login.%s =" % name, ohne_treffer,
                "%s wird geleert, wenn die Bank kein Limit nennt -- damit"
                " waere jede von Hand eingetragene Zahl nach dem naechsten"
                " Abruf weg." % name)

    def test_ein_gemeldeter_wert_gewinnt(self):
        koerper = _block(self.quelle, "refresh_transfer_limit")
        mit_treffer = koerper.split("return {\"stored\": False")[1]
        for name in VON_HAND:
            self.assertIn("login.%s =" % name, mit_treffer)

    def test_die_pruefung_unterscheidet_die_beiden_nicht(self):
        """Ein von Hand eingetragenes Limit wird genau wie das der Bank
        benutzt. check_batch liest die Felder und fragt nicht, woher sie
        kommen -- und darf das auch nicht."""
        koerper = _block(self.quelle, "check_batch")
        self.assertIn("login.transfer_limit_amount", koerper)
        self.assertIn("login.transfer_limit_type", koerper)
        self.assertNotIn("checked_on", koerper)

    def test_leer_heisst_unbekannt_und_nicht_unbegrenzt(self):
        koerper = _block(self.quelle, "check_batch")
        self.assertIn("if not limit or not limit_type:", koerper)
        self.assertIn("no limit known", koerper)


if __name__ == "__main__":
    unittest.main()
