# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Which of two identical rows a second pass wrote.

statement_repair declined this case in writing -- "nothing distinguishes the
two rows ... no field here tells them apart" -- and that was true of the
fields it read. It never read ``creation``, and on the 21.05.2026 run that is
what separates the two populations:

    2,508 pairs   6 to 93 seconds apart, eight over an hour  -> a second pass
      501 pairs   0 to 4 seconds apart                       -> one pass

Rows written in one pass are consecutive. A minute of daylight between two
copies, across thousands of rows, means the file was read again.

The 501 are not decided here and must not be: the source listed the booking
twice, and that may be a duplicated export line or a tenant who really paid
the same amount twice on one day. Deleting one of those loses a real payment,
which is the one thing this family of modules promises not to do.
"""

import os
import unittest
from datetime import datetime, timedelta

from kefiya.utils.rerun_rule import (FROM_A_RERUN, SAME_PASS, SECONDS_APART,
                                     UNDECIDED, identity, seconds_between,
                                     surplus_of)


BASE = datetime(2026, 5, 21, 13, 20, 39)


def row(name, offset_seconds, **overrides):
    data = {
        "name": name,
        "creation": BASE + timedelta(seconds=offset_seconds),
        "bank_account": "SKL Sparkasse - Sparkasse Heidelberg",
        "date": "2025-06-30",
        "deposit": 780.0,
        "withdrawal": 0.0,
        "description": "GUTSCHR. UEBERW. DAUERAUFTR | Miete Wohnung Nr. 007",
        "bank_party_name": "Chiara Dangelo",
        "bank_party_iban": "DE02120300000000202051",
    }
    data.update(overrides)
    return data


class TestWhatMakesTwoRowsTheSameBooking(unittest.TestCase):

    def test_the_account_is_part_of_it(self):
        """Unlike duplicate_rule, which compares ACROSS accounts."""
        self.assertNotEqual(identity(row("a", 0)),
                            identity(row("b", 0, bank_account="Anderes")))

    def test_the_whole_purpose_line_counts(self):
        """These rows sit side by side; two neighbours differing in their
        eighty-first character are two bookings."""
        long_one = "x" * 80 + "A"
        long_two = "x" * 80 + "B"
        self.assertNotEqual(identity(row("a", 0, description=long_one)),
                            identity(row("b", 0, description=long_two)))

    def test_identical_rows_are_one_booking(self):
        self.assertEqual(identity(row("a", 0)), identity(row("b", 40)))


class TestTheGap(unittest.TestCase):

    def test_a_second_pass_is_recognised(self):
        """The measured shape: 41 seconds apart, second pass."""
        copies = [row("erste", 0), row("zweite", 41)]
        surplus, why = surplus_of(copies)
        self.assertEqual(why, FROM_A_RERUN)
        self.assertEqual([r["name"] for r in surplus], ["zweite"])

    def test_the_earliest_always_stays(self):
        """Never the last copy -- the first pass's row is the keeper."""
        copies = [row("spaet", 90), row("frueh", 0), row("mittel", 45)]
        surplus, why = surplus_of(copies)
        self.assertEqual(why, FROM_A_RERUN)
        self.assertNotIn("frueh", [r["name"] for r in surplus])
        self.assertEqual(len(surplus), 2)

    def test_one_pass_decides_nothing(self):
        """The source listed it twice. This does not argue with the source."""
        surplus, why = surplus_of([row("a", 0), row("b", 1)])
        self.assertEqual(why, SAME_PASS)
        self.assertEqual(surplus, [])

    def test_the_boundary_belongs_to_the_cautious_side(self):
        self.assertEqual(surplus_of([row("a", 0),
                                     row("b", SECONDS_APART - 1)])[1],
                         SAME_PASS)
        self.assertEqual(surplus_of([row("a", 0),
                                     row("b", SECONDS_APART)])[1],
                         FROM_A_RERUN)

    def test_a_mixed_group_is_left_whole(self):
        """A same-pass twin AND a later copy. Picking one out would leave a
        pair behind and would rest on an ordering this cannot see."""
        surplus, why = surplus_of([row("a", 0), row("b", 1), row("c", 60)])
        self.assertEqual(why, UNDECIDED)
        self.assertEqual(surplus, [])

    def test_a_single_row_is_not_a_duplicate(self):
        self.assertEqual(surplus_of([row("a", 0)]), ([], UNDECIDED))

    def test_it_never_raises_on_unreadable_stamps(self):
        broken = [row("a", 0), dict(row("b", 0), creation="nicht lesbar")]
        self.assertEqual(surplus_of(broken)[0], [])

    def test_seconds_between_survives_nonsense(self):
        self.assertIsNone(seconds_between("a", "b"))


