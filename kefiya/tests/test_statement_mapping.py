# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import os
from datetime import date

from frappe.tests.utils import FrappeTestCase

from kefiya.utils.statement_import import mapping, reader
from kefiya.utils.statement_import.mapping import MappingError
from kefiya.utils.statement_import.presets import PRESETS, PAYPAL
from kefiya.utils.statement_import.transform import parse_amount, transform

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

ENGLISH_CSV = (
    "Account statement\n"
    "Opening balance,\"1,000.00\"\n"
    "\n"
    "Booking Date,Payer,Payee,Purpose,Credit,Debit,Currency,Ref\n"
    "2026-11-02,ACME Corp,Us Ltd,Invoice 4711,\"1,234.56\",,EUR,R1\n"
    "2026-11-03,Us Ltd,Office Supplies,\"Paper,\nToner\",,-12.50,EUR,R2\n"
    "2026-11-04,Us Ltd,Bank,Fee,,0.35,EUR,R3\n"
    "Total,,,,,,,\n"
).encode("utf-8")

ENGLISH_MAPPING = {
    "number_format": "en",
    "date_format": "%Y-%m-%d",
    "columns": {
        "date": "Booking Date",
        "deposit": "Credit",
        "withdrawal": "Debit",
        "currency": "Currency",
        "party_name_deposit": "Payer",
        "party_name_withdrawal": "Payee",
        "reference_number": "Ref",
    },
    "description": {"columns": ["Purpose"]},
}


def read_fixture(name) -> bytes:
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def run(rawdata, raw_mapping):
    clean = mapping.validate(raw_mapping)
    table = reader.read(rawdata, clean.get("delimiter"), clean.get("header_row"))
    return transform(table, mapping.validate(raw_mapping, table["header"]))


class TestReader(FrappeTestCase):
    def test_semicolon_file_with_preamble(self):
        table = reader.read(read_fixture("dkb_giro_umsatzliste.csv"))
        self.assertEqual(table["delimiter"], ";")
        self.assertEqual(table["header"][0], "Buchungsdatum")
        self.assertEqual(table["rows"][0][0], 6)  # file line of the first booking

    def test_comma_file(self):
        table = reader.read(ENGLISH_CSV)
        self.assertEqual(table["delimiter"], ",")
        self.assertEqual(table["header_row"], 3)
        self.assertEqual(table["header"][:2], ["Booking Date", "Payer"])

    def test_line_break_in_a_cell_keeps_the_last_line(self):
        lines = [line for line, _row in reader.read(ENGLISH_CSV)["rows"]]
        self.assertEqual(lines, [5, 7, 8, 9])

    def test_windows_1252_file(self):
        rawdata = "Datum;Empfänger;Betrag\n01.11.2026;Müller GmbH;-12,50\n".encode("cp1252")
        table = reader.read(rawdata)
        self.assertEqual(table["header"], ["Datum", "Empfänger", "Betrag"])
        self.assertEqual(table["rows"][0][1][1], "Müller GmbH")

    def test_tab_separated(self):
        table = reader.read(b"Date\tAmount\n2026-11-01\t5.00\n2026-11-02\t6.00\n")
        self.assertEqual((table["delimiter"], table["header"]), ("\t", ["Date", "Amount"]))


class TestAmounts(FrappeTestCase):
    def test_german(self):
        for text, amount in [("-1.234,56", -1234.56), ("12,50 €", 12.5), ("EUR -3,20", -3.2),
                             ("12,50-", -12.5), ("1.500", 1500.0), ("+7", 7.0)]:
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text, "de"), amount)

    def test_english(self):
        for text, amount in [("-1,234.56", -1234.56), ("$12.50", 12.5), ("1,500", 1500.0), ("0.35", 0.35)]:
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text, "en"), amount)

    def test_wrong_notation_is_refused(self):
        for text, number_format in [("12.50", "de"), ("12,50", "en"), ("", "de"), ("12,", "de"), ("abc", "en")]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_amount(text, number_format)


