# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import inspect
import unittest

import frappe
from frappe.model.base_document import get_controller


class TestFinTSCompat(unittest.TestCase):
    def test_bank_transaction_controller_has_get_rounded(self):
        """Regression for the statement-import outage.

        When the ALYF ``banking`` app is installed it registers a
        ``before_submit`` doc-event that calls ``doc.get_rounded(...)`` -- a
        method defined on banking's Bank Transaction subclass. Kefiya's override
        wins as the resolved controller, so it MUST inherit that method (by
        extending banking's class) or every ``bank_transaction.insert()`` fails
        with ``AttributeError`` and no transactions are imported.
        """
        if "banking" not in frappe.get_installed_apps():
            self.skipTest("banking app not installed")
        controller = get_controller("Bank Transaction")
        self.assertTrue(
            hasattr(controller, "get_rounded"),
            "Bank Transaction controller must expose get_rounded when the "
            "banking app is installed (kefiya/overrides/bank_transaction).",
        )

    def test_to_jsonable_stringifies_unknown_objects(self):
        """The FinTS fetch wrappers serialise varied library objects best-effort;
        unknown objects must degrade to a string instead of breaking JSON."""
        from kefiya.utils.fints_controller import _to_jsonable

        class _Weird:
            def __repr__(self):
                return "weird-object"

        out = _to_jsonable({"num": 3, "obj": _Weird(), "nested": [_Weird()]})
        self.assertEqual(out["num"], 3)
        self.assertEqual(out["obj"], "weird-object")
        self.assertEqual(out["nested"], ["weird-object"])


class TestMoneyEndpointPermissions(unittest.TestCase):
    """Regression for the review finding "payments can be changed by anyone
    without a permission check".

    Every whitelisted endpoint that moves money or mutates a payment/booking
    MUST perform an explicit ``frappe.has_permission(..., throw=True)`` gate,
    because ``@frappe.whitelist()`` alone makes a method callable by *any*
    logged-in user regardless of DocType permissions.
    """

    # endpoint -> DocType it must guard
    GUARDED_ENDPOINTS = {
        "submit_payment_request_via_fints": "Payment Request",
        "send_transfer_tan": "Kefiya Login",
        "create_payment_entry": "Bank Transaction",
        "add_payment_reference": "Payment Entry",
        "auto_assign_payments": "Payment Entry",
        "change_match_against": "Kefiya Settings",
    }

    def test_endpoints_are_whitelisted(self):
        from kefiya.utils import client

        for name in self.GUARDED_ENDPOINTS:
            fn = getattr(client, name)
            self.assertTrue(
                getattr(fn, "__func__", fn) in frappe.whitelisted
                or fn in frappe.whitelisted,
                f"{name} is expected to be a whitelisted endpoint",
            )

    def test_money_endpoints_have_permission_gate(self):
        from kefiya.utils import client

        for name, doctype in self.GUARDED_ENDPOINTS.items():
            src = inspect.getsource(getattr(client, name))
            self.assertIn(
                "has_permission",
                src,
                f"{name} must call frappe.has_permission before acting",
            )
            self.assertIn(
                "throw=True",
                src,
                f"{name}'s permission check must throw (deny) on failure",
            )
            self.assertIn(
                doctype,
                src,
                f"{name} must guard the {doctype} DocType",
            )

    def test_transfer_needs_explicit_confirmation(self):
        """The money-out endpoint must refuse to send before the confirmation
        gate (and before any permission/DB access), so a stray call can never
        move money by default."""
        from kefiya.utils import client

        res = client.submit_payment_request_via_fints(
            payment_request_name="does-not-matter",
            user_scope="does-not-matter",
            confirmed=0,
        )
        self.assertEqual(res.get("status"), "error")
        self.assertIn("confirm", (res.get("message") or "").lower())


