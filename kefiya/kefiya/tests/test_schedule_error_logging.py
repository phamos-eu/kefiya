# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import inspect
import re
import unittest
from pathlib import Path

import frappe

from kefiya.kefiya.doctype.kefiya_schedule import kefiya_schedule
from kefiya.utils.fints_controller import _mask_iban


class TestScheduleErrorLogging(unittest.TestCase):
    """Regression tests for the scheduler outage of 2026-07.

    ``frappe.log_error(text)`` passes its single positional argument as the
    *title*, which is the Error Log ``method`` field -- a Data column capped at
    140 characters. Logging a full traceback that way raised
    ``CharacterLengthExceededError`` from inside the per-login except block, so
    one broken login aborted the whole scheduler tick instead of just its own
    iteration.
    """

    def test_log_import_failure_never_raises(self):
        """The error path must survive its own logger blowing up.

        This is the actual defect: the except block could not tolerate a
        failing ``log_error``, so the batch died instead of continuing.
        """
        original = frappe.log_error

        def exploding_log_error(*args, **kwargs):
            raise frappe.CharacterLengthExceededError("boom")

        frappe.log_error = exploding_log_error
        try:
            # Must return normally rather than propagate.
            kefiya_schedule._log_import_failure("Some Login")
        finally:
            frappe.log_error = original

    def test_log_import_failure_title_fits_error_log_field(self):
        """A very long login name must not overflow the title field."""
        captured = {}
        original = frappe.log_error

        def capturing_log_error(*args, **kwargs):
            captured.update(kwargs)
            captured["positional"] = args

        frappe.log_error = capturing_log_error
        try:
            # Inside a real except block, because that is the only place
            # _log_import_failure is ever called from -- and the only place
            # frappe.get_traceback() returns anything. Outside one it returns
            # an empty string, which made the traceback assertion below
            # unsatisfiable and this test fail on every run.
            try:
                raise ValueError("simulated import failure")
            except ValueError:
                kefiya_schedule._log_import_failure("L" * 500)
        finally:
            frappe.log_error = original

        self.assertLessEqual(
            len(captured["title"]), 140,
            "Error Log title (fieldname 'method') is a Data column capped at "
            "140 characters; a longer value raises inside the except block.",
        )
        self.assertEqual(
            captured["positional"], (),
            "log_error must be called with keyword arguments only -- a lone "
            "positional argument is taken as the title, not the message.",
        )
        self.assertIn(
            "Traceback", captured["message"],
            "The traceback belongs in `message`, never in `title`.",
        )

    def test_no_positional_log_error_in_app(self):
        """Guard the whole bug class, not just the site that caused the outage.

        ``frappe.log_error(frappe.get_traceback())`` is always wrong: it files
        the traceback as the title. Fail if the pattern reappears anywhere.
        """
        app_root = Path(inspect.getfile(kefiya_schedule)).parents[3]
        offenders = []
        for path in app_root.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "log_error(frappe.get_traceback())" in line:
                    offenders.append("{0}:{1}".format(path, number))
        self.assertEqual(
            offenders, [],
            "Use frappe.log_error(title=..., message=frappe.get_traceback()); "
            "found positional calls at: {0}".format(", ".join(offenders)),
        )


class TestFailedLoginRecovery(unittest.TestCase):
    """The per-login recovery path must isolate one login from the next."""

    def test_recovery_rolls_back_then_logs_then_commits(self):
        """Order matters: a rollback after logging drops the Error Log."""
        order = []
        originals = (frappe.db.rollback, frappe.db.commit, frappe.log_error)

        frappe.db.rollback = lambda: order.append("rollback")
        frappe.db.commit = lambda: order.append("commit")
        frappe.log_error = lambda **kw: order.append("log")
        try:
            kefiya_schedule._recover_from_failed_login("Some Login")
        finally:
            frappe.db.rollback, frappe.db.commit, frappe.log_error = originals

        self.assertEqual(
            order, ["rollback", "log", "commit"],
            "Must roll back the failed login's partial work first, then log, "
            "then commit so the Error Log survives the rollback.",
        )

    def test_recovery_survives_failing_commit_and_rollback(self):
        """A broken transaction must not abort the tick.

        commit() and rollback() can fail in their own right; an exception
        escaping here would end the batch -- the very failure this code exists
        to prevent.
        """
        def boom(*args, **kwargs):
            raise RuntimeError("transaction is gone")

        originals = (frappe.db.rollback, frappe.db.commit, frappe.log_error)
        frappe.db.rollback = boom
        frappe.db.commit = boom
        frappe.log_error = boom
        try:
            kefiya_schedule._recover_from_failed_login("Some Login")
        finally:
            frappe.db.rollback, frappe.db.commit, frappe.log_error = originals


