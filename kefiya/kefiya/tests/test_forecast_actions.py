# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Correcting the forecast by hand.

"Delete" is a cancellation with a reason: a deleted line comes back on the
next run of whatever wrote it, a cancelled one does not. These tests run the
actions against a stand-in for frappe and check what they write, and that
they ask for the write permission line by line.
"""

import importlib
import sys
import types
import unittest


class _Throw(Exception):
    pass


class _Doc:
    def __init__(self, store, values):
        self.__dict__["store"] = store
        self.__dict__["values"] = dict(values)
        self.__dict__["comments"] = []

    def __getattr__(self, key):
        return self.__dict__["values"].get(key)

    def __setattr__(self, key, value):
        self.__dict__["values"][key] = value

    def save(self):
        self.store["saved"].append(dict(self.values))

    def insert(self):
        self.store["inserted"].append(dict(self.values))
        self.values["name"] = "MAN-new"
        return self

    def add_comment(self, kind, text):
        self.comments.append(text)


def _fake(rows):
    store = {"saved": [], "inserted": [], "docs": {}}
    fake = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")
    utils.flt = lambda v, p=None: float(v or 0)
    utils.getdate = lambda v: v
    fake.utils = utils
    fake._ = lambda text: text

    def throw(message):
        raise _Throw(message)

    fake.throw = throw
    fake.parse_json = lambda text: __import__("json").loads(text)
    fake.whitelist = lambda *a, **k: (lambda fn: fn)

    def get_doc(values, name=None):
        if name is None:
            return _Doc(store, values)
        doc = _Doc(store, rows[name])
        store["docs"][name] = doc
        return doc

    fake.get_doc = get_doc

    def has_permission(doctype, ptype="read", doc=None, throw=False):
        store.setdefault("asked", []).append(
            (ptype, getattr(doc, "name", None)))
        if doc is not None and doc.values.get("locked"):
            raise _Throw("no permission")
        return True

    fake.has_permission = has_permission
    return fake, utils, store


class _Base(unittest.TestCase):

    def load(self, rows):
        self.saved = {k: sys.modules.get(k) for k in
                      ("frappe", "frappe.utils",
                       "kefiya.utils.forecast_actions")}
        fake, utils, store = _fake(rows)
        sys.modules["frappe"] = fake
        sys.modules["frappe.utils"] = utils
        sys.modules.pop("kefiya.utils.forecast_actions", None)
        self.store = store
        return importlib.import_module("kefiya.utils.forecast_actions")

    def tearDown(self):
        for key, module in getattr(self, "saved", {}).items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module


class TestCancel(_Base):

    ROWS = {
        "A": {"name": "A", "status": "Open"},
        "B": {"name": "B", "status": "Matched"},
        "C": {"name": "C", "status": "Expired"},
    }

    def test_with_a_reason_and_the_write_right(self):
        fa = self.load(dict(self.ROWS))
        got = fa.cancel_lines('["A", "B", "C"]', "Duplicate", note="twice")
        self.assertEqual(got["cancelled"], ["A", "C"])
        self.assertEqual([s["name"] for s in got["skipped"]], ["B"])
        for saved in self.store["saved"]:
            self.assertEqual((saved["status"], saved["cancellation_reason"]),
                             ("Cancelled", "Duplicate"))
        self.assertEqual(self.store["asked"],
                         [("write", "A"), ("write", "B"), ("write", "C")])
        self.assertEqual(self.store["docs"]["A"].comments, ["twice"])

    def test_one_name_is_a_list_of_one(self):
        fa = self.load(dict(self.ROWS))
        self.assertEqual(fa.cancel_lines("A", "Other")["cancelled"], ["A"])

    def test_not_the_ladder_s_reason(self):
        """That one the ladder reopens on its own -- a person's
        cancellation must not look like it."""
        fa = self.load(dict(self.ROWS))
        with self.assertRaises(_Throw):
            fa.cancel_lines("A", "Superseded by higher stage")
        self.assertNotIn("Superseded by higher stage", fa.REASONS)

    def test_without_the_right_nothing_is_written(self):
        fa = self.load({"A": {"name": "A", "status": "Open",
                              "locked": 1}})
        with self.assertRaises(_Throw):
            fa.cancel_lines("A", "Other")
        self.assertEqual(self.store["saved"], [])


class TestReopen(_Base):

    def test_cancelled_and_expired_come_back(self):
        fa = self.load({
            "A": {"name": "A", "status": "Cancelled",
                  "cancellation_reason": "Duplicate", "superseded_by": "X"},
            "B": {"name": "B", "status": "Expired"},
            "C": {"name": "C", "status": "Open"}})
        got = fa.reopen_lines(["A", "B", "C"])
        self.assertEqual(got["reopened"], ["A", "B"])
        self.assertEqual(self.store["saved"][0]["status"], "Open")
        self.assertIsNone(self.store["saved"][0]["cancellation_reason"])
        self.assertIsNone(self.store["saved"][0]["superseded_by"])


class TestCreate(_Base):

    def test_a_manual_plan(self):
        fa = self.load({})
        name = fa.create_line("ACC-1", "2026-12-01", "250", "Incoming",
                              counterparty_name="Tenant", purpose="Deposit")
        self.assertEqual(name, "MAN-new")
        self.assertEqual(self.store["asked"], [("create", None)])
        doc = self.store["inserted"][0]
        self.assertEqual(
            (doc["bank_account"], doc["expected_amount"], doc["direction"],
             doc["source_kind"], doc["is_manual"], doc["status"]),
            ("ACC-1", 250.0, "Incoming", "Manual", 1, "Open"))
        self.assertNotIn("reference_key", doc,
                         "the controller gives it one")

    def test_refuses_nonsense(self):
        fa = self.load({})
        for args in (("ACC-1", "2026-12-01", 0),
                     ("ACC-1", "2026-12-01", 5, "Sideways")):
            with self.assertRaises(_Throw):
                fa.create_line(*args)


class TestTheControllerNamesAManualLine(unittest.TestCase):

    def test_key_kind_and_company(self):
        saved = {k: sys.modules.get(k) for k in (
            "frappe", "frappe.model", "frappe.model.document")}
        fake = types.ModuleType("frappe")
        fake.generate_hash = lambda length=10: "x" * length
        fake.db = types.SimpleNamespace(
            get_value=lambda dt, name, field: "C1")
        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")
        document.Document = type("Document", (), {})
        sys.modules.update({"frappe": fake, "frappe.model": model,
                            "frappe.model.document": document})
        try:
            mod = importlib.import_module(
                "kefiya.kefiya.doctype.kefiya_planned_payment."
                "kefiya_planned_payment")
            mod = importlib.reload(mod)
            doc = mod.KefiyaPlannedPayment()
            doc.reference_key = None
            doc.source_kind = None
            doc.company = None
            doc.bank_account = "ACC-1"
            doc.before_insert()
            self.assertEqual(
                (doc.reference_key, doc.is_manual, doc.source_kind,
                 doc.company), ("MAN-xxxxxxxxxx", 1, "Manual", "C1"))

            kept = mod.KefiyaPlannedPayment()
            kept.reference_key = "SI-1"
            kept.source_kind = "Sales Invoice"
            kept.company = "C2"
            kept.bank_account = "ACC-1"
            kept.before_insert()
            self.assertEqual((kept.reference_key, kept.company),
                             ("SI-1", "C2"))
        finally:
            for key, module in saved.items():
                if module is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = module


if __name__ == "__main__":
    unittest.main()