class TestSepaExportGate(unittest.TestCase):
    """Regression: the SEPA pain.001 export must not bypass the
    outgoing-payment approval workflow.

    ``export_request`` / ``send_sepa_xml_via_email`` are ``@frappe.whitelist()``
    endpoints, so without an explicit gate any logged-in user could call them on
    an *unapproved draft* Outward Payment Request and receive a ready-to-execute
    SEPA file -- defeating the 4-eyes "Payment Request Freigabe (Ausgang)"
    workflow that only gates the submit. So the export path must (a) require
    submit permission and (b) refuse anything that is not a submitted request.
    """

    def test_export_endpoints_are_whitelisted(self):
        from kefiya.events.hammer_script import payment_request_on_submit as m

        for name in ("export_request", "send_sepa_xml_via_email"):
            fn = getattr(m, name)
            self.assertTrue(
                getattr(fn, "__func__", fn) in frappe.whitelisted
                or fn in frappe.whitelisted,
                f"{name} is expected to be a whitelisted endpoint",
            )

    def test_export_endpoints_have_permission_gate(self):
        from kefiya.events.hammer_script import payment_request_on_submit as m

        for name in ("export_request", "send_sepa_xml_via_email"):
            src = inspect.getsource(getattr(m, name))
            self.assertIn(
                "has_permission",
                src,
                f"{name} must call frappe.has_permission before acting",
            )
            self.assertIn(
                "throw=True",
                src,
                f"{name}'s permission check must throw (deny) on failure",
            )
            self.assertIn(
                "Payment Request",
                src,
                f"{name} must guard the Payment Request DocType",
            )

    def test_builder_gates_on_submitted_state(self):
        """The pain.001 builder must refuse a non-submitted (unapproved) doc, so
        the approval workflow that gates the submit cannot be side-stepped."""
        from kefiya.events.hammer_script import payment_request_on_submit as m

        src = inspect.getsource(m._build_sepa_xml)
        self.assertIn(
            "docstatus",
            src,
            "_build_sepa_xml must check the approval (docstatus) state",
        )

    def test_builder_validates_against_xsd(self):
        """The pain.001 must be validated against the SEPA XSD before
        it leaves the system, so a malformed instruction is blocked rather than
        sent to the bank."""
        from kefiya.events.hammer_script import payment_request_on_submit as m

        src = inspect.getsource(m._build_sepa_xml)
        self.assertIn(
            "validate=True",
            src,
            "_build_sepa_xml must export with XSD validation enabled",
        )
        self.assertNotIn(
            "validate=False",
            src,
            "_build_sepa_xml must not export without validation",
        )


class TestScheduledDebitNormalizer(unittest.TestCase):
    """The forecast table (Kefiya Planned Payment) is fed from the bank's
    standing orders via normalize_scheduled_debits. The bank/library shape is
    variable, so the normalizer must extract dated+amounted items and skip
    anything it cannot parse rather than insert a guessed row."""

    def test_parses_common_shape_and_skips_incomplete(self):
        from kefiya.utils.planned_payment import normalize_scheduled_debits

        raw = [
            {  # a well-formed one-off scheduled transfer
                "execution_date": "2999-01-15",
                "amount": -750.0,
                "recipient_name": "Landlord GmbH",
                "recipient_iban": "DE02120300000000202051",
                "purpose": "Rent",
            },
            {  # nested amount object + no frequency -> single item
                "date": "2999-02-01",
                "amount": {"amount": 42.5, "currency": "EUR"},
                "creditor_name": "Insurance",
            },
            {"recipient_name": "no date, no amount"},  # -> skipped
            "not-a-dict",  # -> skipped
        ]
        out = normalize_scheduled_debits(raw)
        self.assertEqual(out["skipped"], 2)
        self.assertEqual(len(out["items"]), 2)
        first = out["items"][0]
        # amount is always stored positive; direction defaults to Outgoing
        self.assertEqual(first["amount"], 750.0)
        self.assertEqual(first["direction"], "Outgoing")
        self.assertEqual(first["counterparty_name"], "Landlord GmbH")

    def test_empty_input_is_safe(self):
        from kefiya.utils.planned_payment import normalize_scheduled_debits

        out = normalize_scheduled_debits(None)
        self.assertEqual(out, {"items": [], "skipped": 0})


class TestSepaEmailIsServerControlled(unittest.TestCase):
    """The mail endpoint must not relay caller-supplied content.

    ``recipient_email`` and ``xml_content`` used to be parameters, so a user
    holding submit rights on any Payment Request could have arbitrary content
    mailed to an arbitrary address through the server -- and the approval and
    XSD gates in ``_build_sepa_xml`` never applied to what the client passed.
    """

    def test_endpoint_takes_no_recipient_or_payload(self):
        import inspect

        from kefiya.events.hammer_script.payment_request_on_submit import (
            send_sepa_xml_via_email,
        )

        params = list(
            inspect.signature(send_sepa_xml_via_email).parameters
        )
        self.assertEqual(
            params, ["payment_request_name"],
            "The recipient and the attachment must be resolved server-side; "
            "accepting them as arguments makes this an open relay for anyone "
            "with submit rights.",
        )

    def test_endpoint_builds_the_file_and_resolves_the_recipient(self):
        import inspect

        from kefiya.events.hammer_script.payment_request_on_submit import (
            send_sepa_xml_via_email,
        )

        source = inspect.getsource(send_sepa_xml_via_email)
        self.assertIn(
            "_build_sepa_xml(", source,
            "The mailed file must go through _build_sepa_xml so the approval "
            "and XSD gates apply to it as well.",
        )
        self.assertIn(
            "recipient_email", source,
            "The recipient must come from Kefiya Settings.",
        )
        self.assertIn(
            "has_permission", source,
            "Mailing an executable payment file requires submit rights on "
            "the specific Payment Request.",
        )