class TestSchedulerPermissionGate(unittest.TestCase):
    def test_whitelisted_entrypoint_checks_permission(self):
        """`scheduled_import_fints_payments` is callable by any logged-in user.

        With ``manual=1`` it also bypasses the frequency gate, so without a
        permission check anyone could trigger a bank fetch for every configured
        login at will.
        """
        source = inspect.getsource(
            kefiya_schedule.scheduled_import_fints_payments
        )
        self.assertIn(
            "has_permission", source,
            "The whitelisted scheduler entrypoint must gate on an explicit "
            "frappe.has_permission(...) check.",
        )


class TestRequireFintsAccount(unittest.TestCase):
    """Every FinTS operation must resolve its account through the guard.

    python-fints dereferences ``account.iban`` unguarded, so an unresolved
    account surfaces as a bare ``AttributeError`` naming no login. That applies
    to balance, holdings, statements, credit card and -- critically -- the
    money-moving transfer and debit paths.
    """

    def test_no_raw_account_lookup_reaches_the_fints_library(self):
        from kefiya.utils import fints_controller

        source = Path(inspect.getfile(fints_controller)).read_text(
            encoding="utf-8"
        )
        offenders = [
            number
            for number, line in enumerate(source.splitlines(), start=1)
            if "= self.get_fints_account_by_iban(" in line
            and "_require_fints_account" not in line
        ]
        # The single legitimate use is inside _require_fints_account itself.
        self.assertLessEqual(
            len(offenders), 1,
            "Resolve accounts via self._require_fints_account(); a raw "
            "get_fints_account_by_iban() can return None and reach "
            "python-fints unguarded. Offending lines: {0}".format(offenders),
        )

    def test_require_fints_account_does_not_recurse(self):
        """The guard must call the low-level lookup, not itself."""
        from kefiya.utils import fints_controller

        source = inspect.getsource(
            fints_controller.FinTSController._require_fints_account
        )
        self.assertIn(
            "self.get_fints_account_by_iban(", source,
            "_require_fints_account must delegate to the low-level lookup; "
            "calling itself would recurse until the stack blows.",
        )


class TestTranslationHelperIsImported(unittest.TestCase):
    """`_` is not a builtin in Frappe -- it must be imported per module.

    Two modules used _() without importing it. Both stayed latent because the
    affected paths are error branches that had not run in production, so the
    NameError would first have surfaced while reporting another failure.
    """

    def test_every_module_using_underscore_imports_it(self):
        app_root = Path(inspect.getfile(kefiya_schedule)).parents[3]
        pattern = re.compile(r"(?<![\w.])_\(")
        offenders = []

        for path in sorted(app_root.rglob("*.py")):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8")
            body = "\n".join(
                line for line in text.splitlines()
                if not line.lstrip().startswith("#")
            )
            if not pattern.search(body):
                continue
            imports_underscore = re.search(
                r"^\s*from frappe import (.*\b_\b.*)$", text, re.M
            ) or re.search(r"^\s*from frappe import _\s*$", text, re.M)
            if not imports_underscore:
                offenders.append(str(path.relative_to(app_root)))

        self.assertEqual(
            offenders, [],
            "These modules call _() without importing it; the call raises "
            "NameError at runtime: {0}".format(", ".join(offenders)),
        )


class TestMaskIban(unittest.TestCase):
    def test_mask_iban_edge_cases(self):
        """Diagnostic output must never raise, whatever the field holds."""
        self.assertEqual(_mask_iban("DE89370400440532013000"), "DE***3000")
        self.assertEqual(_mask_iban(None), "<no IBAN>")
        self.assertEqual(_mask_iban(""), "<no IBAN>")
        # Too short to mask meaningfully -- returned as-is, still no crash.
        self.assertEqual(_mask_iban("ABCDEF"), "ABCDEF")
        self.assertEqual(_mask_iban("ABCDEFG"), "AB***DEFG")
        # Non-string input must be coerced rather than blow up.
        self.assertEqual(_mask_iban(12345678), "12***5678")

    def test_mask_iban_hides_the_account_number(self):
        """The point of masking: the full number must not reach the log."""
        iban = "DE89370400440532013000"
        self.assertNotIn(iban, _mask_iban(iban))
        self.assertNotIn("37040044", _mask_iban(iban))
