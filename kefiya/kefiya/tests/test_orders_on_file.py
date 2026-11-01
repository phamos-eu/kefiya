# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Run against a stand-in for frappe: the bank's standing orders put on
file, open purchase orders in the forecast, and the standing-order list of
the outgoing payments.
"""

import datetime
import importlib
import sys
import types
import unittest

IBAN_A = "DE89370400440532013000"   # published example
IBAN_B = "DE02120300000000202051"   # published example
TODAY = "2026-11-01"


def _match(row, filters):
    for key, want in (filters or {}).items():
        have = row.get(key)
        if isinstance(want, (tuple, list)) and want and want[0] == "in":
            if have not in want[1]:
                return False
        elif isinstance(want, (tuple, list)) and want and want[0] == "not in":
            if have in want[1]:
                return False
        elif isinstance(want, (tuple, list)) and want and want[0] == "<":
            if not (have is not None and have < want[1]):
                return False
        elif isinstance(want, (tuple, list)) and want and want[0] == ">":
            if not (have is not None and have > want[1]):
                return False
        elif isinstance(want, (tuple, list)) and want and want[0] == ">=":
            if not (have is not None and str(have) >= str(want[1])):
                return False
        elif have != want:
            return False
    return True


class _Doc(dict):
    def __init__(self, store, doctype, values):
        super().__init__(values)
        self.__dict__["store"] = store
        self.__dict__["doctype"] = doctype

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    def save(self, **kwargs):
        for row in self.store.setdefault(self.doctype, []):
            if row["name"] == self["name"]:
                row.update(self)
        self.store.setdefault("_saved", []).append((self.doctype, dict(self)))
        return self

    def insert(self, **kwargs):
        row = dict(self)
        row.pop("doctype", None)
        row["name"] = row.get("name") or row.get("reference_key") \
            or "KEF-SO-{0}".format(len(self.store.get(self.doctype, [])) + 1)
        self["name"] = row["name"]
        table = self.store.setdefault(self.doctype, [])
        if any(r["name"] == row["name"] for r in table):
            return self
        table.append(row)
        return self


def _fake(store):
    fake = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")
    utils.nowdate = lambda: TODAY
    utils.flt = lambda v, p=None: float(v or 0)
    utils.getdate = lambda v: (v if isinstance(v, datetime.date)
                               else datetime.date.fromisoformat(str(v)[:10]))
    utils.now_datetime = lambda: datetime.datetime(2026, 11, 1, 12)
    utils.add_days = lambda d, n: (datetime.date.fromisoformat(str(d)[:10])
                                   + datetime.timedelta(days=n)).isoformat()
    fake.utils = utils
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake._ = lambda text: text
    fake.only_for = lambda roles: None
    fake.has_permission = lambda *a, **k: True
    fake.logs = []
    fake.log_error = lambda title=None, message=None: fake.logs.append(title)

    def get_all(doctype, filters=None, fields=None, **kwargs):
        return [dict(r) for r in store.get(doctype, []) if _match(r, filters)]

    def get_doc(values, name=None):
        if name is None:
            values = dict(values)
            return _Doc(store, values.pop("doctype"), values)
        row = [r for r in store.get(values, []) if r["name"] == name][0]
        return _Doc(store, values, dict(row))

    def get_value(doctype, name, fields):
        row = [r for r in store.get(doctype, []) if r["name"] == name]
        if not row:
            return None
        if isinstance(fields, (list, tuple)):
            return tuple(row[0].get(f) for f in fields)
        return row[0].get(fields)

    def set_value(doctype, name, values, *a, **k):
        for row in store.get(doctype, []):
            if row["name"] == name:
                row.update(values)

    fake.get_all = get_all
    fake.get_list = get_all
    fake.get_doc = get_doc
    fake.db = types.SimpleNamespace(get_value=get_value, set_value=set_value)
    return fake, utils


class _Base(unittest.TestCase):
    MODULES = ()

    def load(self, module, store):
        self.saved = {k: sys.modules.get(k) for k in
                      ("frappe", "frappe.utils", module)}
        fake, utils = _fake(store)
        sys.modules["frappe"] = fake
        sys.modules["frappe.utils"] = utils
        sys.modules.pop(module, None)
        self.fake = fake
        return importlib.import_module(module)

    def tearDown(self):
        for key, mod in getattr(self, "saved", {}).items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod


def _bank_row(amount=600.0, iban=IBAN_A, day="5", task="7"):
    from kefiya.utils import bank_held_orders as orders

    return {"amount": amount, "recipient_name": "Erika Mustermann",
            "recipient_iban": iban, "purpose": "Pflege", "task_id": task,
            "schedule": orders.schedule_from(
                ["20260105", "M", "1", day])}


class TestTheBanksStandingOrdersOnFile(_Base):

    def store(self, orders):
        return {
            "Kefiya Login": [{"name": "L1", "bank_account": "ACC-1",
                              "company": "C1"}],
            "Bank Account": [{"name": "ACC-1", "company": "C1"}],
            "Kefiya Standing Order": orders,
        }

    def test_a_new_one_is_created_held_by_the_bank(self):
        store = self.store([])
        sync = self.load("kefiya.utils.bank_standing_orders", store)
        got = sync.sync("L1", [_bank_row()])
        self.assertEqual(got["created"], 1)
        order = store["Kefiya Standing Order"][0]
        self.assertEqual(
            (order["status"], order["execution_mode"], order["bank_account"],
             order["company"], order["amount"], order["day_of_month"],
             order["frequency"], order["detected_by"], order["bank_seen_on"],
             order["bank_task_id"]),
            ("Active", "Bank", "ACC-1", "C1", 600.0, 5, "Monthly", "bank",
             TODAY, "7"))

    def test_twice_is_once(self):
        store = self.store([])
        sync = self.load("kefiya.utils.bank_standing_orders", store)
        sync.sync("L1", [_bank_row()])
        got = sync.sync("L1", [_bank_row()])
        self.assertEqual((got["created"], got["confirmed"]), (0, 1))
        self.assertEqual(len(store["Kefiya Standing Order"]), 1)

    def test_a_proposal_the_bank_confirms_becomes_active(self):
        store = self.store([{
            "name": "KSO-1", "status": "Proposed", "execution_mode": "Bank",
            "bank_account": "ACC-1", "amount": 600.0, "day_of_month": 5,
            "counterparty_iban": IBAN_A, "bank_task_id": None,
            "bank_seen_on": None}])
        sync = self.load("kefiya.utils.bank_standing_orders", store)
        got = sync.sync("L1", [_bank_row()])
        order = store["Kefiya Standing Order"][0]
        self.assertEqual((got["created"], got["confirmed"]), (0, 1))
        self.assertEqual((order["status"], order["bank_seen_on"],
                          order["bank_task_id"]), ("Active", TODAY, "7"))

    def test_kept_here_and_at_the_bank_is_reported_not_changed(self):
        """That pays twice; which one goes is a person's decision."""
        row = {"name": "KSO-1", "status": "Active",
               "execution_mode": "System", "bank_account": "ACC-1",
               "amount": 600.0, "day_of_month": 5,
               "counterparty_iban": IBAN_A, "bank_task_id": None,
               "bank_seen_on": None}
        store = self.store([dict(row)])
        sync = self.load("kefiya.utils.bank_standing_orders", store)
        got = sync.sync("L1", [_bank_row()])
        self.assertEqual(got["conflicts"], ["KSO-1"])
        self.assertEqual(store["Kefiya Standing Order"][0], row)
        self.assertEqual(self.fake.logs, [sync.LOG_TITLE])

    def test_gone_from_the_bank_is_ended(self):
        store = self.store([{
            "name": "KSO-1", "status": "Active", "execution_mode": "Bank",
            "bank_account": "ACC-1", "amount": 30.0, "day_of_month": 1,
            "counterparty_iban": IBAN_B, "bank_task_id": "9",
            "bank_seen_on": "2026-10-01"}])
        sync = self.load("kefiya.utils.bank_standing_orders", store)
        got = sync.sync("L1", [_bank_row()])
        self.assertEqual(got["ended"], 1)
        self.assertEqual(store["Kefiya Standing Order"][0]["status"],
                         "Ended")

    def test_one_the_bank_never_listed_is_left_alone(self):
        """Typed in by hand, never reported: not the bank's to end."""
        store = self.store([{
            "name": "KSO-1", "status": "Active", "execution_mode": "Bank",
            "bank_account": "ACC-1", "amount": 30.0, "day_of_month": 1,
            "counterparty_iban": IBAN_B, "bank_task_id": None,
            "bank_seen_on": None}])
        sync = self.load("kefiya.utils.bank_standing_orders", store)
        self.assertEqual(sync.sync("L1", [_bank_row()])["ended"], 0)

    def test_unreadable_rows_are_counted_not_guessed(self):
        store = self.store([])
        sync = self.load("kefiya.utils.bank_standing_orders", store)
        row = _bank_row()
        row["schedule"] = {"raw": ["?"]}
        got = sync.sync("L1", [row])
        self.assertEqual(got["created"], 0)
        self.assertEqual(got["skipped"], {"schedule not readable": 1})


