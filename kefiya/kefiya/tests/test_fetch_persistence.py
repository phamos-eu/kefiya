# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import inspect
import unittest

import frappe

from kefiya.utils import client, fetch_persistence, planned_payment
from kefiya.utils.fints_controller import FinTSController


class TestNothingIsOnlyCounted(unittest.TestCase):
    """A fetch used to pull documents and credit-card transactions from the
    bank and then report a bare count -- the data was dropped on the floor."""

    def test_every_retrieval_persists_something(self):
        source = inspect.getsource(client.fetch_all)
        self.assertNotIn(
            '"count": len(', source,
            "Counting a result instead of storing it is the whole defect.",
        )
        for helper in ("store_balance", "download_statements",
                       "store_credit_card_transactions"):
            self.assertIn(
                "fetch_persistence." + helper, source,
                "{0} must be reached from fetch_all.".format(helper))

    def test_holdings_are_fetched_too(self):
        source = inspect.getsource(client.fetch_all)
        self.assertIn("get_fints_holdings()", source)
        self.assertIn("refresh_holdings", source)


class TestBalanceIsStored(unittest.TestCase):
    """The balance was returned to the caller and forgotten."""

    def test_it_lands_on_the_bank_account(self):
        source = inspect.getsource(fetch_persistence.store_balance)
        self.assertIn("custom_account_balance", source)
        self.assertIn("custom_credit_line", source)
        self.assertIn(
            "account.db_set(", source,
            "Through the document object, not a hand-written UPDATE.",
        )
        self.assertNotIn(
            "account.save(", source,
            "A full save runs Bank Account.validate(), which clears is_default "
            "across every account of the company -- a range lock taken to "
            "store one number, and the deadlock of 15.08.",
        )

    def test_the_right_row_is_picked(self):
        """A bank may answer for several accounts in one HISAL response."""
        source = inspect.getsource(fetch_persistence.store_balance)
        self.assertIn('r.get("iban") == login.account_iban', source)

    def test_missing_custom_fields_do_not_fail_the_fetch(self):
        source = inspect.getsource(fetch_persistence.store_balance)
        self.assertIn('meta.has_field("custom_account_balance")', source)

    def test_the_target_fields_exist_on_this_instance(self):
        meta = frappe.get_meta("Bank Account")
        self.assertTrue(meta.has_field("custom_account_balance"))
        self.assertTrue(meta.has_field("custom_credit_line"))


class TestBalanceOnTheBookings(unittest.TestCase):
    """The bank states one number: the balance now. The balance after an
    individual booking has to be counted backwards from it -- and that count is
    only true where nothing is missing in between."""

    def _source(self):
        return inspect.getsource(fetch_persistence.apply_running_balance)

    def test_it_counts_backwards_from_the_current_balance(self):
        source = self._source()
        self.assertIn('order_by="date desc', source)
        self.assertIn(
            "running = flt(running) - flt(row.deposit) + flt(row.withdrawal)",
            source,
            "Undoing a booking means subtracting what came in and adding back "
            "what went out.",
        )

    def test_only_the_window_that_was_just_fetched_is_filled(self):
        """Outside it a gap cannot be ruled out, and a wrong balance that
        looks right is worse than an empty field."""
        source = self._source()
        self.assertIn('"date": ("between", [start, anchor])', source)
        self.assertIn('result["reason"] = "no fetch window"', source)

    def test_drafts_are_not_bookings(self):
        source = self._source()
        self.assertIn('"docstatus": 1', source)

    def test_the_anchor_is_the_balance_date(self):
        """A booking the bank had not counted yet must not be counted here."""
        source = self._source()
        self.assertIn("getdate(balance_date)", source)
        controller = inspect.getsource(FinTSController.get_fints_balance)
        self.assertIn('"balance_date": balance_date', controller)

    def test_a_missing_custom_field_does_not_fail_the_fetch(self):
        source = self._source()
        self.assertIn('has_field("bank_balance")', source)

    def test_it_is_reached_from_the_fetch(self):
        source = inspect.getsource(client.fetch_all)
        self.assertIn("apply_running_balance", source)
        self.assertIn("from_date=kefiya_import.from_date", source)

    def test_repeating_a_fetch_writes_nothing(self):
        source = self._source()
        self.assertIn("if flt(row.bank_balance) != flt(running):", source)

    def test_the_field_exists_on_this_instance(self):
        self.assertTrue(
            frappe.get_meta("Bank Transaction").has_field("bank_balance"))


