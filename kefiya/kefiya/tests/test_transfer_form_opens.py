# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Two things that made an approved transfer unusable.

**Opening it threw.** The form asked Bank Account for a field it does not
have::

    frappe.db.get_value("Bank Account", bank_account,
        ["custom_account_balance", "custom_credit_line", "account_currency",
         "last_integration_date"])

A Bank Account has no currency of its own. It names the ledger Account it
books to, and the currency belongs to that. The server refuses a field that is
not on the doctype, so every attempt to open a Kefiya Transfer produced

    Feld in der Abfrage nicht erlaubt: account_currency

before the form had finished loading. The same figures already had a reader on
the server -- account_kind.account_standing -- which made the same mistake
silently: has_field() dropped the field, so every standing came back with no
currency at all. One reader now, and it resolves the currency properly.

**The date could not be corrected.** An order approved for a day that has
since passed cannot be sent: the bank cannot execute in the past, and
requested_execution_date() says so, in as many words -- "change the date, or
set it to be held here". Neither was possible, because the submit locked the
field. The order sat unsendable and uneditable and the only way out was to
cancel and re-enter it.

Source-level, deliberately: what broke was a field name and a doctype flag,
and neither needs a bank to be wrong.
"""

import json
import os
import unittest


def _path(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(root, *parts)


def _source(*parts):
    with open(_path(*parts), encoding="utf-8") as handle:
        return handle.read()


def _transfer_doctype():
    with open(_path("kefiya", "doctype", "kefiya_transfer",
                    "kefiya_transfer.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _field(fieldname):
    for field in _transfer_doctype()["fields"]:
        if field["fieldname"] == fieldname:
            return field
    raise AssertionError("{0} is not a field of Kefiya Transfer".format(
        fieldname))


class TestNobodyAsksBankAccountForACurrency(unittest.TestCase):
    """It does not have one. Asking is an error, not an empty answer."""

    #: What a query looks like. The prose explaining WHY the field must not
    #: be asked for naturally contains the word, so the assertions below look
    #: for the quoted form -- which is what a query needs and a sentence does
    #: not have.
    QUOTED = ('"account_currency"', "'account_currency'")

    def _asked_for_in(self, js):
        return [form for form in self.QUOTED if form in js]

    def test_the_form_does_not(self):
        self.assertEqual(self._asked_for_in(_source(
            "kefiya", "doctype", "kefiya_transfer", "kefiya_transfer.js")), [])

    def test_no_browser_code_does(self):
        """Any file, not only the one that was caught: the same three-word
        mistake is copyable and was copied once already."""
        offenders = []
        for base, _dirs, names in os.walk(_path("public", "js")):
            for name in names:
                if not name.endswith(".js"):
                    continue
                with open(os.path.join(base, name), encoding="utf-8") as fh:
                    if self._asked_for_in(fh.read()):
                        offenders.append(os.path.join(base, name))
        self.assertEqual(offenders, [])

    def test_the_server_reads_the_ledger_account_instead(self):
        body = _source("utils", "account_kind.py")
        standing = body.split("def account_standing(bank_account):")[1]
        wanted = standing.split("wanted = [f for f in (")[1].split(")]")[0]
        self.assertIn('"account"', wanted)
        self.assertNotIn("account_currency", wanted)
        self.assertIn('"currency": _currency_of(row.get("account"))', standing)

    def test_the_currency_comes_off_the_account(self):
        body = _source("utils", "account_kind.py")
        helper = body.split("def _currency_of(account):")[1].split(
            "\ndef ")[0]
        self.assertIn(
            'frappe.get_cached_value("Account", account, "account_currency")',
            helper)


class TestOneReaderNotTwo(unittest.TestCase):

    def test_the_form_calls_the_server(self):
        body = _source("kefiya", "doctype", "kefiya_transfer",
                       "kefiya_transfer.js")
        self.assertIn(
            'method: "kefiya.utils.account_kind.account_standing_for",', body)

    def test_the_endpoint_is_whitelisted_and_says_no_itself(self):
        """Reachable by any logged-in user, and it answers how much money is
        on a named account."""
        body = _source("utils", "account_kind.py")
        endpoint = body.split("def account_standing_for(bank_account):")[1]
        self.assertIn("@frappe.whitelist()", body.split(
            "def account_standing_for")[0][-40:])
        self.assertIn(
            'frappe.has_permission("Bank Account", ptype="read",',
            endpoint.split("return account_standing(bank_account)")[0])

    def test_the_reader_itself_is_not_gated(self):
        """transfer_sources() calls it per row for logins get_list already
        let the user see. A gate inside would re-ask an answered question and
        break the whole list over one account rather than omit it."""
        body = _source("utils", "account_kind.py")
        reader = body.split("def account_standing(bank_account):")[1].split(
            "\ndef ")[0]
        self.assertNotIn("has_permission", reader)

    def test_the_form_reads_the_names_the_server_answers_with(self):
        """balance/credit_line/currency/as_of, not the raw column names."""
        body = _source("kefiya", "doctype", "kefiya_transfer",
                       "kefiya_transfer.js")
        standing = body.split("function kefiya_show_account_standing(")[1]
        for name in ("a.balance", "a.credit_line", "a.currency", "a.as_of"):
            self.assertIn(name, standing)
        self.assertNotIn("custom_account_balance", standing)


class TestAnExpiredDateCanBeCorrected(unittest.TestCase):

    def test_the_field_is_editable_after_approval(self):
        self.assertEqual(_field("execution_date").get("allow_on_submit"), 1)

    def test_what_the_approval_was_about_stays_locked(self):
        """A date says WHEN the approved payment happens, not what it is."""
        for locked in ("items", "kefiya_login", "total_amount"):
            self.assertNotEqual(_field(locked).get("allow_on_submit"), 1,
                                "{0} must stay locked".format(locked))

    def test_an_order_already_with_the_bank_is_refused(self):
        body = _source("kefiya", "doctype", "kefiya_transfer",
                       "kefiya_transfer.py")
        hook = body.split("def on_update_after_submit(self):")[1].split(
            "\n    @frappe.whitelist()")[0]
        self.assertIn('if self.status in ("Sent", "Scheduled at Bank"):', hook)
        self.assertIn("frappe.throw", hook.split(
            'if self.status in ("Sent", "Scheduled at Bank"):')[1][:200])

    def test_a_date_that_is_not_in_the_future_becomes_ours_to_hold(self):
        """The same correction validate() makes on a draft. Without it the
        order is still unsendable after the date is fixed."""
        body = _source("kefiya", "doctype", "kefiya_transfer",
                       "kefiya_transfer.py")
        hook = body.split("def on_update_after_submit(self):")[1].split(
            "\n    @frappe.whitelist()")[0]
        self.assertIn("getdate(self.execution_date) <= now_datetime().date()",
                      hook)
        self.assertIn('self.db_set("manage_due_date", 1', hook)


if __name__ == "__main__":
    unittest.main()
