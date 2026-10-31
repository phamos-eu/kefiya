# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""When a payee check needs a person -- the rule, on its own.

This decides whether money leaves without anybody looking at it, so it is
written frappe-free and exercised here without a site, a bank or a library.

The negative cases are the ones that matter: a rule that says "no human
needed" too often is not a convenience, it is an unchecked payment.
"""

import unittest

from kefiya.utils import vop_rule


class Evpe:
    """The inner group the bank's answer actually lives in."""

    def __init__(self, result=None, close_match_name=None,
                 other_identification=None):
        self.result = result
        self.close_match_name = close_match_name
        self.other_identification = other_identification


class Hivpp:
    """The segment python-fints hands over: the answer is one level down."""

    def __init__(self, inner=None):
        self.vop_id = b"whatever"
        self.vop_single_result = inner


class TestWhoHasToLook(unittest.TestCase):

    def test_a_full_match_needs_nobody(self):
        self.assertFalse(vop_rule.needs_a_human("RCVC"))

    def test_every_other_answer_needs_somebody(self):
        for code in ("RVMC", "RVNM", "RVNA", "PDNG"):
            self.assertTrue(vop_rule.needs_a_human(code), code)

    def test_could_not_check_is_not_it_is_fine(self):
        """RVNA on its own. Spelled out because treating it as a pass would
        turn every outage at the bank into a batch of unchecked payments."""
        self.assertTrue(vop_rule.needs_a_human(vop_rule.NOT_AVAILABLE))

    def test_an_answer_nobody_here_has_seen_needs_somebody(self):
        for code in ("", None, "XXXX", "rcvc"):
            self.assertTrue(vop_rule.needs_a_human(code), repr(code))

    def test_a_remembered_payee_needs_nobody(self):
        self.assertFalse(vop_rule.needs_a_human("RVMC", remembered=True))
        self.assertFalse(vop_rule.needs_a_human("RVNM", remembered=True))

    def test_remembering_is_the_only_thing_that_softens_it(self):
        """No other argument exists, and none should: a second flag here is a
        second way to skip the check."""
        self.assertTrue(vop_rule.needs_a_human("RVNM", remembered=False))


class TestWhatADecisionIsFiledUnder(unittest.TestCase):

    def test_both_halves_are_in_the_key(self):
        key = vop_rule.payee_key("DE02120300000000202051", "ACME GmbH")
        self.assertIn("DE02120300000000202051", key)
        self.assertIn("acme gmbh", key)

    def test_spacing_and_case_do_not_make_a_new_payee(self):
        self.assertEqual(
            vop_rule.payee_key("de02 1203 0000 0000 2020 51", "ACME  GmbH"),
            vop_rule.payee_key("DE02120300000000202051", "acme gmbh"))

    def test_a_different_iban_is_a_different_payee(self):
        """The whole fraud this check exists for is a swapped IBAN under a
        name that is still right."""
        self.assertNotEqual(
            vop_rule.payee_key("DE02120300000000202051", "ACME GmbH"),
            vop_rule.payee_key("DE02120300000000202052", "ACME GmbH"))

    def test_a_different_name_is_a_different_payee(self):
        self.assertNotEqual(
            vop_rule.payee_key("DE02120300000000202051", "ACME GmbH"),
            vop_rule.payee_key("DE02120300000000202051", "ACNE GmbH"))

    def test_the_legal_form_is_part_of_the_name(self):
        """payee_check drops it on purpose when warning somebody at entry.
        Here it must not be dropped: those are two companies, and a decision
        about one of them is not a decision about the other."""
        self.assertNotEqual(
            vop_rule.payee_key("DE02120300000000202051", "ACME GmbH"),
            vop_rule.payee_key("DE02120300000000202051", "ACME KG"))

    def test_an_empty_payee_produces_an_empty_key(self):
        """Which is what accepted_payee refuses to store or look up, so a
        half-read order never matches a remembered decision."""
        self.assertEqual(vop_rule.payee_key("", "").strip("|"), "")
        self.assertEqual(vop_rule.payee_key(None, None).strip("|"), "")


class TestReadingTheBanksAnswer(unittest.TestCase):

    def test_the_result_is_read_out_of_the_inner_group(self):
        """The one that broke: HIVPP carries the VoP-ID, and the result sits
        in its EVPE. Reading the segment alone finds nothing, and then every
        check looks unanswerable and no full match is ever recognised."""
        self.assertEqual(
            vop_rule.result_from(Hivpp(Evpe(result="RCVC"))), "RCVC")

    def test_the_name_the_bank_holds_is_read_the_same_way(self):
        self.assertEqual(
            vop_rule.bank_name_from(
                Hivpp(Evpe(result="RVMC", close_match_name="ACME AG"))),
            "ACME AG")

    def test_the_other_identification_is_used_where_there_is_no_name(self):
        self.assertEqual(
            vop_rule.bank_name_from(
                Hivpp(Evpe(result="RVMC", other_identification="Handle 42"))),
            "Handle 42")

    def test_a_mapping_reads_too(self):
        """The parked answer has been through the database as JSON."""
        self.assertEqual(
            vop_rule.result_from({"vop_single_result": {"result": "RVNM"}}),
            "RVNM")
        self.assertEqual(vop_rule.result_from({"result": "RVNM"}), "RVNM")

    def test_nothing_readable_answers_empty_rather_than_raising(self):
        """And "" needs a human, so an unreadable answer parks the order."""
        for value in (None, "", "kaputt", Hivpp(None), object()):
            self.assertEqual(vop_rule.result_from(value), "", repr(value))
            self.assertEqual(vop_rule.bank_name_from(value), "", repr(value))
            self.assertTrue(vop_rule.needs_a_human(vop_rule.result_from(value)))
