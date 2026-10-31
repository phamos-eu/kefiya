# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What kefiya asks a bank about the orders it holds, and how it reads them.

On 31.10.2026 both banks asked answered HKCDB with 9110 / 9160 "Pflichtfeld
nicht gefunden": the segment went out with "Unterstuetzte SEPA pain
messages" empty. HKCSB was built the same way. And the refusal read as an
empty bestand, so the forecast stayed empty without a single Error Log.
"""

import datetime
import unittest

from fints.models import SEPAAccount
from fints.parser import FinTS3Serializer

from kefiya.utils import bank_held_orders as orders
from kefiya.utils.fints_segments import HKCDB1, HKCSB1

PAIN_001_03 = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"
PAIN_001_09 = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"
PAIN_008 = "urn:iso:std:iso:20022:tech:xsd:pain.008.001.02"

#: The published example IBAN (test_no_tenant_data lists it).
ACCOUNT = SEPAAccount(iban="DE89370400440532013000", bic="XXXXDEXXXXX",
                      accountnumber="0532013000", subaccount=None,
                      blz="37040044")

TODAY = datetime.date(2026, 11, 3)


class _Answer:
    def __init__(self, code, text="", parameters=None):
        self.code, self.text = code, text
        self.parameters = parameters or []


class _Response:
    def __init__(self, *answers):
        self.answers = answers

    def responses(self, command_seg, code=None):
        for answer in self.answers:
            if code is None or answer.code == code:
                yield answer


class TestTheMandatoryFieldIsFilled(unittest.TestCase):

    def _wire(self, segment_class, listed):
        pain = segment_class._fields["supported_sepa_pain_messages"].type(
            sepa_descriptors=orders.pain_messages(listed))
        seg = segment_class(
            account=segment_class._fields["account"].type.from_sepa_account(
                ACCOUNT),
            supported_sepa_pain_messages=pain)
        return FinTS3Serializer().serialize_message(seg)

    def test_both_queries_carry_the_banks_format(self):
        for segment_class in (HKCDB1, HKCSB1):
            wire = self._wire(segment_class, [PAIN_001_03, PAIN_008])
            self.assertIn(b"pain.001.001.03", wire)
            self.assertNotIn(b"pain.008", wire)

    def test_what_went_out_before_was_empty(self):
        """The refused form, kept so the difference stays visible."""
        seg = HKCDB1(account=HKCDB1._fields["account"].type
                     .from_sepa_account(ACCOUNT))
        self.assertTrue(
            FinTS3Serializer().serialize_message(seg).endswith(b"+'"))

    def test_only_the_family_asked_for(self):
        self.assertEqual(
            orders.pain_messages([PAIN_008, PAIN_001_09, PAIN_001_03]),
            [PAIN_001_09, PAIN_001_03])
        self.assertEqual(orders.pain_messages([PAIN_001_03, PAIN_008],
                                              family="pain.008"),
                         [PAIN_008])

    def test_never_empty(self):
        """An empty field is what the banks refused."""
        self.assertEqual(orders.pain_messages([]), [orders.DEFAULT_PAIN_001])
        self.assertEqual(orders.pain_messages(None),
                         [orders.DEFAULT_PAIN_001])
        self.assertEqual(orders.pain_messages([PAIN_008]),
                         [orders.DEFAULT_PAIN_001])


class TestARefusalIsNotAnEmptyBestand(unittest.TestCase):

    def test_the_9xxx_codes_are_read(self):
        said = orders.refusals(_Response(
            _Answer("9110",
                    "Ungueltige Auftragsnachricht: Unbekannter Aufbau."),
            _Answer("9160", "Pflichtfeld nicht gefunden"),
            _Answer("0020", "Auftrag ausgefuehrt")), object())
        self.assertEqual(said, [
            "9110 Ungueltige Auftragsnachricht: Unbekannter Aufbau.",
            "9160 Pflichtfeld nicht gefunden"])

    def test_success_and_warnings_refuse_nothing(self):
        self.assertEqual(orders.refusals(_Response(
            _Answer("0020"), _Answer("3040", parameters=["X"])), None), [])

    def test_an_unreadable_response_refuses_nothing(self):
        self.assertEqual(orders.refusals(object(), None), [])

    def test_the_exception_names_segment_and_codes(self):
        exc = orders.BankRefusedQuery("HKCDB", ["9160 Pflichtfeld"])
        self.assertIn("HKCDB", str(exc))
        self.assertIn("9160", str(exc))

    def test_the_touchdown_point(self):
        self.assertEqual(orders.touchdown_of(_Response(
            _Answer("3040", parameters=["page-2"])), None), "page-2")
        self.assertIsNone(orders.touchdown_of(_Response(_Answer("0020")),
                                              None))


class TestTheScheduleIsReadByShape(unittest.TestCase):

    def test_dates_first(self):
        """The order of the FinTS details group: first run, cycle, rhythm,
        day, last run."""
        got = orders.schedule_from(["20260115", "M", "1", "15", "20271215"])
        self.assertEqual(
            (got["first_execution"], got["time_unit"], got["rhythm"],
             got["execution_day"], got["last_execution"]),
            ("2026-01-15", "M", 1, 15, "2027-12-15"))

    def test_cycle_first(self):
        """The order this module assumed before -- read the same."""
        got = orders.schedule_from(["M", "3", "1", "20260201"])
        self.assertEqual(
            (got["time_unit"], got["rhythm"], got["execution_day"],
             got["first_execution"]), ("M", 3, 1, "2026-02-01"))
        self.assertNotIn("last_execution", got)

    def test_marked_unconfirmed_and_kept_raw(self):
        got = orders.schedule_from(["20260115", "M", "1", "15"])
        self.assertFalse(got["confirmed"])
        self.assertEqual(got["raw"], ["20260115", "M", "1", "15"])

    def test_nothing_readable(self):
        self.assertEqual(orders.schedule_from(None),
                         {"raw": None, "confirmed": False})
        self.assertFalse(orders.is_readable(orders.schedule_from(["X"])))


class TestTheExecutionsOfTheComingMonth(unittest.TestCase):

    def _dates(self, raw):
        return [d.isoformat() for d in
                orders.occurrences(orders.schedule_from(raw), TODAY)]

    def test_monthly(self):
        self.assertEqual(self._dates(["20260115", "M", "1", "15"]),
                         ["2026-11-15"])

    def test_monthly_on_the_horizon(self):
        self.assertEqual(self._dates(["20260103", "M", "1", "3"]),
                         ["2026-11-03", "2026-12-03"])

    def test_quarterly_off_month_has_none(self):
        self.assertEqual(self._dates(["20260201", "M", "3", "1"]), [])

    def test_quarterly_in_month(self):
        self.assertEqual(self._dates(["20260201", "M", "3", "28"]),
                         ["2026-11-28"])

    def test_ultimo(self):
        self.assertEqual(self._dates(["20260131", "M", "1", "99"]),
                         ["2026-11-30"])
        self.assertEqual(self._dates(["20260131", "M", "1", "98"]),
                         ["2026-11-29"])

    def test_a_day_the_month_lacks_becomes_its_last(self):
        self.assertEqual(self._dates(["20260131", "M", "1", "31"]),
                         ["2026-11-30"])

    def test_weekly_on_fridays(self):
        self.assertEqual(self._dates(["20261002", "W", "1", "5"]),
                         ["2026-11-06", "2026-11-13", "2026-11-20",
                          "2026-11-27"])

    def test_every_second_week(self):
        self.assertEqual(self._dates(["20261009", "W", "2", "5"]),
                         ["2026-11-06", "2026-11-20"])

    def test_not_before_the_first_run(self):
        self.assertEqual(self._dates(["20261120", "M", "1", "15"]), [])

    def test_not_after_the_last_run(self):
        self.assertEqual(
            self._dates(["20260115", "M", "1", "15", "20261031"]), [])

    def test_impossible_days_give_nothing(self):
        self.assertEqual(self._dates(["20260115", "M", "1", "45"]), [])
        self.assertEqual(self._dates(["20260115", "W", "1", "9"]), [])


class TestForecastItems(unittest.TestCase):

    ROW = {
        "amount": 1250.0,
        "recipient_name": "Erika Mustermann",
        "recipient_iban": "DE89370400440532013000",
        "purpose": "Miete",
        "task_id": "ORDER-1",
        "schedule": orders.schedule_from(["20260101", "M", "1", "1"]),
    }

    def test_one_item_per_execution(self):
        got = orders.items_from([self.ROW], TODAY)
        self.assertEqual(got["skipped"], 0)
        self.assertEqual([i["planned_date"] for i in got["items"]],
                         ["2026-12-01"])
        item = got["items"][0]
        self.assertEqual(
            (item["amount"], item["direction"], item["payment_kind"],
             item["task_id"]),
            (1250.0, "Outgoing", "Bank Standing Order", "ORDER-1"))

    def test_the_kind_is_one_the_forecast_knows(self):
        import json
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "doctype", "kefiya_planned_payment",
            "kefiya_planned_payment.json")
        with open(path, encoding="utf-8") as handle:
            fields = json.load(handle)["fields"]
        kinds = [f for f in fields if f["fieldname"] == "source_kind"][0]
        self.assertIn(orders.KIND, kinds["options"].split("\n"))

    def test_unreadable_schedule_is_skipped(self):
        row = dict(self.ROW, schedule={"raw": ["?"]})
        self.assertEqual(orders.items_from([row], TODAY),
                         {"items": [], "skipped": 1})

    def test_no_amount_is_skipped(self):
        row = dict(self.ROW, amount=0)
        self.assertEqual(orders.items_from([row], TODAY)["skipped"], 1)

    def test_outside_the_month_is_not_skipped(self):
        row = dict(self.ROW, schedule=orders.schedule_from(
            ["20260201", "M", "3", "1"]))
        self.assertEqual(orders.items_from([row], TODAY),
                         {"items": [], "skipped": 0})


if __name__ == "__main__":
    unittest.main()
