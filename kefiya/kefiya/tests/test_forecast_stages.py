# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One payment, one line in the forecast.

A payment is planned, ordered, invoiced, handed to the bank, and booked.
Every stage may leave a line in the forecast; without a rule the liquidity
preview counts the same payment once per stage it reached.
"""

import unittest

from kefiya.utils import forecast_stages as fs

IBAN_A = "DE89370400440532013000"   # published example
IBAN_B = "DE02120300000000202051"   # published example


def line(name, kind, due, amount=100.0, from_bank=0, status="Open",
         account="ACC-1", direction="Outgoing", iban=IBAN_A, **extra):
    row = {"name": name, "source_kind": kind, "from_bank": from_bank,
           "status": status, "bank_account": account, "direction": direction,
           "expected_amount": amount, "due_date": due,
           "counterparty_iban": iban}
    row.update(extra)
    return row


class TestStages(unittest.TestCase):

    def test_the_ladder(self):
        self.assertLess(fs.stage_of("Manual"), fs.stage_of("Purchase Order"))
        self.assertLess(fs.stage_of("Purchase Order"),
                        fs.stage_of("Purchase Invoice"))
        self.assertLess(fs.stage_of("Purchase Invoice"),
                        fs.stage_of("Scheduled Transfer"))
        self.assertLess(fs.stage_of("Scheduled Transfer", 0),
                        fs.stage_of("Scheduled Transfer", 1))
        self.assertLess(fs.stage_of("Bank Standing Order", 1),
                        fs.stage_of("Pending", 1))

    def test_unknown_kinds_are_plans(self):
        """Kinds another app adds -- rent, loan instalments -- are plans
        until they say otherwise. kefiya does not need to know them."""
        for kind in ("Rent", "Loan Installment", "Insurance Premium", None):
            self.assertEqual(fs.stage_of(kind), fs.PLAN)


class TestTheSamePayment(unittest.TestCase):

    def test_standing_order_ours_and_the_banks(self):
        """The instance's standing order and the bank's report of it."""
        got = fs.decide([
            line("KSO-1", "Bank Standing Order", "2026-11-03"),
            line("bank-1", "Bank Standing Order", "2026-11-03", from_bank=1),
        ])
        self.assertEqual(got["supersede"], [("KSO-1", "bank-1")])
        self.assertEqual(got["ambiguous"], [])

    def test_a_few_days_apart(self):
        """A weekend moves the execution date."""
        got = fs.decide([
            line("ST-1", "Scheduled Transfer", "2026-11-14"),
            line("bank-1", "Scheduled Transfer", "2026-11-16", from_bank=1),
        ])
        self.assertEqual(got["supersede"], [("ST-1", "bank-1")])

    def test_not_a_different_month(self):
        got = fs.decide([
            line("ST-1", "Scheduled Transfer", "2026-11-03"),
            line("bank-1", "Scheduled Transfer", "2026-12-03", from_bank=1),
        ])
        self.assertEqual(got["supersede"], [])

    def test_not_a_different_amount(self):
        got = fs.decide([
            line("ST-1", "Scheduled Transfer", "2026-11-03", amount=100.00),
            line("bank-1", "Scheduled Transfer", "2026-11-03", amount=100.01,
                 from_bank=1),
        ])
        self.assertEqual(got["supersede"], [])

    def test_not_another_account_direction_or_payee(self):
        for other in ({"account": "ACC-2"}, {"direction": "Incoming"},
                      {"iban": IBAN_B}):
            got = fs.decide([
                line("ST-1", "Scheduled Transfer", "2026-11-03"),
                line("bank-1", "Scheduled Transfer", "2026-11-03",
                     from_bank=1, **other),
            ])
            self.assertEqual(got["supersede"], [], other)

    def test_no_iban_no_match(self):
        """Without a counterparty a line cannot be told apart from the next
        payment of the same amount."""
        got = fs.decide([
            line("RENT-1", "Rent", "2026-11-03", iban=None,
                 direction="Incoming"),
            line("pend-1", "Pending", "2026-11-03", from_bank=1,
                 direction="Incoming"),
        ])
        self.assertEqual(got["supersede"], [])

    def test_the_partys_bank_account_names_it(self):
        """An invoice knows its supplier, the supplier's Bank Account knows
        the IBAN -- standard ERPNext."""
        got = fs.decide([
            line("PI-1", "Purchase Invoice", "2026-11-10", iban=None,
                 party_ibans=[IBAN_A]),
            line("bank-1", "Scheduled Transfer", "2026-11-10", from_bank=1),
        ])
        self.assertEqual(got["supersede"], [("PI-1", "bank-1")])

    def test_the_whole_ladder(self):
        """Plan, invoice, the bank's order, pending: each line is replaced
        by the next stage, and only the highest stays open."""
        got = fs.decide([
            line("plan", "Manual", "2026-11-10"),
            line("inv", "Purchase Invoice", "2026-11-10"),
            line("bank", "Scheduled Transfer", "2026-11-10", from_bank=1),
            line("pend", "Pending", "2026-11-10", from_bank=1),
        ])
        self.assertEqual(sorted(got["supersede"]), [
            ("bank", "pend"), ("inv", "bank"), ("plan", "inv")])

    def test_settled_lines_stay(self):
        got = fs.decide([
            line("ST-1", "Scheduled Transfer", "2026-11-03",
                 status="Matched"),
            line("bank-1", "Scheduled Transfer", "2026-11-03", from_bank=1),
        ])
        self.assertEqual(got["supersede"], [])


class TestWhenInDoubtBothStay(unittest.TestCase):

    def test_two_candidates_below(self):
        got = fs.decide([
            line("a", "Scheduled Transfer", "2026-11-02"),
            line("b", "Scheduled Transfer", "2026-11-04"),
            line("bank", "Scheduled Transfer", "2026-11-03", from_bank=1),
        ])
        self.assertEqual(got["supersede"], [])
        self.assertEqual(got["ambiguous"], [("bank", ["a", "b"])])

    def test_two_claims_on_one_line(self):
        got = fs.decide([
            line("a", "Scheduled Transfer", "2026-11-03"),
            line("bank1", "Scheduled Transfer", "2026-11-02", from_bank=1),
            line("bank2", "Scheduled Transfer", "2026-11-04", from_bank=1),
        ])
        self.assertEqual(got["supersede"], [])
        self.assertEqual(sorted(got["ambiguous"]),
                         [("bank1", ["a"]), ("bank2", ["a"])])


class TestComingBack(unittest.TestCase):

    def test_the_bank_dropped_the_order(self):
        lower = {"name": "KSO-1", "superseded_by": "bank-1"}
        target = {"name": "bank-1", "from_bank": 1, "status": "Cancelled"}
        self.assertEqual(fs.to_reopen([lower], {"bank-1": target}),
                         ["KSO-1"])

    def test_the_line_is_gone(self):
        self.assertEqual(fs.to_reopen(
            [{"name": "KSO-1", "superseded_by": "x"}], {}), ["KSO-1"])

    def test_still_there_or_booked(self):
        for status in ("Open", "Matched", "Partially Matched", "Booked"):
            target = {"name": "b", "from_bank": 1, "status": status}
            self.assertEqual(fs.to_reopen(
                [{"name": "l", "superseded_by": "b"}], {"b": target}), [],
                status)

    def test_the_higher_one_was_itself_superseded(self):
        target = {"name": "b", "from_bank": 1, "status": "Cancelled",
                  "cancellation_reason": fs.SUPERSEDED}
        self.assertEqual(fs.to_reopen(
            [{"name": "l", "superseded_by": "b"}], {"b": target}), [])

    def test_lines_someone_planned_are_left_alone(self):
        """An invoice cancelled as uncollectible does not bring the rent
        back -- whoever cancelled it had a reason."""
        target = {"name": "SI-1", "from_bank": 0, "status": "Cancelled",
                  "cancellation_reason": "Uncollectible"}
        self.assertEqual(fs.to_reopen(
            [{"name": "RENT-1", "superseded_by": "SI-1"}], {"SI-1": target}),
            [])

    def test_expired_is_not_withdrawn(self):
        target = {"name": "b", "from_bank": 1, "status": "Expired"}
        self.assertEqual(fs.to_reopen(
            [{"name": "l", "superseded_by": "b"}], {"b": target}), [])


class TestATransferToOurselves(unittest.TestCase):
    """Out of one of our accounts, into another: both sides in the forecast."""

    OWN = {IBAN_A: "ACC-1", IBAN_B: "ACC-2"}
    NAMES = {"ACC-1": "Current", "ACC-2": "Savings"}

    def source(self, **change):
        row = line("KTR-1", "Scheduled Transfer", "2026-11-30",
                   amount=500.0, iban=IBAN_B.lower(), purpose="Umbuchung")
        row.update(change)
        return row

    def test_out_here_in_there(self):
        got = fs.mirror_actions([self.source()], self.OWN, self.NAMES)
        self.assertEqual(len(got["create"]), 1)
        name, values = got["create"][0]
        self.assertEqual(name, "KTR-1")
        self.assertEqual(
            (values["direction"], values["bank_account"],
             values["counterparty_iban"], values["counterparty_name"],
             values["expected_amount"], values["due_date"],
             values["from_bank"]),
            ("Incoming", "ACC-2", IBAN_A, "Current", 500.0, "2026-11-30", 0))

    def test_whoever_wrote_the_source(self):
        """A plan of the instance, our own order, the bank's report: each
        gets its incoming side, and keeps its own stage."""
        got = fs.mirror_actions([self.source(from_bank=1)], self.OWN)
        self.assertEqual(got["create"][0][1]["from_bank"], 1)

    def test_a_stranger_is_not_mirrored(self):
        got = fs.mirror_actions(
            [self.source(counterparty_iban="DE02120300000000202052")],
            self.OWN)
        self.assertEqual(got["create"], [])

    def test_not_pending_and_not_incoming(self):
        """The receiving bank reports its side of a pending entry itself."""
        for change in ({"source_kind": "Pending"}, {"direction": "Incoming"},
                       {"bank_account": None}, {"status": "Matched"}):
            got = fs.mirror_actions([self.source(**change)], self.OWN)
            self.assertEqual(got["create"], [], change)

    def test_not_to_itself(self):
        got = fs.mirror_actions(
            [self.source(counterparty_iban=IBAN_A)], self.OWN)
        self.assertEqual(got["create"], [])

    def test_the_mirror_follows_its_source(self):
        mirror = line("MIR-KTR-1", "Scheduled Transfer", "2026-11-30",
                      amount=500.0, account="ACC-2", direction="Incoming",
                      mirror_of="KTR-1", purpose="Umbuchung")
        got = fs.mirror_actions(
            [self.source(due_date="2026-12-01", expected_amount=510.0),
             mirror], self.OWN)
        self.assertEqual(got["create"], [])
        self.assertEqual(got["update"], [(
            "MIR-KTR-1", {"due_date": "2026-12-01",
                          "expected_amount": 510.0})])

    def test_cancelled_with_its_source(self):
        mirror = line("MIR-KTR-1", "Scheduled Transfer", "2026-11-30",
                      account="ACC-2", direction="Incoming",
                      mirror_of="KTR-1")
        for status, reason, want in (
                ("Cancelled", "Reversal", [("MIR-KTR-1", "Reversal")]),
                ("Expired", None, [("MIR-KTR-1", "Other")]),
                ("Cancelled", fs.SUPERSEDED, []),
                ("Matched", None, [])):
            got = fs.mirror_actions(
                [self.source(status=status, cancellation_reason=reason),
                 dict(mirror)], self.OWN)
            self.assertEqual(got["cancel"], want, (status, reason))
        got = fs.mirror_actions([dict(mirror)], self.OWN)
        self.assertEqual(got["cancel"], [("MIR-KTR-1", "Other")],
                         "source gone")

    def test_a_higher_stage_replaces_the_mirror_too(self):
        """Our order is replaced by the bank's; each has its mirror on the
        receiving account, and those are one payment as well."""
        got = fs.decide([
            line("MIR-ours", "Scheduled Transfer", "2026-11-30", amount=500,
                 account="ACC-2", direction="Incoming"),
            line("MIR-bank", "Scheduled Transfer", "2026-11-30", amount=500,
                 account="ACC-2", direction="Incoming", from_bank=1),
        ])
        self.assertEqual(got["supersede"], [("MIR-ours", "MIR-bank")])

    def test_the_mirror_meets_the_receiving_banks_pending_entry(self):
        got = fs.decide([
            line("MIR-bank", "Scheduled Transfer", "2026-11-30", amount=500,
                 from_bank=1, account="ACC-2", direction="Incoming"),
            line("pend", "Pending", "2026-11-30", amount=500, from_bank=1,
                 account="ACC-2", direction="Incoming"),
        ])
        self.assertEqual(got["supersede"], [("MIR-bank", "pend")])


if __name__ == "__main__":
    unittest.main()
