# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The stage ladder against a stand-in for frappe: what it reads, what it
writes, and that it writes nothing else.

forecast_stages decides; planned_payment reads the forecast and writes the
decision. A source test would only say the lines are there -- this one runs
them.
"""

import datetime
import importlib
import inspect
import sys
import types
import unittest

IBAN_A = "DE89370400440532013000"   # published example
IBAN_B = "DE02120300000000202051"   # published example


class _FakeDB:
    def __init__(self, tables):
        self.tables = tables
        self.writes = []

    def set_value(self, doctype, name, values, *args, **kwargs):
        self.writes.append((doctype, name, dict(values)))
        for row in self.tables.get(doctype, []):
            if row["name"] == name:
                row.update(values)

    def get_value(self, doctype, name, field):
        for row in self.tables.get(doctype, []):
            if row["name"] == name:
                return row.get(field)
        return None


def _matches(row, filters):
    for key, want in (filters or {}).items():
        have = row.get(key)
        if isinstance(want, tuple) and want[0] == "in":
            if have not in want[1]:
                return False
        elif isinstance(want, tuple) and want == ("is", "set"):
            if not have:
                return False
        elif isinstance(want, list) and want and want[0] == "between":
            if not (str(want[1][0]) <= str(have) <= str(want[1][1])):
                return False
        elif have != want:
            return False
    return True


class _Row(dict):
    """frappe._dict: a dict with attribute access, as get_all returns."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


class _Doc(dict):
    def __init__(self, tables, values):
        super().__init__(values)
        self.tables = tables

    def __getattr__(self, key):
        if key in ("tables",):
            raise AttributeError(key)
        return self.get(key)

    def __setattr__(self, key, value):
        if key == "tables":
            dict.__setattr__(self, key, value)
        else:
            self[key] = value

    def save(self, **kwargs):
        for row in self.tables.get(self["doctype"], []):
            if row["name"] == self["name"]:
                row.update({k: v for k, v in self.items()
                            if k != "doctype"})
        return self

    def insert(self, **kwargs):
        row = dict(self)
        row["name"] = row.get("name") or row.get("reference_key")
        table = self.tables.setdefault(row.pop("doctype"), [])
        if any(r["name"] == row["name"] for r in table):
            return self
        table.append(row)
        return self


def _fake_frappe(tables):
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: datetime.datetime(2026, 11, 1, 12, 0)
    utils.getdate = lambda v: (v if isinstance(v, datetime.date)
                               else datetime.date.fromisoformat(str(v)[:10]))
    utils.add_days = lambda d, n: d + datetime.timedelta(days=n)
    utils.add_months = lambda d, n: d
    utils.flt = lambda v, p=None: float(v or 0)
    utils.cint = lambda v: int(v or 0)
    fake = types.ModuleType("frappe")
    fake.utils = utils
    fake.db = _FakeDB(tables)

    def get_all(doctype, filters=None, fields=None, **kwargs):
        out = []
        for row in tables.get(doctype, []):
            if _matches(row, filters):
                out.append(_Row(row))
        return out

    fake.get_all = get_all

    def get_doc(values, name=None):
        if name is None:
            return _Doc(tables, values)
        for row in tables.get(values, []):
            if row["name"] == name:
                return _Doc(tables, dict(row, doctype=values))
        raise KeyError(name)

    fake.get_doc = get_doc
    fake.db.exists = lambda doctype, name: (
        name if any(r["name"] == name for r in tables.get(doctype, []))
        else None)
    fake.log_error = lambda *a, **k: None
    fake.get_traceback = lambda: ""
    fake.logger = lambda name=None: types.SimpleNamespace(
        info=lambda *a, **k: None)
    return fake, utils


def _line(name, kind, due, from_bank=0, status="Open", account="ACC-1",
          direction="Outgoing", iban=IBAN_A, amount=100.0, **extra):
    row = {"name": name, "source_kind": kind, "from_bank": from_bank,
           "status": status, "bank_account": account, "direction": direction,
           "expected_amount": amount, "due_date": due,
           "counterparty_iban": iban, "party_type": None, "party": None,
           "cancellation_reason": None, "superseded_by": None,
           "reference_key": name}
    row.update(extra)
    return row


class _WithFakeFrappe(unittest.TestCase):

    def load(self, tables):
        self.saved = {k: sys.modules.get(k) for k in
                      ("frappe", "frappe.utils",
                       "kefiya.utils.planned_payment")}
        fake, utils = _fake_frappe(tables)
        sys.modules["frappe"] = fake
        sys.modules["frappe.utils"] = utils
        sys.modules.pop("kefiya.utils.planned_payment", None)
        self.fake = fake
        return importlib.import_module("kefiya.utils.planned_payment")

    def tearDown(self):
        for key, module in getattr(self, "saved", {}).items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module