class TestTransform(FrappeTestCase):
    def test_deposit_and_withdrawal_columns(self):
        result = run(ENGLISH_CSV, ENGLISH_MAPPING)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["skipped"], 1)  # the total line has no date
        rows = [transaction for _line, transaction in result["transactions"]]
        self.assertEqual([row["amount"] for row in rows], [1234.56, -12.5, -0.35])
        self.assertEqual(rows[0]["date"], date(2026, 11, 2))
        self.assertEqual(rows[0]["party_name"], "ACME Corp")  # payer of a deposit
        self.assertEqual(rows[1]["party_name"], "Office Supplies")  # payee of a withdrawal
        self.assertEqual(rows[1]["description"], "Paper,\nToner")
        self.assertEqual((rows[2]["currency"], rows[2]["reference_number"]), ("EUR", "R3"))

    def test_generic_ids_are_stable_and_unique(self):
        first = [t["transaction_id"] for _l, t in run(ENGLISH_CSV, ENGLISH_MAPPING)["transactions"]]
        second = [t["transaction_id"] for _l, t in run(ENGLISH_CSV, ENGLISH_MAPPING)["transactions"]]
        self.assertEqual(first, second)
        self.assertEqual(len(set(first)), 3)

    def test_identical_rows_get_different_ids(self):
        rawdata = b"Date;Amount\n01.11.2026;5,00\n01.11.2026;5,00\n"
        result = run(rawdata, {"columns": {"date": "Date", "amount": "Amount"}})
        ids = [t["transaction_id"] for _l, t in result["transactions"]]
        self.assertEqual(len(set(ids)), 2)

    def test_bad_date_is_reported_with_its_line(self):
        rawdata = b"Date;Amount\n01.11.2026;5,00\n2026-11-02;6,00\n"
        result = run(rawdata, {"columns": {"date": "Date", "amount": "Amount"}})
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(result["errors"][0][0], 3)

    def test_transaction_id_twice_in_the_file_is_an_error(self):
        rawdata = b"Date;Amount;Id\n01.11.2026;5,00;A1\n02.11.2026;6,00;A1\n"
        result = run(rawdata, {"columns": {"date": "Date", "amount": "Amount", "transaction_id": "Id"}})
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(len(result["errors"]), 1)

    def test_long_transaction_id_is_shortened(self):
        long_id = "X" * 200
        rawdata = f"Date;Amount;Id\n01.11.2026;5,00;{long_id}\n".encode()
        result = run(rawdata, {"columns": {"date": "Date", "amount": "Amount", "transaction_id": "Id"}})
        self.assertEqual(len(result["transactions"][0][1]["transaction_id"]), 32)

    def test_filters_skip_rows(self):
        rawdata = b"Date;Amount;State\n01.11.2026;5,00;done\n02.11.2026;6,00;pending\n"
        result = run(rawdata, {
            "columns": {"date": "Date", "amount": "Amount"},
            "filters": [{"column": "State", "op": "equals", "value": "pending"}],
        })
        self.assertEqual((len(result["transactions"]), result["skipped"]), (1, 1))

    def test_fee_becomes_its_own_transaction(self):
        rawdata = b"Date;Amount;Fee;Id\n01.11.2026;100,00;-2,50;T1\n02.11.2026;50,00;0,00;T2\n"
        result = run(rawdata, {
            "columns": {"date": "Date", "amount": "Amount", "transaction_id": "Id"},
            "fee": {"column": "Fee", "party_name": "Shop", "description": "Fee for {id}"},
        })
        rows = [t for _l, t in result["transactions"]]
        self.assertEqual([(r["transaction_id"], r["amount"]) for r in rows],
                         [("T1", 100.0), ("T1-fee", -2.5), ("T2", 50.0)])
        self.assertEqual((rows[1]["party_name"], rows[1]["description"]), ("Shop", "Fee for T1"))

    def test_paypal_preset_works_on_an_export_without_optional_columns(self):
        rawdata = (
            '"Datum","Name","Typ","Währung","Brutto","Gebühr","Transaktionscode","Auswirkung auf Guthaben"\n'
            '"01.08.2026","Max","Zahlung","EUR","10,00","-0,35","1AB","Haben"\n'
        ).encode()
        result = run(rawdata, PRESETS[PAYPAL])
        self.assertEqual([t["amount"] for _l, t in result["transactions"]], [10.0, -0.35])


class TestMappingValidation(FrappeTestCase):
    def test_valid_mapping_is_cleaned(self):
        clean = mapping.validate(ENGLISH_MAPPING)
        self.assertEqual(clean["description"]["formatter"], "joined")
        self.assertEqual(clean["filters"], [])

    def test_json_text_is_accepted(self):
        self.assertEqual(mapping.validate('{"columns": {"date": "D", "amount": "A"}}')["columns"]["amount"], "A")

    def test_invalid_mappings_are_refused(self):
        base = {"columns": {"date": "D", "amount": "A"}}
        for broken in [
            "not json",
            [],
            {"columns": {"amount": "A"}},  # no date
            {"columns": {"date": "D"}},  # no amount
            {"columns": {"date": "D", "amount": "A", "deposit": "C"}},
            {**base, "columns": {**base["columns"], "unknown": "X"}},
            {**base, "delimiter": "x"},
            {**base, "date_format": "%s"},
            {**base, "number_format": "fr"},
            {**base, "description": {"columns": ["D"], "formatter": "eval"}},
            {**base, "filters": [{"column": "S", "op": "regex", "value": ".*"}]},
            {**base, "header_row": 999},
            {**base, "columns": {"date": "D" * 200, "amount": "A"}},
            {**base, "fee": {"column": "F", "description": "x" * 200}},
        ]:
            with self.subTest(mapping=broken), self.assertRaises(MappingError):
                mapping.validate(broken)

    def test_columns_must_be_in_the_file(self):
        with self.assertRaises(MappingError):
            mapping.validate({"columns": {"date": "D", "amount": "A"}}, header=["D", "B"])

    def test_optional_columns_may_be_missing(self):
        header = ["Datum", "Name", "Typ", "Währung", "Brutto", "Gebühr", "Transaktionscode",
                  "Auswirkung auf Guthaben"]
        mapping.validate(PRESETS[PAYPAL], header=header)