class TestOpenPurchaseOrders(_Base):

    def load_orders(self, store):
        return self.load("kefiya.utils.order_forecast", store)

    def test_what_is_still_to_pay(self):
        of = self.load_orders({})
        self.assertEqual(of.remaining({"grand_total": 1000}), 1000)
        self.assertEqual(of.remaining({"grand_total": 1000,
                                       "per_billed": 40}), 600)
        # An advance of 100 and a partial invoice of 400: the advance sits
        # in the invoice, 600 is still to come from the order.
        self.assertEqual(of.remaining({"grand_total": 1000, "per_billed": 40,
                                       "advance_paid": 100}), 600)
        # Paid 700 at the bank on it, 400 billed: 300 to come.
        self.assertEqual(of.remaining({"grand_total": 1000,
                                       "per_billed": 40}, 700), 300)
        self.assertEqual(of.remaining({"grand_total": 100,
                                       "advance_paid": 500}), 0)

    ORDERS = [
        {"name": "AUF-1", "supplier": "S1", "supplier_name": "Builder Ltd",
         "transaction_date": "2026-09-01"},
        {"name": "AUF-2", "supplier": "S2", "supplier_name": "Roofer Ltd",
         "transaction_date": "2026-09-01"},
        {"name": "AUF-3", "supplier": "S2", "supplier_name": "Roofer Ltd",
         "transaction_date": "2026-09-01"},
    ]

    def test_partial_payments_naming_the_order(self):
        of = self.load_orders({})
        got = of.paid_at_bank(self.ORDERS, [
            {"date": "2026-09-10", "withdrawal": 500,
             "description": "Abschlag AUF-1", "unallocated_amount": 0},
            {"date": "2026-10-10", "withdrawal": 300,
             "description": "2. Abschlag zu AUF-1", "unallocated_amount": 300},
            {"date": "2026-08-01", "withdrawal": 99,
             "description": "AUF-1, before the order",
             "unallocated_amount": 99},
        ])
        self.assertEqual(got, {"AUF-1": 800})

    def test_only_the_supplier_when_it_is_unambiguous(self):
        of = self.load_orders({})
        got = of.paid_at_bank(self.ORDERS, [
            # One open order of this supplier, not allocated: counts.
            {"date": "2026-09-10", "withdrawal": 200,
             "bank_party_name": "builder ltd", "unallocated_amount": 200},
            # Allocated to something (an invoice): does not.
            {"date": "2026-09-11", "withdrawal": 50,
             "bank_party_name": "Builder Ltd", "unallocated_amount": 0},
            # Two open orders of this supplier: which one is a guess.
            {"date": "2026-09-12", "withdrawal": 70,
             "bank_party_name": "Roofer Ltd", "unallocated_amount": 70},
            # By IBAN, from the supplier's bank account.
            {"date": "2026-09-13", "withdrawal": 30,
             "bank_party_iban": "DE89 3704 0044 0532 0130 00",
             "unallocated_amount": 30},
        ], {"S1": {IBAN_A}})
        self.assertEqual(got, {"AUF-1": 230})

    def test_the_due_date(self):
        of = self.load_orders({})
        order = {"schedule_date": "2026-12-01",
                 "transaction_date": "2026-10-01"}
        self.assertEqual(of.due_date(order, []), "2026-12-01")
        self.assertEqual(of.due_date(order, [
            {"due_date": "2026-11-20", "outstanding": 0},
            {"due_date": "2026-12-15", "outstanding": 50}]), "2026-12-15")
        self.assertEqual(of.due_date({"transaction_date": "2026-10-01"}, []),
                         "2026-10-01")

    def store(self, po, lines=()):
        return {
            "Purchase Order": [po],
            "Payment Schedule": [],
            "Bank Account": [{"name": "ACC-1", "company": "C1",
                              "is_company_account": 1, "is_default": 1,
                              "disabled": 0}],
            "Kefiya Planned Payment": list(lines),
        }

    PO = {"name": "PO-1", "company": "C1", "supplier": "S1",
          "supplier_name": "Supplier One", "grand_total": 1190.0,
          "per_billed": 0, "advance_paid": 0, "schedule_date": "2026-11-20",
          "transaction_date": "2026-11-01", "docstatus": 1,
          "status": "To Receive and Bill"}

    def test_an_open_order_becomes_a_line(self):
        store = self.store(dict(self.PO))
        of = self.load_orders(store)
        got = of._refresh()
        self.assertEqual(got["created"], 1)
        line = store["Kefiya Planned Payment"][0]
        self.assertEqual(
            (line["name"], line["source_kind"], line["direction"],
             line["expected_amount"], str(line["due_date"]),
             line["bank_account"], line["party"], line["generated_by"]),
            ("PO-PO-1", "Purchase Order", "Outgoing", 1190.0, "2026-11-20",
             "ACC-1", "S1", "kefiya_orders"))
        self.assertIn("PO-1", line["purpose"])

    def test_a_partial_payment_at_the_bank_shrinks_it(self):
        store = self.store(dict(self.PO))
        store["Bank Transaction"] = [{
            "name": "BT-1", "docstatus": 1, "date": "2026-11-05",
            "withdrawal": 400.0, "description": "Anzahlung PO-1",
            "unallocated_amount": 400.0}]
        of = self.load_orders(store)
        of._refresh()
        self.assertEqual(
            store["Kefiya Planned Payment"][0]["expected_amount"], 790.0)

    def test_billing_shrinks_it(self):
        line = {"name": "PO-PO-1", "status": "Open", "generated_by":
                "kefiya_orders", "expected_amount": 1190.0,
                "due_date": "2026-11-20", "bank_account": "ACC-1",
                "cancellation_reason": None}
        store = self.store(dict(self.PO, per_billed=50), [line])
        of = self.load_orders(store)
        self.assertEqual(of._refresh()["updated"], 1)
        self.assertEqual(line["expected_amount"], 595.0)

    def test_a_closed_order_takes_its_line(self):
        line = {"name": "PO-PO-1", "status": "Open", "generated_by":
                "kefiya_orders", "expected_amount": 1190.0,
                "due_date": "2026-11-20", "bank_account": "ACC-1",
                "cancellation_reason": None}
        store = self.store(dict(self.PO, status="Closed"), [line])
        of = self.load_orders(store)
        self.assertEqual(of._refresh()["closed"], 1)
        self.assertEqual((line["status"], line["cancellation_reason"]),
                         ("Cancelled", "Settlement"))

    def test_a_person_s_cancellation_is_kept(self):
        line = {"name": "PO-PO-1", "status": "Cancelled", "generated_by":
                "kefiya_orders", "expected_amount": 1190.0,
                "due_date": "2026-11-20", "bank_account": "ACC-1",
                "cancellation_reason": "Duplicate"}
        store = self.store(dict(self.PO, per_billed=50), [line])
        of = self.load_orders(store)
        got = of._refresh()
        self.assertEqual((got["created"], got["updated"]), (0, 0))
        self.assertEqual(line["status"], "Cancelled")


