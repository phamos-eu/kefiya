# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The release prompt has to say which access is asking.

"Freigabe erforderlich -- TAN" was the whole message. In a collective fetch a
dozen accesses stop for a release one after the other and the box looked
identical every time, so the user had to guess which banking app to open -- and
a release given in the wrong app is one the waiting dialog never receives.
"""

import inspect
import unittest

import frappe

from kefiya.utils.fints_interactive import FinTSInteractive


class _Recorder:
    """Stands in for frappe.db.get_value / frappe.publish_realtime."""

    def __init__(self, values):
        self.values = values
        self.published = []

    def get_value(self, doctype, name, fields):
        return self.values.get(doctype)

    def publish_realtime(self, event, params, user=None):
        self.published.append((event, params))


class TanContextTestCase(unittest.TestCase):
    def _interactive(self, values, docname="Muster Betriebs-GmbH Genobank"):
        recorder = _Recorder(values)
        self._patched = [
            (frappe.db, "get_value", frappe.db.get_value),
            (frappe, "publish_realtime", frappe.publish_realtime),
        ]
        frappe.db.get_value = recorder.get_value
        frappe.publish_realtime = recorder.publish_realtime
        self.addCleanup(self._restore)
        return FinTSInteractive(
            {"docname": docname, "enabled": True}), recorder

    def _restore(self):
        for obj, attr, original in getattr(self, "_patched", []):
            setattr(obj, attr, original)


class TestThePromptNamesTheAccount(TanContextTestCase):

    def test_bank_and_account_travel_with_the_request(self):
        interactive, recorder = self._interactive({
            "Kefiya Login": ("Girokonto - Genobank eG",
                             "DE02120300000000202051"),
            "Bank Account": ("Genobank eG",
                             "Muster Betriebs-GmbH", None),
        })
        interactive.request_tan(possible_tan_modes=["pushTAN"])

        self.assertEqual(len(recorder.published), 1)
        event, params = recorder.published[0]
        self.assertEqual(event, "fints_tan_interaction_required")
        self.assertEqual(params["bank"], "Genobank eG")
        self.assertEqual(params["account_name"],
                         "Muster Betriebs-GmbH")
        self.assertIn("Genobank eG", params["account_label"])
        self.assertIn("Muster Betriebs-GmbH",
                      params["account_label"])

    def test_the_iban_is_masked_to_its_last_four_digits(self):
        """An account number belongs in no dialog that may be on a shared
        screen. Four digits tell two accounts of one company apart."""
        interactive, recorder = self._interactive({
            "Kefiya Login": ("Girokonto - Genobank eG",
                             "DE02120300000000202051"),
            "Bank Account": ("Genobank eG", "Betrieb", None),
        })
        interactive.request_tan(possible_tan_modes=["pushTAN"])

        _, params = recorder.published[0]
        self.assertEqual(params["iban"], "DE***2051")
        self.assertNotIn("120300", params["account_detail"])
        self.assertNotIn("DE02120300000000202051", str(params))

    def test_an_access_without_a_bank_account_still_names_itself(self):
        """The login's own name already identifies the access; a prompt with
        no heading at all is what this change exists to remove."""
        interactive, recorder = self._interactive(
            {"Kefiya Login": (None, None)}, docname="Muster Beteiligungs-GmbH Genobank")
        interactive.request_tan(possible_tan_modes=["pushTAN"])

        _, params = recorder.published[0]
        self.assertEqual(params["account_label"],
                         "Muster Beteiligungs-GmbH Genobank")

    def test_a_failed_lookup_does_not_cost_the_release(self):
        """A prompt without its heading is worse than the old one. A prompt
        that never appears because the heading could not be resolved would
        strand the fetch entirely."""
        interactive, recorder = self._interactive({})

        def explode(*args, **kwargs):
            raise Exception("Bank Account is gone")

        frappe.db.get_value = explode
        interactive.request_tan(possible_tan_modes=["pushTAN"])

        self.assertEqual(len(recorder.published), 1)
        _, params = recorder.published[0]
        self.assertTrue(params["tan_required"])
        self.assertEqual(params["docname"],
                         "Muster Betriebs-GmbH Genobank")

    def test_the_lookup_happens_once_per_run(self):
        """request_tan_prompt fires up to three times for one release -- mode,
        medium, TAN. The heading must not cost three round trips."""
        interactive, recorder = self._interactive({
            "Kefiya Login": ("Girokonto - Genobank eG", "DE02120300000000202051"),
            "Bank Account": ("Genobank eG", "Betrieb", None),
        })

        calls = []
        original = frappe.db.get_value

        def counted(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        frappe.db.get_value = counted
        interactive.request_tan_mechanism(["pushTAN"])
        interactive.request_tan_mechanism(["pushTAN"], ["Handy"])
        interactive.request_tan(["pushTAN"], ["Handy"])

        self.assertEqual(len(recorder.published), 3)
        self.assertLessEqual(
            len(calls), 2,
            "One login lookup and one bank account lookup, not one pair per "
            "prompt.")

    def test_nothing_is_published_when_the_run_is_not_interactive(self):
        """The scheduled run has nobody to ask, and must not pay for a heading
        it never shows."""
        interactive = FinTSInteractive({"docname": "x", "enabled": False})
        calls = []
        original = frappe.db.get_value
        frappe.db.get_value = lambda *a, **k: calls.append(a)
        try:
            interactive.request_tan(possible_tan_modes=["pushTAN"])
        finally:
            frappe.db.get_value = original
        self.assertEqual(calls, [])


class TestEveryPromptShowsIt(unittest.TestCase):
    """Three prompts read this event. Two live here; the third is the
    outgoing-payments block on the site that embeds this app."""

    def _js(self, name):
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "public", "js", "controllers", name)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_the_dialog_title_carries_the_access(self):
        for name in ("tan_prompt.js", "fints_interactive.js"):
            source = self._js(name)
            self.assertIn(
                "account_label", source,
                "{0} still opens a nameless dialog.".format(name))

    def test_the_context_is_prepended_after_the_fields_are_built(self):
        """Both prompts address their fields by index -- fields[0] is the TAN
        mode, fields[1] the medium. An entry inserted before they are built
        would make the wrong field read-only."""
        for name in ("tan_prompt.js", "fints_interactive.js"):
            source = self._js(name)
            self.assertIn(
                "unshift", source,
                "{0} must prepend the context, not push it first.".format(name))
            self.assertLess(
                source.index("fields[0]"), source.index("unshift"),
                "{0} indexes fields before the context is prepended; "
                "prepending earlier shifts every index by one.".format(name))

    def test_the_account_is_escaped_before_it_reaches_the_dom(self):
        """An account name is user-supplied text going into innerHTML."""
        for name in ("tan_prompt.js", "fints_interactive.js"):
            source = self._js(name)
            self.assertIn("frappe.utils.escape_html", source, name)


class TestThePayloadIsBuiltOnce(unittest.TestCase):

    def test_request_tan_prompt_is_the_only_publisher(self):
        source = inspect.getsource(FinTSInteractive)
        self.assertEqual(
            source.count('publish_realtime("fints_tan_interaction_required"'), 1,
            "A second publisher would be a second prompt shape to keep in "
            "step.")
        self.assertIn("params.update(self.account_context())", source)
