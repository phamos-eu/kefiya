# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A pending entry whose amount is an MT940 Amount object.

On 31.10.2026 the pending-entry fetch failed at every account with::

    File "planned_payment.py", in _first
        if v not in (None, "", []):
    File "mt940/models.py", in __eq__
        return self.amount == other.amount and ...
    AttributeError: 'NoneType' object has no attribute 'amount'

``in`` compares with ``==``, and the mt940 version on the instance answers
``Amount == None`` by reading ``None.amount``. The stand-in below behaves
the same way, whatever mt940 is installed here.
"""

import datetime
import importlib
import sys
import types
import unittest
from decimal import Decimal


class _InstanceAmount:
    """mt940.models.Amount as the instance has it: == reads other.amount."""

    def __init__(self, amount, currency="EUR"):
        self.amount = Decimal(amount)
        self.currency = currency

    def __eq__(self, other):
        return self.amount == other.amount and self.currency == other.currency


def _fake_frappe():
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: datetime.datetime(2026, 11, 3, 12, 0)
    utils.getdate = lambda value: (
        value if isinstance(value, datetime.date)
        else datetime.date.fromisoformat(str(value)[:10]))
    utils.add_days = lambda d, n: d + datetime.timedelta(days=n)
    utils.add_months = lambda d, n: d
    utils.flt = lambda value, precision=None: float(value or 0)
    utils.cint = lambda value: int(value or 0)
    fake = types.ModuleType("frappe")
    fake.utils = utils
    return fake, utils


class TestPendingEntriesWithAnAmountObject(unittest.TestCase):

    def setUp(self):
        self.saved = {k: sys.modules.get(k)
                      for k in ("frappe", "frappe.utils",
                                "kefiya.utils.planned_payment")}
        fake, utils = _fake_frappe()
        sys.modules["frappe"] = fake
        sys.modules["frappe.utils"] = utils
        sys.modules.pop("kefiya.utils.planned_payment", None)
        self.pp = importlib.import_module("kefiya.utils.planned_payment")

    def tearDown(self):
        for key, module in self.saved.items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module

    def test_the_stand_in_fails_like_the_instance(self):
        with self.assertRaises(AttributeError):
            _InstanceAmount("1.00") in (None, "", [])

    def test_a_debit_becomes_an_outgoing_item(self):
        entry = {"status": "D", "amount": _InstanceAmount("-48.90"),
                 "date": datetime.date(2026, 11, 2),
                 "applicant_name": "Beispiel GmbH", "purpose": "Abo"}
        got = self.pp.normalize_pending_entries([entry])
        self.assertEqual(got["skipped"], 0)
        item = got["items"][0]
        self.assertEqual((item["amount"], item["direction"]),
                         (48.90, "Outgoing"))

    def test_without_status_the_sign_decides(self):
        entry = {"amount": _InstanceAmount("-10.00"),
                 "date": datetime.date(2026, 11, 2)}
        self.assertEqual(
            self.pp.normalize_pending_entries([entry])["items"][0]
            ["direction"], "Outgoing")
        entry["amount"] = _InstanceAmount("10.00")
        self.assertEqual(
            self.pp.normalize_pending_entries([entry])["items"][0]
            ["direction"], "Incoming")

    def test_plain_shapes_still_read(self):
        self.assertEqual(self.pp._coerce_amount("-12,50"), 12.5)
        self.assertEqual(self.pp._coerce_amount({"amount": "7"}), 7.0)
        self.assertIsNone(self.pp._coerce_amount(""))
        self.assertIsNone(self.pp._coerce_amount("abc"))

    def test_blank(self):
        for value in (None, "", [], {}):
            self.assertTrue(self.pp._is_blank(value))
        for value in (0, "x", _InstanceAmount("0")):
            self.assertFalse(self.pp._is_blank(value))


if __name__ == "__main__":
    unittest.main()
