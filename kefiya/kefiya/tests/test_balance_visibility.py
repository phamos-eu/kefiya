# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Who may see what is on a bank account.

The site decides with a field permission level; code that reads the balance
fields itself -- the account standing at transfer entry, the fetch log --
asks before it hands a number on. Run against a stand-in for frappe.
"""

import importlib
import inspect
import os
import sys
import types
import unittest


def _fake(permlevel, levels, user="u@example.com"):
    fake = types.ModuleType("frappe")
    field = types.SimpleNamespace(permlevel=permlevel)
    meta = types.SimpleNamespace(
        get_field=lambda name: field if name == "custom_account_balance"
        else None,
        get_permlevel_access=lambda ptype="read", user=None: list(levels),
        has_field=lambda name: True)
    fake.get_meta = lambda doctype: meta
    fake.session = types.SimpleNamespace(user=user)
    fake.db = types.SimpleNamespace(
        get_value=lambda *a, **k: {"custom_account_balance": 5000.0,
                                   "custom_credit_line": 0.0,
                                   "account": None,
                                   "last_integration_date": "2026-11-01"})
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake._ = lambda s: s
    return fake


class _Base(unittest.TestCase):

    def load(self, module, fake):
        self.saved = {k: sys.modules.get(k) for k in
                      ("frappe", "kefiya.utils.balance_visibility", module)}
        sys.modules["frappe"] = fake
        sys.modules.pop("kefiya.utils.balance_visibility", None)
        sys.modules.pop(module, None)
        return importlib.import_module(module)

    def tearDown(self):
        for key, mod in getattr(self, "saved", {}).items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod


class TestTheSiteDecides(_Base):

    def test_level_zero_says_nothing(self):
        bv = self.load("kefiya.utils.balance_visibility", _fake(0, [0]))
        self.assertTrue(bv.may_see_balances())

    def test_a_raised_level_needs_the_role(self):
        bv = self.load("kefiya.utils.balance_visibility", _fake(1, [0]))
        self.assertFalse(bv.may_see_balances())
        bv = self.load("kefiya.utils.balance_visibility", _fake(1, [0, 1]))
        self.assertTrue(bv.may_see_balances())

    def test_administrator(self):
        bv = self.load("kefiya.utils.balance_visibility",
                       _fake(1, [0], user="Administrator"))
        self.assertTrue(bv.may_see_balances())


class TestTheStandingAtTransferEntry(_Base):

    def standing(self, levels):
        sys.modules.setdefault("frappe.utils", types.ModuleType(
            "frappe.utils"))
        ak = self.load("kefiya.utils.account_kind", _fake(1, levels))
        ak._currency_of = lambda account: "EUR"
        return ak.account_standing("ACC-1")

    def test_hidden_without_the_level(self):
        got = self.standing([0])
        self.assertEqual(got.get("hidden"), 1)
        self.assertIsNone(got.get("balance"))
        self.assertNotIn("available", got)

    def test_shown_with_it(self):
        got = self.standing([0, 1])
        self.assertEqual(got.get("balance"), 5000.0)
        self.assertNotIn("hidden", got)


class TestTheFetchLog(unittest.TestCase):

    def test_the_summary_is_scrubbed(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "utils", "client.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        part = source.split('summary["balance"] = _optional_fetch(')[1]
        part = part.split("# 2b)")[0]
        self.assertIn('if summary["balance"] and not may_see_balances():',
                      part)
        self.assertIn('"hidden": 1', part)
        self.assertNotIn('"balance": summary', part)


class TestAMigrateKeepsTheSitesLevel(unittest.TestCase):

    def test_existing_levels_are_carried(self):
        saved = {k: sys.modules.get(k) for k in (
            "frappe", "frappe.custom", "frappe.custom.doctype",
            "frappe.custom.doctype.custom_field",
            "frappe.custom.doctype.custom_field.custom_field",
            "frappe.custom.doctype.property_setter",
            "frappe.custom.doctype.property_setter.property_setter",
            "kefiya.setup.install")}
        fake = types.ModuleType("frappe")
        levels = {("Bank Account", "custom_account_balance"): 1}
        fake.db = types.SimpleNamespace(
            get_value=lambda dt, flt, field: levels.get(
                (flt["dt"], flt["fieldname"])))
        cf = types.ModuleType(
            "frappe.custom.doctype.custom_field.custom_field")
        cf.create_custom_fields = lambda *a, **k: None
        ps = types.ModuleType(
            "frappe.custom.doctype.property_setter.property_setter")
        ps.make_property_setter = lambda *a, **k: None
        for name in ("frappe.custom", "frappe.custom.doctype",
                     "frappe.custom.doctype.custom_field",
                     "frappe.custom.doctype.property_setter"):
            sys.modules[name] = types.ModuleType(name)
        sys.modules["frappe"] = fake
        sys.modules["frappe.custom.doctype.custom_field.custom_field"] = cf
        sys.modules[
            "frappe.custom.doctype.property_setter.property_setter"] = ps
        sys.modules.pop("kefiya.setup.install", None)
        try:
            install = importlib.import_module("kefiya.setup.install")
            got = install.keep_site_permlevels({
                "Bank Account": [{"fieldname": "custom_account_balance"},
                                 {"fieldname": "custom_credit_line"}]})
            self.assertEqual(got["Bank Account"][0]["permlevel"], 1)
            self.assertNotIn("permlevel", got["Bank Account"][1])
            self.assertIn("keep_site_permlevels(get_custom_fields())",
                          inspect.getsource(install.after_migrate))
        finally:
            for key, mod in saved.items():
                if mod is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = mod


if __name__ == "__main__":
    unittest.main()
