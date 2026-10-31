# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Um 05:01 bestaetigt niemand eine Freigabe in einer Banking-App.

Der Zeitplan baut seinen Controller mit interactive=False -- er WEISS also,
dass niemand da ist. Trotzdem lief bei einer verlangten TAN::

    trusted_client_context -> is_tan_required_and_requested -> ask_for_tan

und ask_for_tan parkt die Anfrage und verschickt eine Aufforderung, ohne je
zu fragen, ob jemand zuhoert. Danach TanInteractionRequired; die Schleife
fing jede Ausnahme und machte beim naechsten Konto dasselbe.

Ergebnis, jede Nacht seit dem 08.10.2026: zehn Protokollzeilen und zehn
geparkte Freigabeanfragen, eine je Konto des Zugangs. Die Bank haelt fuer
jede davon einen Dialog offen -- so steht es im Docstring von
_park_tan_challenge. Niemand bestaetigte sie. Sie veralteten, und eine
veraltete antwortete am 11.10. um 06:01::

    9050 Die Nachricht enthaelt Fehler.
    9800 Dialog abgebrochen
    9210 Auftrag abgelehnt - Auftrag auf Kundenwunsch abgebrochen.

Zwei Zusagen, die diese Tests halten: unbeaufsichtigt wird nichts geparkt,
und ein Zugang wird einmal gefragt und nicht zehnmal.

Quelltext gelesen, weil beide Module frappe oben importieren und ausserhalb
einer Bench nicht laden.
"""

import os
import re
import unittest


def _lies(*teile):
    pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *teile)
    with open(pfad, encoding="utf-8") as griff:
        return griff.read()


def _funktion(quelle, name):
    """Eine def, nach Einrueckung."""
    # [ \t]* und nicht \s*: \s trifft auch Zeilenumbrueche.
    treffer = re.search(r"^([ \t]*)def %s\(" % re.escape(name), quelle, re.M)
    assert treffer, name
    tiefe = len(treffer.group(1))
    zeilen = quelle[treffer.start():].split("\n")
    raus = [zeilen[0]]
    for zeile in zeilen[1:]:
        if zeile.strip() and (len(zeile) - len(zeile.lstrip())) <= tiefe:
            break
        raus.append(zeile)
    return "\n".join(raus)


class TestOhneJemandenWirdNichtGefragt(unittest.TestCase):

    def setUp(self):
        self.body = _funktion(_lies("utils", "fints_controller.py"),
                              "is_tan_required_and_requested")

    def test_der_schalter_wird_gelesen(self):
        self.assertIn("if not self.interactive.enabled:", self.body)

    def test_und_zwar_bevor_gefragt_wird(self):
        """Sonst ist die Anfrage geparkt, bevor jemand merkt, dass niemand
        da ist."""
        self.assertLess(self.body.index("self.interactive.enabled"),
                        self.body.index("self.ask_for_tan(response)"))

    def test_die_antwort_bleibt_dieselbe(self):
        """Eine TAN ist noetig -- das aendert sich nicht dadurch, dass
        niemand sie geben kann. Der Aufrufer loest weiter aus."""
        stelle = self.body.index("if not self.interactive.enabled:")
        danach = self.body[stelle:stelle + 120]
        self.assertIn("return True", danach)

    def test_und_eine_antwort_ohne_tan_bleibt_falsch(self):
        self.assertIn("if not isinstance(response, NeedTANResponse):",
                      self.body)
        self.assertLess(self.body.index("NeedTANResponse"),
                        self.body.index("self.interactive.enabled"))


class TestEinZugangWirdEinmalGefragt(unittest.TestCase):
    """Ein Zugang, eine Kennung, eine Anmeldung: meldet das erste Login
    "TAN noetig", melden die uebrigen dasselbe."""

    def setUp(self):
        self.quelle = _lies("kefiya", "doctype", "kefiya_schedule",
                            "kefiya_schedule.py")
        self.lauf = _funktion(self.quelle, "scheduled_import_fints_payments")

    def test_die_ausnahme_wird_eigens_gefangen(self):
        self.assertIn("except TanInteractionRequired:", self.lauf)
        self.assertLess(self.lauf.index("except TanInteractionRequired:"),
                        self.lauf.index("except Exception:"),
                        "Die allgemeine Klausel wuerde sie sonst zuerst"
                        " fangen.")

    def test_der_zugang_wird_gemerkt(self):
        self.assertIn("tan_verlangt.add(zugang)", self.lauf)

    def test_und_die_uebrigen_uebersprungen(self):
        self.assertIn("if zugang and zugang in tan_verlangt:", self.lauf)
        self.assertLess(self.lauf.index("if zugang and zugang in tan_verlangt:"),
                        self.lauf.index("tan_verlangt.add(zugang)"))

    def test_der_schluessel_kommt_aus_der_gemeinsamen_regel(self):
        """Was ein Zugang ist, steht an einer Stelle -- dieselbe, die das
        Verwerfen eines unbrauchbaren Zustands benutzt."""
        self.assertIn("login_siblings.access_key(blz, fints_login)",
                      self.lauf)

    def test_er_ist_gebunden_bevor_etwas_schiefgehen_kann(self):
        """Der except-Zweig liest ihn. Faellt etwas aus, bevor er
        feststeht, waere der Name ungebunden -- oder er truege den Zugang
        der vorigen Runde und wuerde fremde Logins ueberspringen."""
        self.assertIn("zugang = None", self.lauf)
        self.assertLess(self.lauf.index("zugang = None"),
                        self.lauf.index("try:"))

    def test_und_es_gibt_keinen_traceback_dafuer(self):
        """Zehn Tracebacks je Nacht fuer einen Sachverhalt begraben die
        echten Fehlschlaege."""
        notiz = _funktion(self.quelle, "_log_tan_wanted")
        self.assertNotIn("get_traceback", notiz)
        self.assertIn("nobody is there", notiz)

    def test_die_erholung_bleibt_dieselbe(self):
        """Zuruecksetzen, aufschreiben, Versuch stempeln, festschreiben --
        nur die Notiz unterscheidet sich."""
        gemeinsam = _funktion(self.quelle, "_recover_from_login")
        self.assertIn("frappe.db.rollback()", gemeinsam)
        self.assertIn("aufschreiben(login_name)", gemeinsam)
        self.assertIn("_record_fetch_attempt(login_name)", gemeinsam)
        for name in ("_recover_from_failed_login", "_recover_from_tan_wanted"):
            self.assertIn("_recover_from_login(login_name,",
                          _funktion(self.quelle, name))


class TestWasEinZugangIst(unittest.TestCase):

    def test_ohne_kennung_kein_schluessel(self):
        from kefiya.utils import login_siblings
        self.assertIsNone(login_siblings.access_key(None, "kennung"))
        self.assertIsNone(login_siblings.access_key("67200000", ""))

    def test_und_sonst_blz_und_kennung(self):
        from kefiya.utils import login_siblings
        self.assertEqual(login_siblings.access_key("99999999", "nutzer1"),
                         ("99999999", "nutzer1"))

    def test_zwei_kennungen_sind_zwei_zugaenge(self):
        from kefiya.utils import login_siblings
        self.assertNotEqual(
            login_siblings.access_key("99999999", "nutzer1"),
            login_siblings.access_key("99999999", "nutzer2"))


if __name__ == "__main__":
    unittest.main()