class TestTheLadderWrites(_WithFakeFrappe):

    def test_ours_replaced_by_the_banks(self):
        rows = [_line("KSO-1", "Bank Standing Order", "2026-11-03"),
                _line("bank-1", "Bank Standing Order", "2026-11-03",
                      from_bank=1)]
        pp = self.load({"Kefiya Planned Payment": rows})
        got = pp.apply_stage_ladder()
        self.assertEqual((got["superseded"], got["reopened"]), (1, 0))
        self.assertEqual(self.fake.db.writes, [(
            "Kefiya Planned Payment", "KSO-1",
            {"status": "Cancelled",
             "cancellation_reason": "Superseded by higher stage",
             "superseded_by": "bank-1"})])

    def test_only_status_reason_and_link_are_written(self):
        rows = [_line("PI-1", "Purchase Invoice", "2026-11-10", iban=None,
                      party_type="Supplier", party="SUP-1"),
                _line("bank-1", "Scheduled Transfer", "2026-11-10",
                      from_bank=1)]
        pp = self.load({
            "Kefiya Planned Payment": rows,
            "Bank Account": [{"name": "BA-SUP", "party_type": "Supplier",
                              "party": "SUP-1", "iban": IBAN_A,
                              "disabled": 0}]})
        pp.apply_stage_ladder()
        self.assertEqual(len(self.fake.db.writes), 1)
        for _dt, _name, values in self.fake.db.writes:
            self.assertEqual(set(values), {"status", "cancellation_reason",
                                           "superseded_by"})

    def test_not_back_when_a_person_cancelled_the_higher_one(self):
        rows = [_line("KSO-1", "Bank Standing Order", "2026-11-03",
                      status="Cancelled",
                      cancellation_reason="Superseded by higher stage",
                      superseded_by="bank-1"),
                _line("bank-1", "Bank Standing Order", "2026-11-03",
                      from_bank=1, status="Cancelled",
                      cancellation_reason="Reversal")]
        pp = self.load({"Kefiya Planned Payment": rows})
        self.assertEqual(pp.apply_stage_ladder()["reopened"], 0)

    def test_back_when_the_bank_dropped_it(self):
        rows = [_line("KSO-1", "Bank Standing Order", "2026-11-03",
                      status="Cancelled",
                      cancellation_reason="Superseded by higher stage",
                      superseded_by="bank-1"),
                _line("bank-1", "Bank Standing Order", "2026-11-03",
                      from_bank=1, status="Cancelled")]
        pp = self.load({"Kefiya Planned Payment": rows})
        got = pp.apply_stage_ladder()
        self.assertEqual(got["reopened"], 1)
        self.assertEqual(rows[0]["status"], "Open")
        self.assertIsNone(rows[0]["superseded_by"])

    def _refresh(self, status, reason):
        item = {"planned_date": "2026-11-16", "amount": 100.0,
                "direction": "Outgoing", "payment_kind": "Scheduled Transfer",
                "counterparty_iban": IBAN_A, "counterparty_name": "Payee",
                "purpose": "Invoice"}
        pp = self.load({})
        key = pp.compute_reference_key("ACC-1", "2026-11-16", 100.0,
                                       IBAN_A, "Payee", "Invoice")
        row = _line(key, "Scheduled Transfer", "2026-11-16", from_bank=1,
                    status=status, cancellation_reason=reason,
                    kefiya_login="L1")
        pp = self.load({
            "Kefiya Planned Payment": [row],
            "Kefiya Login": [{"name": "L1", "bank_account": "ACC-1",
                              "company": "C1"}]})
        pp.refresh_planned_payments("L1", [item],
                                    payment_kinds=("Scheduled Transfer",))
        return row

    def test_a_person_s_cancellation_survives_the_next_fetch(self):
        """The bank still reports the order; the person said it is wrong."""
        for reason in ("Duplicate", "Superseded by higher stage"):
            row = self._refresh("Cancelled", reason)
            self.assertEqual(row["status"], "Cancelled", reason)

    def test_the_sweep_s_cancellation_does_not(self):
        """Cancelled without a reason: the sweep did it because the bank
        stopped reporting the order. Reported again, it is open again."""
        row = self._refresh("Cancelled", None)
        self.assertEqual(row["status"], "Open")


