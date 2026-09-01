# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The payee check, tested for what it decides.

The bank's Verification of Payee happens at the bank, on submission, and
cannot happen earlier -- in FinTS the check is part of the transfer message,
so asking it means sending the order. That is too late where one person enters
an order and another sends it.

This is the check that can be made at entry, and it answers a different
question: have we paid this IBAN before, and did it belong to this name? It is
the question that catches invoice manipulation -- right letterhead, right
name, swapped IBAN -- which the bank's own name/IBAN match would not flag at
all, because the swapped IBAN really does belong to somebody with a plausible
name.
"""

import unittest

from kefiya.utils import payee_check


class TestANameIsComparedByWhatIdentifiesIt(unittest.TestCase):
    """Two invoices from one company write the name three ways. A check that
    trips over "GmbH" versus "GmbH & Co. KG" is a check nobody reads after the
    first week."""

    def test_the_same_payee_written_three_ways(self):
        for other in ("Sofienstrasse GmbH & Co. KG",
                      "SOFIENSTRASSE GMBH CO KG",
                      "Sofienstrasse GmbH"):
            self.assertNotEqual(
                payee_check.names_match("Sofienstrasse GmbH & Co. KG", other),
                "different", other)

    def test_punctuation_case_and_titles_do_not_matter(self):
        """A doctorate is not part of who gets paid."""
        self.assertEqual(
            payee_check.names_match("Dr. Claudia Fischer", "CLAUDIA FISCHER"),
            "exact")

    def test_two_different_people_are_different(self):
        self.assertEqual(
            payee_check.names_match("Milorad Vrban", "Milorad Djordjic"),
            "different")

    def test_one_word_in_common_is_not_a_match(self):
        """"Müller Bau GmbH" and "Müller Transport GmbH" are two companies."""
        self.assertEqual(
            payee_check.names_match("Mueller Bau", "Mueller Transport"),
            "different")

    def test_umlauts_are_not_folded_away(self):
        """"Müller" and "Mueller" is a real question; answering it silently
        here would answer it in the wrong direction."""
        self.assertEqual(payee_check.names_match("Müller", "Mueller"),
                         "different")

    def test_nothing_matches_nothing(self):
        for pair in (("", "Vrban"), ("Vrban", None), ("GmbH", "AG")):
            self.assertEqual(payee_check.names_match(*pair), "different", pair)


class TestTheVerdictSeparatesTheFourCases(unittest.TestCase):

    IBAN = "DE27672500200009355367"
    OTHER = "DE72672500200009253580"

    def test_a_known_iban_under_a_known_name(self):
        self.assertEqual(
            payee_check.verdict_for(
                "Milorad Vrban", self.IBAN,
                history=[{"name": "Milorad Vrban"}]),
            payee_check.VERDICT_KNOWN)

    def test_a_known_iban_under_another_name(self):
        """The IBAN was paid before -- to somebody else."""
        self.assertEqual(
            payee_check.verdict_for(
                "Milorad Vrban", self.IBAN,
                history=[{"name": "Bauhaus AG"}]),
            payee_check.VERDICT_NAME_DIFFERS)

    def test_a_known_payee_with_a_new_iban_is_the_loud_one(self):
        """Right letterhead, right name, swapped IBAN. This is the case the
        bank's own check does not flag."""
        self.assertEqual(
            payee_check.verdict_for(
                "Milorad Vrban", self.IBAN, history=[],
                other=[{"iban": self.OTHER}]),
            payee_check.VERDICT_OTHER_IBAN)

    def test_a_payee_nobody_has_paid_is_simply_new(self):
        """Not an alarm -- every payee is new once."""
        self.assertEqual(
            payee_check.verdict_for("Neue Firma GmbH", self.IBAN,
                                    history=[], other=[]),
            payee_check.VERDICT_NEW)

    def test_the_same_iban_in_the_other_list_is_not_another_iban(self):
        """It is the one being checked."""
        self.assertEqual(
            payee_check.verdict_for("Milorad Vrban", self.IBAN, history=[],
                                    other=[{"iban": self.IBAN}]),
            payee_check.VERDICT_NEW)

    def test_one_matching_entry_is_enough_among_many(self):
        """An IBAN paid under several spellings is one payee, not a mismatch."""
        self.assertEqual(
            payee_check.verdict_for(
                "Sofienstrasse GmbH & Co. KG", self.IBAN,
                history=[{"name": "Bauhaus AG"},
                         {"name": "SOFIENSTRASSE GMBH CO KG"}]),
            payee_check.VERDICT_KNOWN)

    def test_a_written_iban_matches_a_spaced_one(self):
        self.assertEqual(
            payee_check.verdict_for("X", "DE27 6725 0020 0009 3553 67",
                                    history=[], other=[{"iban": self.IBAN}]),
            payee_check.VERDICT_NEW,
            "The IBAN being checked must not count as another one merely "
            "because it was typed with spaces.")


