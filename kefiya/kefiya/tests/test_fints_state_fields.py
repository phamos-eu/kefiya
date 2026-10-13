# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein Check ist NOT NULL, und None darauf ist kein leeres Feld.

Am 05.10.2026 scheiterte das Verwerfen eines unbrauchbaren
Verbindungszustands an

    pymysql.err.IntegrityError: (1048, "Column
    'stored_tan_state_decoupled' cannot be null")

-- nachdem eine Ueberweisung schon auf der Leitung war. Ein Dokument-save()
wandelt None still in 0 um, frappe.db.set_value schreibt es in rohes SQL.
Die beiden Wege verwerfen denselben Zustand, also muessen sie dieselbe
Feldliste mit datenbankfaehigen Werten benutzen.

Diese Tests lesen kefiya_login.json. Sie laufen ohne Bench und melden sich,
bevor ein neues Zustandsfeld beim Senden auffaellt.
"""

import json
import pathlib
import unittest

from kefiya.utils import fints_state_fields

HIER = pathlib.Path(__file__).resolve()
WURZEL = HIER.parents[3]
DOCTYPE = WURZEL / "kefiya/kefiya/doctype/kefiya_login/kefiya_login.json"


def felder():
    daten = json.loads(DOCTYPE.read_text(encoding="utf-8"))
    return {f["fieldname"]: f for f in daten["fields"]}


class TestJedesFeldGibtEsAuch(unittest.TestCase):

    def test_kein_name_ins_leere(self):
        vorhanden = felder()
        for name in fints_state_fields.CLEARED:
            self.assertIn(name, vorhanden,
                          "%s steht in CLEARED, aber nicht im Doctype" % name)


class TestDerWertPasstZurSpalte(unittest.TestCase):
    """Das ist der Test, den es am 05.10.2026 nicht gab."""

    def test_ein_check_bekommt_null_als_zahl(self):
        vorhanden = felder()
        for name, wert in fints_state_fields.CLEARED.items():
            if vorhanden[name].get("fieldtype") == "Check":
                self.assertEqual(
                    wert, 0,
                    "%s ist ein Check -- in der Datenbank int(1) NOT NULL."
                    " None darauf scheitert mit Fehler 1048." % name)

    def test_alles_andere_wird_geleert(self):
        vorhanden = felder()
        for name, wert in fints_state_fields.CLEARED.items():
            if vorhanden[name].get("fieldtype") != "Check":
                self.assertIsNone(wert, name)


class TestKeinZustandsfeldFehlt(unittest.TestCase):
    """Ein neues stored_*-Feld, das hier nicht auftaucht, ueberlebt das
    Verwerfen -- und damit ueberlebt der kaputte Zustand."""

    def test_alle_stored_felder_sind_dabei(self):
        for name, feld in felder().items():
            if not name.startswith("stored_"):
                continue
            self.assertIn(
                name, fints_state_fields.CLEARED,
                "%s ist ein gespeicherter Zustand, wird aber nicht"
                " verworfen." % name)


class TestDieIdentitaetBleibtStehen(unittest.TestCase):
    """account_iban sagt, welches Konto dieses Login abruft. Ein
    automatisches Verwerfen nach einem Fehlschlag darf einem
    Geschwister-Login nicht das Konto unter den Fuessen wegziehen --
    clear_fints_caches() darf das, weil ein Mensch den Knopf gedrueckt hat."""

    def test_das_konto_wird_nicht_mitgeloescht(self):
        self.assertNotIn("account_iban", fints_state_fields.CLEARED)
        self.assertNotIn("iban_list", fints_state_fields.CLEARED)


class TestDieVorlageBleibtUnberuehrt(unittest.TestCase):

    def test_cleared_gibt_eine_kopie(self):
        eine = fints_state_fields.cleared()
        eine["stored_client_state"] = "angefasst"
        self.assertIsNone(fints_state_fields.CLEARED["stored_client_state"])
        self.assertEqual(fints_state_fields.cleared(),
                         dict(fints_state_fields.CLEARED))


class TestDasModulBrauchtKeineBench(unittest.TestCase):

    def test_es_importiert_nichts(self):
        quelle = (WURZEL / "kefiya/utils/fints_state_fields.py").read_text(
            encoding="utf-8")
        for zeile in quelle.split("\n"):
            self.assertFalse(zeile.startswith(("import ", "from ")), zeile)


if __name__ == "__main__":
    unittest.main()