class TestTheStandingOrderList(_Base):

    ORDERS = [
        {"name": "A", "status": "Active", "execution_mode": "Bank",
         "amount": 300.0, "frequency": "Quarterly",
         "bank_seen_on": "2026-10-30", "next_execution_date": None,
         "valid_until": None},
        {"name": "B", "status": "Active", "execution_mode": "System",
         "amount": 100.0, "frequency": "Monthly", "bank_seen_on": None,
         "next_execution_date": None, "valid_until": None},
        {"name": "C", "status": "Proposed", "execution_mode": "Bank",
         "amount": 50.0, "frequency": "Monthly", "bank_seen_on": None,
         "next_execution_date": None, "valid_until": None},
        {"name": "D", "status": "Ended", "execution_mode": "Bank",
         "amount": 70.0, "frequency": "Monthly",
         "bank_seen_on": "2026-01-01", "next_execution_date": None,
         "valid_until": None},
    ]

    def test_scopes_counts_and_the_monthly_sum(self):
        ov = self.load("kefiya.utils.standing_order_overview",
                       {"Kefiya Standing Order": [dict(o) for o in
                                                  self.ORDERS]})
        got = ov.overview("current")
        self.assertEqual([r["name"] for r in got["rows"]], ["A", "B"])
        self.assertEqual(got["counts"], {"current": 2, "proposals": 1,
                                         "ended": 1, "all": 4})
        self.assertEqual(got["monthly"], 200.0)
        self.assertEqual(len(ov.overview("all")["rows"]), 4)

    def test_what_the_bank_says(self):
        ov = self.load("kefiya.utils.standing_order_overview", {})
        check = ov.bank_check
        self.assertIsNone(check({"execution_mode": "System"}, TODAY))
        self.assertEqual(check({"execution_mode": "Bank"}, TODAY), "never")
        self.assertEqual(check({"execution_mode": "Bank",
                                "bank_seen_on": "2026-10-30"}, TODAY),
                         "reported")
        self.assertEqual(check({"execution_mode": "Bank",
                                "bank_seen_on": "2026-09-01"}, TODAY),
                         "stale")


if __name__ == "__main__":
    unittest.main()