class TestATransferToOurselves(_WithFakeFrappe):

    ACCOUNTS = [
        {"name": "ACC-1", "iban": IBAN_A, "account_name": "Current",
         "disabled": 0, "is_company_account": 1, "company": "C1"},
        {"name": "ACC-2", "iban": IBAN_B, "account_name": "Savings",
         "disabled": 0, "is_company_account": 1, "company": "C2"},
    ]

    def transfer(self, **change):
        row = _line("KTR-1", "Scheduled Transfer", "2026-11-30",
                    amount=500.0, iban=IBAN_B.lower(),
                    generated_by="someone", reference_name=None,
                    purpose="Umbuchung")
        row.update(change)
        return row

    def test_the_receiving_account_gets_the_incoming_side(self):
        rows = [self.transfer()]
        pp = self.load({"Kefiya Planned Payment": rows,
                        "Bank Account": self.ACCOUNTS})
        got = pp.apply_stage_ladder()
        self.assertEqual(got["mirrors"]["created"], 1)
        mirror = [r for r in rows if r["name"] == "MIR-KTR-1"][0]
        self.assertEqual(
            (mirror["bank_account"], mirror["company"], mirror["direction"],
             mirror["counterparty_iban"], mirror["counterparty_name"],
             mirror["expected_amount"], mirror["generated_by"],
             mirror["reference_name"], mirror["status"]),
            ("ACC-2", "C2", "Incoming", IBAN_A, "Current", 500.0,
             "kefiya_mirror", "KTR-1", "Open"))
        self.assertNotIn("kefiya_login", mirror,
                         "no login: the sweep of a fetch never cancels it")

    def test_twice_is_once(self):
        rows = [self.transfer()]
        pp = self.load({"Kefiya Planned Payment": rows,
                        "Bank Account": self.ACCOUNTS})
        pp.apply_stage_ladder()
        got = pp.apply_stage_ladder()
        self.assertEqual(got["mirrors"]["created"], 0)
        self.assertEqual(len(rows), 2)

    def test_a_stranger_is_not_mirrored(self):
        rows = [self.transfer(counterparty_iban="DE02120300000000202052")]
        pp = self.load({"Kefiya Planned Payment": rows,
                        "Bank Account": self.ACCOUNTS})
        self.assertEqual(pp.apply_stage_ladder()["mirrors"]["created"], 0)

    def test_the_booking_on_the_receiving_account_matches_it(self):
        mirror = _line("MIR-KTR-1", "Scheduled Transfer", "2026-11-30",
                       amount=500.0, account="ACC-2", direction="Incoming",
                       generated_by="kefiya_mirror")
        pp = self.load({"Kefiya Planned Payment": [mirror]})
        pp.match_on_bank_transaction(types.SimpleNamespace(
            name="BT-1", bank_account="ACC-2", date="2026-11-30",
            withdrawal=0, deposit=500.0, bank_party_iban=IBAN_A,
            bank_party_name="Current"))
        self.assertEqual(mirror["status"], "Matched")

    def test_the_rent_one_company_charges_another(self):
        """Company C2 rents to company C1 and plans the rent; C1 pays it by
        standing order. The mirror of the standing order and the planned
        rent are one payment on C2's account -- standard ERPNext says so:
        the customer represents company C1, whose accounts are ours."""
        rows = [
            _line("KSO-1", "Bank Standing Order", "2026-12-03",
                  amount=2975.0, iban=IBAN_B, generated_by="someone",
                  reference_name=None),
            _line("RENT-1", "Rent", "2026-12-05", amount=2975.0,
                  account="ACC-2", direction="Incoming", iban=None,
                  party_type="Customer", party="C1 as tenant"),
        ]
        pp = self.load({
            "Kefiya Planned Payment": rows,
            "Bank Account": self.ACCOUNTS,
            "Customer": [{"name": "C1 as tenant",
                          "represents_company": "C1"}]})
        got = pp.apply_stage_ladder()
        self.assertEqual(got["mirrors"]["created"], 1)
        rent = [r for r in rows if r["name"] == "RENT-1"][0]
        self.assertEqual((rent["status"], rent["superseded_by"]),
                         ("Cancelled", "MIR-KSO-1"))

    def test_without_it_both_stay(self):
        rows = [
            _line("KSO-1", "Bank Standing Order", "2026-12-03",
                  amount=2975.0, iban=IBAN_B, generated_by="someone",
                  reference_name=None),
            _line("RENT-1", "Rent", "2026-12-05", amount=2975.0,
                  account="ACC-2", direction="Incoming", iban=None,
                  party_type="Customer", party="C1 as tenant"),
        ]
        pp = self.load({
            "Kefiya Planned Payment": rows,
            "Bank Account": self.ACCOUNTS,
            "Customer": [{"name": "C1 as tenant",
                          "represents_company": None}]})
        pp.apply_stage_ladder()
        rent = [r for r in rows if r["name"] == "RENT-1"][0]
        self.assertEqual(rent["status"], "Open")

    def test_the_generator_name_is_one(self):
        from kefiya.utils import forecast_stages

        source = inspect.getsource(self.load({}))
        self.assertEqual(source.count('"kefiya_mirror"'), 1)
        self.assertEqual(forecast_stages.MIRROR_GENERATOR, "kefiya_mirror")


class TestItRunsAfterEveryFetchAndHourly(unittest.TestCase):

    def test_after_the_fetch(self):
        import os

        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "..", "utils", "client.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"stages", "forecast_stages", _apply_stages', source)

    def test_hourly(self):
        import os

        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "..", "hooks.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        hourly = source.split('"hourly": [', 2)[-1].split("]")[0]
        self.assertIn("kefiya.utils.planned_payment.apply_stage_ladder",
                      hourly)


if __name__ == "__main__":
    unittest.main()