class TestPendingEntriesGoIntoTheForecast(unittest.TestCase):
    """A pending entry is a payment the bank accepted but has not booked --
    which is what the forecast is for, and why payment_kind already carries a
    "Pending" option.

    Storing them as Bank Transaction drafts instead would have been worse in
    two ways: match_on_bank_transaction fires on after_insert, so every draft
    would have deleted a forecast record it had not actually fulfilled; and
    this instance already carries 211,340 unrelated drafts, in which they would
    simply vanish.
    """

    def test_they_are_written_as_planned_payments(self):
        source = inspect.getsource(client.fetch_all)
        self.assertIn("normalize_pending_entries", source)
        self.assertIn("PENDING_KIND", source)

    def test_no_bank_transaction_drafts_are_created(self):
        from kefiya.utils import fetch_persistence as fp

        self.assertFalse(
            hasattr(fp, "replace_pending_transactions"),
            "The draft path is gone; leaving it as dead code is dangerous "
            "because it can delete Bank Transactions.",
        )

    def test_the_kind_exists_on_the_doctype(self):
        options = frappe.get_meta("Kefiya Planned Payment") \
            .get_field("payment_kind").options or ""
        self.assertIn(planned_payment.PENDING_KIND, options.split("\n"))

    def test_booking_the_entry_clears_the_forecast_by_itself(self):
        """That is the whole reason they live here: no separate matching."""
        source = inspect.getsource(planned_payment.match_on_bank_transaction)
        self.assertIn('"status": "Open"', source)
        self.assertNotIn(
            "payment_kind", source,
            "The matcher must not exclude Pending records -- clearing them on "
            "the real booking is the reconciliation.",
        )

    def test_direction_is_not_read_off_the_absolute_amount(self):
        """_coerce_amount deliberately returns abs(), so the sign is gone."""
        source = inspect.getsource(planned_payment._pending_direction)
        self.assertIn("CreditDebitIndicator", source)
        self.assertIn('token.startswith("D")', source)

    def test_the_two_sweeps_cannot_cancel_each_other(self):
        """refresh_planned_payments cancels whatever it was not told about."""
        source = inspect.getsource(client.fetch_all)
        self.assertEqual(
            source.count("payment_kinds="), 2,
            "Standing orders and pending entries are written in two separate "
            "calls; unscoped, each would cancel what the other just wrote.",
        )
        sweep = inspect.getsource(planned_payment.refresh_planned_payments)
        self.assertIn('sweep_filters["payment_kind"] = ("in"', sweep)


class TestPendingFetchIsSafe(unittest.TestCase):
    """The pending entries come from the same message as the booked ones."""

    def _source(self):
        return inspect.getsource(
            FinTSController.get_fints_pending_transactions)

    def test_it_uses_the_crash_safe_path(self):
        source = self._source()
        self.assertIn(
            "self._get_transactions_raw(", source,
            "The library's public get_transactions dies on the camt unpacking "
            "as soon as the bank answers with a TAN challenge.",
        )
        self.assertNotIn("self.fints_connection.get_transactions(", source)

    def test_a_tan_challenge_does_not_break_the_fetch(self):
        source = self._source()
        self.assertIn("NeedRetryResponse", source)
        self.assertIn(
            "return []", source,
            "The booked transactions are the point and already went through; "
            "a TAN request for the pending ones must not undo that.",
        )

    def test_include_pending_reaches_the_library(self):
        raw = inspect.getsource(FinTSController._get_transactions_raw)
        self.assertIn("include_pending=False", raw)
        self.assertIn(
            "if include_pending:", raw,
            "The camt path has to add the pending streams, not ignore them.",
        )
        self.assertIn(
            "streams += [x for x in (result[1] or []) if x]", raw,
            "And it has to filter them: a bank that sends no pending block "
            "yields [None], which is truthy, and the parser dies on it -- one "
            "missing optional field took out holdings and statements too.",
        )


class TestStatementsAreDownloaded(unittest.TestCase):
    """The list only says which statements exist; each has to be fetched."""

    def _source(self):
        return inspect.getsource(fetch_persistence.download_statements)

    def test_the_documents_themselves_are_fetched(self):
        source = self._source()
        self.assertIn("controller.get_fints_statement(", source)

    def test_it_does_not_download_the_same_one_twice(self):
        source = self._source()
        self.assertIn('frappe.db.exists("File"', source)

    def test_they_are_attached_to_the_bank_account(self):
        """A statement documents an account, not a credential record."""
        source = self._source()
        self.assertIn('"attached_to_doctype": "Bank Account"', source)
        self.assertNotIn('"attached_to_doctype": "Kefiya Login"', source)

    def test_the_field_names_match_the_fints_segment(self):
        """HIKAU calls them statement_number and year."""
        source = self._source()
        self.assertIn('"statement_number"', source)

    def test_the_extension_follows_the_real_format(self):
        """FinTS allows MT940 and ISO 8583 besides PDF."""
        source = inspect.getsource(fetch_persistence._statement_suffix)
        self.assertIn("statement_format", source)
        self.assertIn(".sta", source)
        self.assertIn('b"%PDF-"', source)

    def test_a_first_run_cannot_pull_years_of_pdfs(self):
        source = self._source()
        self.assertIn("limit", source)
        self.assertIn("entries[:limit]", source)


class TestCreditCardTransactionsAreBooked(unittest.TestCase):
    """A card charge moves money exactly like a transfer does."""

    def _source(self):
        return inspect.getsource(
            fetch_persistence.store_credit_card_transactions)

    def test_they_become_bank_transactions(self):
        source = self._source()
        self.assertIn('"doctype": "Bank Transaction"', source)
        self.assertIn('"docstatus": 1', source)

    def test_a_repeated_fetch_is_harmless(self):
        source = self._source()
        self.assertIn('frappe.db.exists("Bank Transaction"', source)
        self.assertIn("hashlib.md5", source)
