# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A transfer may not take more than the account has: balance plus line.

Unless the bank keeps the date or a standing order placed it -- those the
bank refuses itself. A balance that is not recent blocks.
"""

import datetime
import importlib
import sys
import types
import unittest

from kefiya.utils import funds_check as fc

NOW = datetime.datetime(2026, 11, 1, 12, 0)


class TestTheRule(unittest.TestCase):

    def test_covered(self):
        got = fc.verdict(900, 1000, 0, NOW - datetime.timedelta(hours=1),
                         NOW)
        self.assertTrue(got["ok"])
        self.assertEqual(got["left"], 1000)

    def test_the_credit_line_counts(self):
        self.assertTrue(fc.verdict(1500, 1000, 600, NOW, NOW)["ok"])
        self.assertFalse(fc.verdict(1700, 1000, 600, NOW, NOW)["ok"])

    def test_what_is_already_on_its_way_counts(self):
        got = fc.verdict(500, 1000, 0, NOW, NOW, in_flight=600)
        self.assertEqual((got["ok"], got["why"], got["left"]),
                         (False, "short", 400))

    def test_exactly_all_of_it(self):
        self.assertTrue(fc.verdict(1000, 1000, 0, NOW, NOW)["ok"])

    def test_a_negative_balance(self):
        self.assertFalse(fc.verdict(1, -50, 0, NOW, NOW)["ok"])
        self.assertTrue(fc.verdict(1, -50, 100, NOW, NOW)["ok"])

    def test_unknown_or_old_blocks(self):
        self.assertEqual(fc.verdict(1, None, 0, NOW, NOW)["why"], "unknown")
        self.assertEqual(fc.verdict(1, 1000, 0, None, NOW)["why"], "unknown")
        old = NOW - datetime.timedelta(days=2, minutes=1)
        self.assertEqual(fc.verdict(1, 1000, 0, old, NOW)["why"], "stale")
        fresh = NOW - datetime.timedelta(days=1, hours=23)
        self.assertTrue(fc.verdict(1, 1000, 0, fresh, NOW)["ok"])

    def test_dates_as_text(self):
        self.assertTrue(fc.verdict(1, 1000, 0, "2026-11-01 08:00:00.123456",
                                   "2026-11-01 12:00:00")["ok"])


class _Doc(types.SimpleNamespace):
    pass


class TestWhatIsLeftToTheBank(unittest.TestCase):

    def test_a_dated_order_at_the_bank(self):
        docs = [_Doc(name="A", standing_order=None)]
        self.assertEqual(fc.exempt(docs, scheduled=True), (docs, []))

    def test_a_standing_order_s_transfer(self):
        a = _Doc(name="A", standing_order="KSO-1")
        b = _Doc(name="B", standing_order=None)
        self.assertEqual(fc.exempt([a, b], scheduled=False), ([a], [b]))


def _fake(account, transfers, may_see=True, stamp=True):
    fake = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")
    utils.flt = lambda v, p=None: float(v or 0)
    utils.fmt_money = lambda v, *a, **k: "{0:.2f}".format(float(v))
    utils.getdate = lambda v: (v.date() if isinstance(v, datetime.datetime)
                               else v if isinstance(v, datetime.date)
                               else datetime.date.fromisoformat(str(v)[:10]))
    utils.now_datetime = lambda: NOW
    fake.utils = utils
    fake._ = lambda s: s
    meta = types.SimpleNamespace(has_field=lambda f: stamp)
    fake.get_meta = lambda dt: meta

    def get_value(doctype, name, fields, as_dict=False):
        if doctype == "Kefiya Login":
            return "ACC-1"
        return account

    fake.db = types.SimpleNamespace(get_value=get_value)

    def get_all(doctype, filters=None, fields=None, **kw):
        out = []
        for t in transfers:
            if t["status"] != filters["status"]:
                continue
            if t["name"] in filters["name"][1]:
                continue
            if "modified" in filters and not t["modified"] > \
                    filters["modified"][1]:
                continue
            out.append(types.SimpleNamespace(**t))
        return out

    fake.get_all = get_all
    bv = types.ModuleType("kefiya.utils.balance_visibility")
    bv.may_see_balances = lambda: may_see
    return fake, utils, bv


class TestTheCheckOnSend(unittest.TestCase):

    def run_check(self, docs, account, transfers=(), may_see=True,
                  stamp=True, scheduled=False):
        keys = ("frappe", "frappe.utils", "kefiya.utils.balance_visibility")
        saved = {k: sys.modules.get(k) for k in keys}
        fake, utils, bv = _fake(account, list(transfers), may_see, stamp)
        sys.modules.update({"frappe": fake, "frappe.utils": utils,
                            "kefiya.utils.balance_visibility": bv})
        try:
            mod = importlib.reload(fc)
            return mod.check(docs, scheduled)
        finally:
            for k, m in saved.items():
                if m is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = m
            importlib.reload(fc)

    ACCOUNT = {"custom_account_balance": 1000.0, "custom_credit_line": 0.0,
               "custom_last_successful_fetch": NOW - datetime.timedelta(
                   hours=2)}

    def doc(self, amount, standing=None):
        return _Doc(name="KT-NEW", kefiya_login="L1", total_amount=amount,
                    standing_order=standing)

    def test_passes(self):
        self.assertIsNone(self.run_check([self.doc(900)], self.ACCOUNT))

    def test_sent_since_the_balance_counts(self):
        sent = {"name": "KT-1", "status": "Sent", "total_amount": 300.0,
                "modified": NOW - datetime.timedelta(hours=1)}
        before = {"name": "KT-0", "status": "Sent", "total_amount": 5000.0,
                  "modified": NOW - datetime.timedelta(hours=3)}
        got = self.run_check([self.doc(900)], self.ACCOUNT, [sent, before])
        self.assertEqual(got["why"], "short")
        self.assertIn("300.00", got["message"])

    def test_without_the_right_no_figures(self):
        got = self.run_check([self.doc(5000)], self.ACCOUNT, may_see=False)
        self.assertEqual(got["message"],
                         "The account does not cover this transfer.")

    def test_old_balance_blocks(self):
        old = dict(self.ACCOUNT, custom_last_successful_fetch=NOW
                   - datetime.timedelta(days=3))
        got = self.run_check([self.doc(1)], old)
        self.assertEqual(got["why"], "stale")

    def test_left_to_the_bank(self):
        self.assertIsNone(self.run_check([self.doc(5000)], self.ACCOUNT,
                                         scheduled=True))
        self.assertIsNone(self.run_check([self.doc(5000, "KSO-1")],
                                         self.ACCOUNT))

    def test_a_site_without_the_stamp(self):
        self.assertIsNone(self.run_check([self.doc(5000)], self.ACCOUNT,
                                         stamp=False))


if __name__ == "__main__":
    unittest.main()