class TestTheTwoLoudCasesAreNamedAsSuch(unittest.TestCase):

    def test_both_swaps_count_as_loud(self):
        self.assertIn(payee_check.VERDICT_OTHER_IBAN, payee_check.LOUD)
        self.assertIn(payee_check.VERDICT_NAME_DIFFERS, payee_check.LOUD)

    def test_a_new_payee_is_not_an_alarm(self):
        self.assertNotIn(payee_check.VERDICT_NEW, payee_check.LOUD)
        self.assertNotIn(payee_check.VERDICT_KNOWN, payee_check.LOUD)


class TestTheEndpointIsGated(unittest.TestCase):
    """The answer names IBANs and payees this company has paid -- not
    something a reader without the right should enumerate one guess at a
    time."""

    def test_it_asks_for_the_right_to_create_a_transfer(self):
        import inspect
        source = inspect.getsource(payee_check.check_payee)
        self.assertIn('frappe.has_permission("Kefiya Transfer",'
                      ' ptype="create", throw=True)', source)


def _read(*parts):
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


DOC = ("kefiya", "doctype", "kefiya_transfer", "kefiya_transfer.py")
FORM = ("kefiya", "doctype", "kefiya_transfer", "kefiya_transfer.js")
DIALOG = ("public", "js", "controllers", "transfer_form.js")


class TestTheAnswerIsRecordedNotJustShown(unittest.TestCase):
    """The person entering the order has the invoice in front of them; the
    person sending it does not. By the time the second one looks, the check
    has to be a recorded fact and not a question asked again."""

    def test_the_verdict_is_written_onto_the_order(self):
        source = _read(*DOC)
        self.assertIn("def check_the_payees(self):", source)
        self.assertIn("row.payee_check = answer[\"verdict\"]", source)
        self.assertIn("row.payee_check_detail = _payee_detail(answer)", source)

    def test_the_item_carries_the_fields(self):
        import json
        meta = json.loads(_read("kefiya", "doctype", "kefiya_transfer_item",
                                "kefiya_transfer_item.json"))
        fields = {f["fieldname"]: f for f in meta["fields"]}
        for name in ("payee_check", "payee_check_detail"):
            self.assertIn(name, fields)
            self.assertEqual(fields[name].get("read_only"), 1,
                             "A verdict somebody can type over is not one.")

    def test_it_is_checked_on_every_save(self):
        body = _read(*DOC).split("def validate(")[1].split("\n    def ")[0]
        self.assertIn("self.check_the_payees()", body)

    def test_it_never_blocks_the_order(self):
        """A first payment to a new payee is ordinary, and an order that
        cannot be saved until the software is satisfied gets entered
        somewhere else."""
        body = _read(*DOC).split("def check_the_payees(")[1].split(
            "\n    def ")[0]
        self.assertNotIn("frappe.throw", body)

    def test_a_failed_check_leaves_no_verdict_rather_than_a_good_one(self):
        body = _read(*DOC).split("def check_the_payees(")[1].split(
            "\n    def ")[0]
        self.assertIn("continue", body)
        self.assertIn("frappe.log_error(", body)


class TestTheThreeReadersEachSeeIt(unittest.TestCase):
    """Three different people look: while entering, on the order, and in the
    box the managing director confirms."""

    def test_while_entering(self):
        dialog = _read(*DIALOG)
        self.assertIn("kefiya.payee_check(who, iban)", dialog)
        self.assertIn("dialog.fields_dict.recipient_iban.df.onchange"
                      " = checkPayee", dialog)
        self.assertIn("dialog.fields_dict.recipient_name.df.onchange"
                      " = checkPayee", dialog)

    def test_in_the_confirmation_before_sending(self):
        form = _read(*FORM)
        self.assertIn("kefiya.payee_verdict(row.payee_check)", form)
        self.assertIn("kefiya.payee_needs_a_look(row.payee_check)", form)

    def test_a_flagged_recipient_cannot_be_scrolled_past(self):
        """Named again above the list, not only in a row of it."""
        form = _read(*FORM)
        self.assertIn("alert alert-danger", form)
        self.assertIn("need a look", form)

    def test_not_knowing_is_not_shown_as_being_fine(self):
        reader = _read("public", "js", "controllers", "payee_check.js")
        body = reader.split("kefiya.payee_check = function")[1][:700]
        self.assertIn("return null;", body)
        self.assertIn("catch", body)

    def test_the_loud_verdict_carries_its_evidence(self):
        """"Known payee, new IBAN" without the old IBAN is an alarm nobody can
        act on."""
        reader = _read("public", "js", "controllers", "payee_check.js")
        self.assertIn("answer.other_ibans", reader)
        self.assertIn("answer.known_as", reader)