def _source(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return handle.read()


class TestOnlyOnePlaceDeletes(unittest.TestCase):
    """Die Zusicherung, die die Kopie verhindert.

    Vorher trugen delete_duplicates und delete_rerun_duplicates je dreissig
    Zeilen, die sich in dreien unterschieden: welcher Planer gerufen wird,
    der Wortlaut einer Logzeile, ein Rueckgabeschluessel. Alles, was das
    Loeschen sicher macht -- Rechtepruefung, Bestaetigung ueber die Zahl,
    Blockschleife, weiches Loeschen -- stand zweimal da, in einem Pfad,
    dessen Aufgabe das Vernichten von Dokumenten ist.

    Gezaehlt statt beschrieben: Eine zweite Kopie faellt hier auf, eine
    Beschreibung der Regel nicht.
    """

    def _quelle(self):
        return _source("utils", "statement_repair.py")

    def test_es_gibt_genau_einen_loeschaufruf(self):
        self.assertEqual(self._quelle().count("frappe.delete_doc("), 1)

    def test_es_gibt_genau_eine_bestaetigung_ueber_die_zahl(self):
        self.assertEqual(
            self._quelle().count("cint(confirm) != len(doomed)"), 1)

    def test_es_gibt_genau_einen_zeilenleser(self):
        """Die beiden Kopien waren schon auseinandergelaufen -- eine holte
        bank_party_iban, die andere nicht."""
        quelle = self._quelle()
        self.assertEqual(quelle.count('frappe.get_all(\n        "Bank Transaction"'), 1)
        self.assertEqual(quelle.count("def _grouped("), 1)

    def test_beide_einstiege_gehen_durch_denselben_kern(self):
        quelle = self._quelle()
        self.assertEqual(quelle.count("_carry_out("), 3)  # Definition + zwei Aufrufe

    def test_beide_einstiege_fragen_um_erlaubnis(self):
        """_carry_out selbst nicht -- die Schranke gehoert an den Eingang."""
        quelle = self._quelle()
        for name in ("def delete_duplicates(", "def delete_rerun_duplicates("):
            rumpf = quelle.split(name)[1].split("\n@")[0]
            self.assertIn("_may_repair()", rumpf, name)


class TestDerKernHaeltJedeZusage(unittest.TestCase):
    """Als Aufruf mit Argumenten geprueft, nicht als Prosa -- der Docstring
    listet die Zusagen woertlich auf, ein Grep nach den Worten findet also
    die Beschreibung und beweist nichts."""

    def _rumpf(self):
        """Ohne Docstring UND ohne Kommentare.

        Der Docstring listet die Zusagen woertlich auf und ein Kommentar
        erklaert, warum hier nicht delete_permanently steht -- eine Pruefung
        auf das Wort findet also beide und beweist nichts ueber den Code.
        Diese Suite ist genau so schon mehrfach hereingefallen.
        """
        quelle = _source("utils", "statement_repair.py")
        methode = quelle.split("def _carry_out(")[1]
        rumpf = methode.split('"""', 2)[2].split("\n@frappe.whitelist()")[0]
        return "\n".join(z for z in rumpf.splitlines()
                          if not z.strip().startswith("#"))

    def test_es_bestaetigt_ueber_die_zahl(self):
        rumpf = self._rumpf()
        self.assertIn("if cint(confirm) != len(doomed):", rumpf)
        self.assertIn("frappe.throw", rumpf)

    def test_es_loescht_weich(self):
        rumpf = self._rumpf()
        self.assertIn('frappe.delete_doc("Bank Transaction", name,', rumpf)
        self.assertNotIn("delete_permanently", rumpf)

    def test_es_committet_in_bloecken(self):
        self.assertIn("if index % BATCH == 0:", self._rumpf())

    def test_fehler_werden_gemeldet_nicht_verschluckt(self):
        rumpf = self._rumpf()
        self.assertIn('failed.append({"name": name,', rumpf)

    def test_der_planer_liest_nur_unberuehrte_entwuerfe(self):
        quelle = _source("utils", "statement_repair.py")
        leser = quelle.split("def _grouped(")[1].split("\n\ndef ")[0]
        self.assertIn('"docstatus": 0', leser)
        self.assertIn("if not _untouched(row):", leser)

    def test_die_unentschiedenen_paare_werden_benannt(self):
        quelle = _source("utils", "statement_repair.py")
        plan = quelle.split("def plan_reruns(")[1].split("\n\ndef ")[0]
        self.assertIn("review.append({", plan)
        self.assertIn('"zeilen": [row["name"] for row in copies]', plan)


if __name__ == "__main__":
    unittest.main()
