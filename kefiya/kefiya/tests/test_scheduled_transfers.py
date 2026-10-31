# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The dated transfers a bank holds, the ones StarMoney shows.

The forecast of bank-held orders asked only for dated DIRECT DEBITS (HKDBS),
which a payer never has. It stayed empty at every bank, while StarMoney
listed the same accounts' dated transfers. Those come from HKCSB, which
kefiya never asked.

The reader runs here on pain messages built by sepaxml, in both schemas.
"""

import datetime
import os
import unittest

from kefiya.utils import scheduled_transfers

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

try:
    from sepaxml import SepaTransfer
except ImportError:  # pragma: no cover - only on a machine without it
    SepaTransfer = None


def _pain(schema, payments, day=datetime.date(2026, 11, 15)):
    sepa = SepaTransfer({"name": "Muster GmbH", "currency": "EUR",
                         "IBAN": "DE89999999990001111111",
                         "batch": len(payments) > 1},
                        schema=schema, clean=False)
    for index, (name, iban, cents, purpose) in enumerate(payments):
        sepa.add_payment({"name": name, "IBAN": iban, "amount": cents,
                          "description": purpose, "execution_date": day,
                          "endtoend_id": "E2E-{0}".format(index)})
    return sepa.export(validate=True)


class Segment:
    def __init__(self, pain, task_id="T-1"):
        self.sepa_pain_message = pain
        self.task_id = task_id


@unittest.skipIf(SepaTransfer is None, "sepaxml not installed")
class TestTheDateAndTheMoneyComeOutOfThePain(unittest.TestCase):

    def test_pain_001_001_03(self):
        pay = scheduled_transfers.payments_in(_pain(
            "pain.001.001.03",
            [("Erika Beispiel", "DE58999999990002222222", 8000, "Miete")]))
        self.assertEqual(len(pay), 1)
        self.assertEqual(pay[0]["planned_date"], "2026-11-15")
        self.assertEqual(pay[0]["amount"], 80.0)
        self.assertEqual(pay[0]["currency"], "EUR")
        self.assertEqual(pay[0]["counterparty_name"], "Erika Beispiel")
        self.assertEqual(pay[0]["counterparty_iban"],
                         "DE58999999990002222222")
        self.assertEqual(pay[0]["purpose"], "Miete")

    def test_pain_001_001_09_wraps_the_date(self):
        pay = scheduled_transfers.payments_in(_pain(
            "pain.001.001.09",
            [("Erika Beispiel", "DE58999999990002222222", 8000, "Miete")]))
        self.assertEqual(pay[0]["planned_date"], "2026-11-15")

    def test_a_collective_order_gives_every_payment(self):
        pay = scheduled_transfers.payments_in(_pain(
            "pain.001.001.03",
            [("Erika Beispiel", "DE58999999990002222222", 8000, "A"),
             ("Max Beispiel", "DE27999999990003333333", 12050, "B")]))
        self.assertEqual([p["amount"] for p in pay], [80.0, 120.5])

    def test_items_carry_kind_direction_and_task(self):
        out = scheduled_transfers.items_from([Segment(_pain(
            "pain.001.001.03",
            [("Erika Beispiel", "DE58999999990002222222", 8000, "Miete")]))])
        self.assertEqual(out["skipped"], 0)
        item = out["items"][0]
        self.assertEqual(item["payment_kind"], "Scheduled Transfer")
        self.assertEqual(item["direction"], "Outgoing")
        self.assertEqual(item["task_id"], "T-1")


class TestNothingIsGuessed(unittest.TestCase):

    def test_no_date_is_skipped(self):
        pain = ("<Document><PmtInf><CdtTrfTxInf><Amt><InstdAmt Ccy=\"EUR\">"
                "5.00</InstdAmt></Amt></CdtTrfTxInf></PmtInf></Document>")
        out = scheduled_transfers.items_from([Segment(pain)])
        self.assertEqual(out, {"items": [], "skipped": 1})

    def test_nothing_at_all(self):
        self.assertEqual(scheduled_transfers.items_from(None),
                         {"items": [], "skipped": 0})
        self.assertEqual(scheduled_transfers.payments_in(b""), [])


class TestTheFetchAsksForThem(unittest.TestCase):

    def _read(self, *parts):
        with open(os.path.join(WURZEL, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_the_query_and_the_answer_are_declared(self):
        source = self._read("utils", "fints_segments.py")
        self.assertIn("class HKCSB1(FinTS3Segment):", source)
        self.assertIn("class HICSB1(FinTS3Segment):", source)

    def test_the_fetch_writes_them_into_the_forecast(self):
        client = self._read("utils", "client.py")
        self.assertIn("get_fints_scheduled_transfers()", client)
        self.assertIn("payment_kinds=(scheduled_transfers.KIND,))", client)

    def test_the_debit_sweep_no_longer_cancels_them(self):
        client = self._read("utils", "client.py")
        self.assertIn("payment_kinds=DEBIT_FETCH_KINDS)", client)
        planned = self._read("utils", "planned_payment.py")
        self.assertIn('DEBIT_FETCH_KINDS = ("Scheduled Debit",)', planned)


if __name__ == "__main__":
    unittest.main()


class TestOneForecastTable(unittest.TestCase):
    """Kefiya Planned Payment holds the bank's rows and everybody else's,
    under the forecast's field names. kefiya keeps only its own."""

    def setUp(self):
        import json

        with open(os.path.join(WURZEL, "kefiya", "doctype",
                               "kefiya_planned_payment",
                               "kefiya_planned_payment.json"),
                  encoding="utf-8") as handle:
            self.meta = json.load(handle)
        self.fields = {f["fieldname"]: f for f in self.meta["fields"]}
        with open(os.path.join(WURZEL, "utils", "planned_payment.py"),
                  encoding="utf-8") as handle:
            self.source = handle.read()

    def test_the_forecast_field_names(self):
        for name in ("due_date", "expected_amount", "source_kind", "status",
                     "direction", "party_type", "party", "reference_doctype",
                     "reference_name", "matched_bank_transaction",
                     "matched_amount", "net_amount", "tax_amount",
                     "tax_rate", "probability", "is_manual", "generated_by",
                     "last_generated_on", "cancellation_reason",
                     "superseded_by", "reference_key"):
            self.assertIn(name, self.fields)
        self.assertEqual(self.meta["autoname"], "field:reference_key")

    def test_the_mark_says_where_a_row_came_from(self):
        self.assertEqual(self.fields["from_bank"]["fieldtype"], "Check")
        self.assertIn('"from_bank": 1,', self.source)

    def test_nothing_instance_specific_is_linked(self):
        """Lease and Property belong to another app; they stay custom
        fields where that app is installed."""
        self.assertNotIn("lease", self.fields)
        self.assertNotIn("property", self.fields)

    def test_kefiya_only_touches_the_banks_rows(self):
        match = self.source.split("def match_on_bank_transaction(")[1].split(
            "\ndef ")[0]
        expire = self.source.split("def expire_stale_planned_payments(")[1]
        for body in (match, expire):
            self.assertIn('"from_bank": 1', body)
        self.assertNotIn("delete_doc", match)
        self.assertIn('"status": "Matched"', match)
