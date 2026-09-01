# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Fetching statements from a service that holds a connection we cannot.

A card issuer without FinTS leaves a specific gap: the PSD2 account
information route carries bookings and balances and no documents at all, so an
account can be current on its transactions and still have not one statement
filed against it. A document service closes that half.

What this module must get right is less about fetching than about what it does
NOT do: never publish the API key, never file the same statement twice, and
never write on a run nobody asked to write.
"""

import inspect
import unittest

from kefiya.utils import document_service as ds


class TestTheKeyStaysWhereItBelongs(unittest.TestCase):
    """The API key is stored encrypted in a Password field. Everything here
    exists to keep it from leaking back out the side."""

    def test_the_key_is_read_through_the_encrypted_field(self):
        source = inspect.getsource(ds._credentials)
        self.assertIn("get_password(", source,
                      "Reading it as a plain field would return the cipher "
                      "text, and storing it as one would defeat the point.")
        self.assertEqual(source.count("get_password("), 2,
                         "One read per source -- inline and external.")

    def test_the_key_is_not_held_on_the_settings_document_in_between(self):
        """It is fetched at the moment of the call and passed straight into
        the body, so there is no attribute anyone can print later."""
        source = inspect.getsource(ds._call)
        self.assertIn('body["api_key"] = key', source)
        self.assertNotIn("self.api_key", source)

    def test_a_failed_call_does_not_carry_the_request_body(self):
        """requests puts the body on some exceptions. The body is where the
        key is, and a traceback lands in the Error Log, which far more people
        may read than may read the settings document."""
        source = inspect.getsource(ds._call)
        self.assertIn("from None", source,
                      "Chaining the original exception would carry it along.")

        # Comments stripped: the point is what the handler DOES, and the
        # comment above it necessarily discusses the body it must not pass on.
        handler = "\n".join(
            line for line in source.split("except Exception")[1].splitlines()
            if not line.strip().startswith("#"))
        self.assertNotIn("body", handler,
                         "Nothing from the body may reach the raised error.")
        self.assertNotIn("exc)", handler.replace("type(exc).__name__", ""),
                         "str(exc) of a requests error can quote the request.")

    def test_no_code_path_logs_the_payload(self):
        source = inspect.getsource(ds)
        for marker in ("log_error(title=", "msgprint("):
            for chunk in source.split(marker)[1:]:
                head = chunk[:300]
                self.assertNotIn("api_key", head)
                self.assertNotIn("payload", head)
                self.assertNotIn("body", head)

    def test_the_summary_never_contains_the_key(self):
        source = inspect.getsource(ds.fetch_statements)
        self.assertNotIn("api_key", source)


class TestTheKeyMayLiveWhereTheInstanceKeepsItsKeys(unittest.TestCase):
    """An instance that already has a place for API credentials should not
    grow a second one. A key kept in two places is a key that gets rotated in
    one of them, and the failure then looks like the service is down."""

    def test_this_app_names_nobodys_particular_setup(self):
        """Where the credentials live is a fact about the instance and
        belongs in its configuration, not in this app."""
        source = inspect.getsource(ds).lower()
        for name in ("axessio", "propms"):
            self.assertNotIn(name, source)

    def test_an_external_doctype_can_supply_the_key(self):
        source = inspect.getsource(ds._credentials)
        for part in ("credential_doctype", "credential_child_table",
                     "credential_match_field", "credential_token_field"):
            self.assertIn(part, source)

    def test_a_single_and_a_named_document_are_both_reachable(self):
        source = inspect.getsource(ds._credentials)
        self.assertIn("meta.issingle", source)
        self.assertIn("frappe.get_single(doc.credential_doctype)", source)
        self.assertIn("credential_docname", source)

    def test_a_missing_row_is_named_not_silently_empty(self):
        """An empty key produces an authentication error at the service,
        which reads like the key is wrong rather than absent."""
        source = inspect.getsource(ds._credentials)
        self.assertIn("if holder is None:", source)
        self.assertIn("frappe.throw", source)

    def test_a_switched_off_credential_row_is_not_used(self):
        source = inspect.getsource(ds._credentials)
        self.assertIn('not holder.get("enabled")', source)

    def test_an_absent_key_stops_the_run_before_the_call(self):
        source = inspect.getsource(ds._settings)
        self.assertIn("if not key:", source)
        self.assertLess(source.index("if not key:"), source.index("return doc"))

    def test_the_endpoint_may_come_from_the_same_row(self):
        """Endpoint and key belong together; splitting them across two places
        is how a key ends up sent to the wrong host."""
        source = inspect.getsource(ds._credentials)
        self.assertIn("credential_url_field", source)


class TestPlainHttpIsRefused(unittest.TestCase):
    """The key travels in the body of every single call."""

    def test_the_endpoint_must_be_https(self):
        source = inspect.getsource(ds._settings)
        self.assertIn('startswith("https://")', source)
        self.assertIn("frappe.throw", source)


class TestNothingIsWrittenUnasked(unittest.TestCase):

    def test_a_run_is_a_dry_run_unless_told_otherwise(self):
        self.assertEqual(
            inspect.signature(ds.fetch_statements).parameters["dry_run"].default,
            1)

    def test_the_scheduled_run_does_nothing_while_switched_off(self):
        source = inspect.getsource(ds.fetch_statements_scheduled)
        self.assertIn("if not doc.enabled:", source)
        self.assertIn("return", source)

    def test_every_entry_point_is_restricted(self):
        for fn in (ds.test_connection, ds.probe, ds.fetch_statements):
            self.assertIn('frappe.only_for("System Manager")',
                          inspect.getsource(fn), fn.__name__)

    def test_a_run_is_bounded(self):
        """Each document is a separate download. A first run must work its
        backlog off over several runs, not in one request."""
        source = inspect.getsource(ds.fetch_statements)
        self.assertIn("max_documents_per_run", source)
        self.assertIn("budget <= 0", source)


class TestTheSameStatementIsNotFiledTwice(unittest.TestCase):

    def test_the_name_is_deterministic(self):
        entry = {"prim_uid": 4711, "document_date": "2026-08-01",
                 "file_name": "Abrechnung.pdf"}
        self.assertEqual(ds._statement_name(entry, 0),
                         ds._statement_name(dict(entry), 0))

    def test_the_name_survives_a_reordered_list(self):
        """A file downloaded a week later has the same statements at other
        positions, so the position must not be part of the identity."""
        entry = {"prim_uid": 4711, "document_date": "2026-08-01",
                 "file_name": "Abrechnung.pdf"}
        self.assertEqual(ds._statement_name(entry, 0),
                         ds._statement_name(entry, 7))

    def test_two_statements_get_two_names(self):
        a = {"prim_uid": 1, "document_date": "2026-07-01", "file_name": "A.pdf"}
        b = {"prim_uid": 2, "document_date": "2026-08-01", "file_name": "B.pdf"}
        self.assertNotEqual(ds._statement_name(a, 0), ds._statement_name(b, 0))

    def test_a_document_without_a_name_still_gets_one(self):
        self.assertTrue(ds._statement_name({}, 3))

    def test_an_extension_is_added_only_when_missing(self):
        named = ds._statement_name({"file_name": "Auszug.pdf"}, 0)
        self.assertTrue(named.endswith(".pdf"))
        self.assertNotIn(".pdf.pdf", named)

    def test_the_existing_file_is_checked_before_it_is_downloaded(self):
        """Each download is a request. Fetching a statement in order to
        discover it was already filed pays for it twice."""
        source = inspect.getsource(ds.fetch_statements)
        self.assertLess(
            source.index('frappe.db.exists("File"'),
            source.index("_download_and_attach("),
            "The presence check must come before the download.")


class TestTheResponseIsNotAssumed(unittest.TestCase):
    """The published client for this service documents the REQUEST shape
    only. Guessing the response field names in code and failing silently on
    the first real run is what probe() exists to prevent."""

    def test_a_wrapped_list_is_found_whatever_it_is_called(self):
        for key in ("documents", "data", "result", "items"):
            self.assertEqual(ds._documents_in({key: [{"a": 1}]}), [{"a": 1}])

    def test_a_bare_list_is_accepted(self):
        self.assertEqual(ds._documents_in([{"a": 1}]), [{"a": 1}])

    def test_a_response_with_no_list_says_so_instead_of_reading_as_empty(self):
        """"No documents" and "response not understood" must not look the
        same -- one is a quiet day, the other is a broken integration."""
        self.assertIsNone(ds._documents_in({"status": "ok"}))
        self.assertIsNone(ds._documents_in("nonsense"))

    def test_an_empty_list_is_empty_not_unrecognised(self):
        self.assertEqual(ds._documents_in({"documents": []}), [])

    def test_the_identifier_is_looked_for_under_several_names(self):
        for key in ("prim_uid", "document_prim_uid", "id"):
            self.assertEqual(ds._pick({key: 42}, ds._ID_KEYS), 42)

    def test_probe_reports_names_and_never_values(self):
        source = inspect.getsource(ds.probe)
        self.assertIn("type(v).__name__", source,
                      "The types are the point; the values must stay on the "
                      "site.")
        self.assertNotIn("entry.values()", source)


class TestTheDocumentItselfIsHandledDefensively(unittest.TestCase):

    def test_a_data_uri_is_reduced_to_its_payload(self):
        source = inspect.getsource(ds._download_and_attach)
        self.assertIn('startswith("data:")', source)

    def test_a_document_without_content_is_reported_not_stored_empty(self):
        source = inspect.getsource(ds._download_and_attach)
        self.assertIn("if payload is None:", source)
        self.assertIn("return False", source)

    def test_it_files_through_the_shared_helper(self):
        """So a statement from this route lands exactly where a FinTS-fetched
        one lands: private file on the Bank Account."""
        source = inspect.getsource(ds._download_and_attach)
        self.assertIn("statement_import.attach_statement(", source)
