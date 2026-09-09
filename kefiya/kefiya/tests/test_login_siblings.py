# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein zweiter Zugang erbt die Zugangsdaten, nicht das Gespraech.

Die Volksbank nennt unter einer einzigen VR-NetKey vierzig Konten. kefiya
braucht je Konto einen Kefiya Login, und bisher wurde jeder von Hand
getippt -- mitsamt PIN und Produkt-ID. Zwei Konten fehlten deshalb schlicht,
und eine falsch getippte PIN sperrt bei drei Versuchen den ganzen Zugang.

Was ein neuer Login uebernimmt, steht in login_siblings.COPIED. Was er
nicht uebernehmen darf, ist der gespeicherte Dialogzustand: eine geparkte
TAN oder eine offene VoP-Freigabe gehoert dem Gespraech, in dem sie
entstanden ist. Ein Login, der sie erbt, wartet auf eine Freigabe, die es
nie gab.

Der erste Teil laeuft ohne Bank und ohne Bench. Der zweite liest Quelltext
und weiss das (CLAUDE.md): er sagt, dass die Zeile da steht, nicht, dass
sie laeuft.
"""

import json
import os
import unittest

from kefiya.utils import login_siblings

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

VB = "DE79670923000034079536"
BRILU = "DE83670923000033312679"
SCHON_DA = "DE16670923000033080697"


def _quelle(*teile):
    with open(os.path.join(WURZEL, *teile), encoding="utf-8") as handle:
        return handle.read()


class TestWelcheKontenNochFehlen(unittest.TestCase):

    def test_was_die_bank_nennt_und_keiner_abruft(self):
        offen = login_siblings.unclaimed(
            json.dumps([SCHON_DA, VB, BRILU]), [SCHON_DA])
        self.assertEqual(offen, [VB, BRILU])

    def test_die_reihenfolge_ist_die_der_bank(self):
        self.assertEqual(
            login_siblings.unclaimed(json.dumps([BRILU, VB]), []),
            [BRILU, VB])

    def test_geschrieben_wie_der_nutzer_sie_schreibt(self):
        """Mit Leerzeichen und klein -- dieselbe IBAN."""
        self.assertEqual(
            login_siblings.unclaimed(json.dumps([VB]),
                                     ["de79 6709 2300 0034 0795 36"]),
            [])

    def test_zweimal_genannt_bleibt_ein_konto(self):
        self.assertEqual(login_siblings.unclaimed(json.dumps([VB, VB]), []),
                         [VB])

    def test_ohne_kontenliste_wird_nichts_geraten(self):
        """Solange "Konten laden" nicht gedrueckt wurde, hat die Bank nichts
        gesagt -- und die Bank ist der Schiedsrichter."""
        self.assertEqual(login_siblings.named_by_bank(None), [])
        self.assertEqual(login_siblings.named_by_bank(""), [])
        self.assertEqual(login_siblings.named_by_bank("kein json"), [])
        self.assertEqual(login_siblings.named_by_bank('{"a": 1}'), [])

    def test_auch_schon_als_liste(self):
        self.assertEqual(login_siblings.named_by_bank([VB]), [VB])

    def test_leere_eintraege_zaehlen_nicht(self):
        self.assertEqual(login_siblings.named_by_bank(json.dumps([VB, "", None])),
                         [VB])


class TestWasNichtMitkopiertWird(unittest.TestCase):

    def test_nichts_aus_einem_laufenden_gespraech(self):
        geerbt = [f for f in login_siblings.COPIED
                  if login_siblings.belongs_to_one_dialog(f)]
        self.assertEqual(geerbt, [], "Diese Felder gehoeren einem Dialog, "
                                     "nicht dem Zugang")

    def test_der_dialogzustand_des_doctypes_ist_erkannt(self):
        """Was das Dokument an Gespraech speichert, muss die Regel auch als
        solches sehen -- sonst schuetzt sie nur die Felder von heute."""
        pfad = os.path.join(WURZEL, "kefiya", "doctype", "kefiya_login",
                            "kefiya_login.json")
        with open(pfad, encoding="utf-8") as handle:
            felder = [f["fieldname"] for f in json.load(handle)["fields"]
                      if f.get("fieldname")]
        zustand = [f for f in felder
                   if f.startswith("stored_") or f.startswith("vop_")]
        self.assertTrue(zustand)
        for feld in zustand:
            self.assertTrue(login_siblings.belongs_to_one_dialog(feld), feld)

    def test_die_zugangsdaten_sind_dabei(self):
        for feld in ("fints_url", "blz", "fints_login", "fints_password",
                     "product_id"):
            self.assertIn(feld, login_siblings.COPIED)

    def test_die_geheimen_felder_sind_benannt(self):
        for feld in login_siblings.SECRETS:
            self.assertIn(feld, login_siblings.COPIED)


class TestDerZugangWirdAufDemServerKopiert(unittest.TestCase):

    def setUp(self):
        self.quelle = _quelle("kefiya", "doctype", "kefiya_login",
                              "kefiya_login.py")
        self.anlegen = self.quelle.split("    def add_account(")[1] \
                                  .split("\n    def ")[0]

    def test_die_pin_kommt_aus_get_password(self):
        """Im Dokument steht sie verschluesselt. Wer sie mit get() liest,
        vererbt eine Maske -- und drei Anmeldungen damit sperren die
        Kennung."""
        uebernahme = self.quelle.split("    def _carried_over(")[1] \
                                .split("\n    def ")[0]
        self.assertIn("login_siblings.SECRETS", uebernahme)
        self.assertIn("self.get_password(fieldname", uebernahme)

    def test_die_pin_steht_nicht_im_dialog(self):
        dialog = _quelle("kefiya", "doctype", "kefiya_login",
                         "kefiya_login.js")
        dialog = dialog.split("function kefiya_add_account_dialog(")[1]
        for feld in ("fints_password", "product_id", "fints_login"):
            self.assertNotIn(feld, dialog)

    def test_die_bank_entscheidet_ob_es_das_konto_gibt(self):
        self.assertIn("login_siblings.named_by_bank(self.iban_list)",
                      self.anlegen)
        self.assertIn("frappe.throw", self.anlegen)

    def test_kein_konto_wird_zweimal_abgerufen(self):
        self.assertIn("self.accounts_without_login()", self.anlegen)

    def test_die_iban_des_bankkontos_muss_passen(self):
        """Sonst holt der neue Zugang die Umsaetze des einen Kontos und
        bucht sie auf ein anderes."""
        self.assertIn('frappe.get_doc("Bank Account", bank_account)',
                      self.anlegen)
        self.assertIn("login_siblings.normalise(konto.iban) != iban",
                      self.anlegen)

    def test_uebernommen_wird_genau_die_liste(self):
        self.assertIn("for feld in login_siblings.COPIED:", self.anlegen)
        self.assertIn("self._carried_over(feld)", self.anlegen)

    def test_geschrieben_wird_nur_mit_schreibrecht(self):
        self.assertIn('frappe.has_permission(ptype="write", doc=self,'
                      ' throw=True)', self.anlegen)
        self.assertIn('frappe.has_permission("Kefiya Login",'
                      ' ptype="create", throw=True)', self.anlegen)
