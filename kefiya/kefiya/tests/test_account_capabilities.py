# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What the bank allows on an account -- read, stored, and asked about.

The three rules the module exists to keep are the three things worth testing:
the bank is the source, silence is neither permission nor refusal, and the
refusal happens before the TAN rather than after it.
"""

import unittest

from kefiya.utils import account_capabilities as caps


class _Allowed:
    """One entry of HIUPD's allowed_transactions, as python-fints yields it."""

    def __init__(self, transaction, required_signatures=1, limit_type=None,
                 limit_amount=None, limit_days=None):
        self.transaction = transaction
        self.required_signatures = required_signatures
        self.limit_type = limit_type
        self.limit_amount = _Amount(limit_amount) if limit_amount else None
        self.limit_days = limit_days


class _Amount:
    def __init__(self, amount, currency="EUR"):
        self.amount = amount
        self.currency = currency


class _AccountInfo:
    def __init__(self, account_number, subaccount_number=None):
        self.account_number = account_number
        self.subaccount_number = subaccount_number


class _HIUPD:
    def __init__(self, iban, allowed, account_number="123456"):
        self.iban = iban
        self.allowed_transactions = allowed
        self.account_information = _AccountInfo(account_number)


class _UPD:
    def __init__(self, segments):
        self.segments = segments

    def find_segments(self, name):
        return list(self.segments) if name == "HIUPD" else []


class _Connection:
    def __init__(self, upd=None):
        self.upd = upd


class TestReadingItFromTheBank(unittest.TestCase):

    def test_every_account_the_bank_names_comes_back(self):
        conn = _Connection(_UPD([
            _HIUPD("DE02120300000000202051", [_Allowed("HKCCS")]),
            _HIUPD("DE02100500000054540402", [_Allowed("HKSAL")]),
        ]))
        rows = caps.read_from_connection(conn)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["iban"], "DE02120300000000202051")
        self.assertIn("HKCCS", rows[0]["segments"])
        self.assertIn("HKSAL", rows[1]["segments"])

    def test_the_account_is_not_filtered_to_the_login(self):
        """A login covers several accounts; the answer is about the account."""
        conn = _Connection(_UPD([
            _HIUPD("DE02120300000000202051", [_Allowed("HKCCS")]),
            _HIUPD("DE02100500000054540402", []),
        ]))
        self.assertEqual(len(caps.read_from_connection(conn)), 2)

    def test_a_version_behind_the_code_is_the_same_transaction(self):
        conn = _Connection(_UPD([
            _HIUPD("DE02120300000000202051", [_Allowed("HKCCS1")]),
        ]))
        rows = caps.read_from_connection(conn)
        self.assertIn("HKCCS", rows[0]["segments"])

    def test_signatures_and_limits_ride_along(self):
        conn = _Connection(_UPD([
            _HIUPD("DE02120300000000202051", [
                _Allowed("HKCCS", required_signatures=2, limit_type="T",
                         limit_amount=5000, limit_days=1),
            ]),
        ]))
        detail = caps.read_from_connection(conn)[0]["segments"]["HKCCS"]
        self.assertEqual(detail["required_signatures"], 2)
        self.assertEqual(detail["limit_type"], "T")
        self.assertEqual(detail["limit_amount"], 5000)

    def test_no_upd_is_no_answer_rather_than_an_empty_one(self):
        self.assertEqual(caps.read_from_connection(_Connection(None)), [])
        self.assertEqual(caps.read_from_connection(_Connection(_UPD([]))), [])


class TestSilenceIsNotRefusal(unittest.TestCase):
    """The distinction the whole module turns on."""

    def test_an_account_with_no_stored_list_is_unknown(self):
        import inspect

        source = inspect.getsource(caps.verdict)
        # No rows at all -> UNKNOWN, and it must be the first thing decided.
        self.assertIn("if not rows:", source)
        self.assertIn("return UNKNOWN", source)

    def test_unknown_passes_and_only_refused_stops(self):
        import inspect

        source = inspect.getsource(caps.supports)
        self.assertIn("!= REFUSED", source)
        self.assertNotIn("== ALLOWED", source)

    def test_a_capability_missing_from_an_older_list_is_unknown(self):
        """A list written before this app knew the transaction says nothing
        about it -- concluding "refused" would hide a button over our own
        catalogue having grown."""
        import inspect

        source = inspect.getsource(caps.verdict)
        self.assertIn("return UNKNOWN", source.split("for row in rows")[1])


class TestWhichTransactionAnOrderNeeds(unittest.TestCase):

    def test_one_payment_now(self):
        self.assertEqual(caps.required_capability(1), "transfer")

    def test_several_payments_are_a_collective_order(self):
        self.assertEqual(
            caps.required_capability(3), "transfer_collective")

    def test_a_date_the_bank_holds_is_its_own_transaction(self):
        self.assertEqual(
            caps.required_capability(1, scheduled=True), "scheduled_transfer")
        self.assertEqual(
            caps.required_capability(4, scheduled=True),
            "scheduled_transfer_collective")

    def test_instant_beats_dated(self):
        """SEPA Instant is executed now by definition; there is no dated
        instant payment, and the segment sent is HKIPZ either way."""
        self.assertEqual(
            caps.required_capability(1, scheduled=True, instant=True),
            "instant_transfer")
        self.assertEqual(
            caps.required_capability(2, instant=True),
            "instant_transfer_collective")


class TestTheCatalogueIsConsistent(unittest.TestCase):

    def test_every_key_is_reachable_from_its_segments(self):
        for key, segments, _label in caps.CATALOGUE:
            for segment in segments:
                self.assertEqual(caps.KEY_BY_SEGMENT[segment], key)

    def test_no_segment_belongs_to_two_capabilities(self):
        seen = []
        for _key, segments, _label in caps.CATALOGUE:
            seen.extend(segments)
        self.assertEqual(len(seen), len(set(seen)))

    def test_the_transactions_this_app_can_issue_are_in_it(self):
        """Every segment kefiya sends or reads must be answerable, otherwise
        the gate has nothing to say about the order it is gating."""
        for segment in ("HKCCS", "HKCCM", "HKCSE", "HKCME", "HKIPZ", "HKIPM",
                        "HKCDB", "HKDBS", "HKKAZ", "HKSAL", "HKWPD", "HKEKA"):
            self.assertIn(segment, caps.KEY_BY_SEGMENT)

    def test_segments_are_five_letters(self):
        for _key, segments, _label in caps.CATALOGUE:
            for segment in segments:
                self.assertEqual(len(segment), 5, segment)
                self.assertEqual(segment, segment.upper())


class TestTheRefusalHappensBeforeTheTan(unittest.TestCase):

    def test_the_send_endpoint_asks_before_it_locks(self):
        import inspect

        from kefiya.utils import client

        source = inspect.getsource(client.submit_kefiya_transfer)
        self.assertIn("_refuse_unsupported", source)
        # Before the send lock is claimed, which is the last step before the
        # bank is contacted.
        self.assertLess(source.index("_refuse_unsupported"),
                        source.index("_claim_send_lock"))

    def test_the_outbox_asks_for_the_collective_transaction(self):
        """Several orders leave as one HKCCM. An account cleared for a single
        transfer is not thereby cleared for the collective one."""
        import inspect

        from kefiya.utils import client

        source = inspect.getsource(client.send_transfer_outbox)
        self.assertIn("_refuse_unsupported", source)
        self.assertLess(source.index("_refuse_unsupported"),
                        source.index("_claim_send_lock"))

    def test_an_unknown_account_is_not_stopped(self):
        import inspect

        from kefiya.utils import client

        source = inspect.getsource(client._refuse_unsupported)
        # Asks `refuses`, not `supports`: only an explicit no stops an order.
        self.assertIn("caps.refuses(", source)
        self.assertIn("return None", source)


class TestItIsReadAtEveryFetch(unittest.TestCase):

    def test_the_fetch_refreshes_the_list(self):
        import inspect

        from kefiya.utils import client

        source = inspect.getsource(client.fetch_all)
        self.assertIn("refresh_account_capabilities", source)

    def test_it_is_best_effort_like_the_other_extras(self):
        """A bank that sends no HIUPD must not fail a fetch that worked."""
        import inspect

        from kefiya.utils import client

        source = inspect.getsource(client.fetch_all)
        block = source.split("refresh_account_capabilities")[-1]
        self.assertIn("_optional_fetch", block)


class TestTheGateNeverBreaksTheSendPath(unittest.TestCase):
    """This module sits between a person and their bank. Whatever it cannot
    answer, it must answer with "unknown" -- never with an exception."""

    def test_the_child_table_is_queried_by_parent_doctype(self):
        """`parent=` is not a query argument. get_all() pops `parent_doctype`
        and passes everything else to DatabaseQuery.execute(), which has no
        `parent` -- so the wrong spelling raises TypeError rather than
        querying the wrong rows."""
        import inspect

        source = inspect.getsource(caps.stored_rows)
        self.assertIn('parent_doctype="Bank Account"', source)
        self.assertNotIn('parent="Bank Account"', source)

    def test_a_failed_read_is_an_empty_list_not_an_exception(self):
        import inspect

        source = inspect.getsource(caps.stored_rows)
        self.assertIn("except Exception:", source)
        self.assertIn("return []", source.split("except Exception:")[1])

    def test_and_the_failure_is_recorded(self):
        """Silently swallowing it would hide a gate that has stopped
        gating."""
        import inspect

        source = inspect.getsource(caps.stored_rows)
        self.assertIn("frappe.log_error", source)


class TestTheFieldsAreDeclaredByThisApp(unittest.TestCase):
    """A field that exists on one instance and nowhere else is a field that
    quietly does nothing after a fresh install."""

    def _bank_account_fields(self):
        from kefiya.setup.install import get_custom_fields

        return {f["fieldname"]: f
                for f in get_custom_fields()["Bank Account"]}

    def test_the_table_points_at_the_child_doctype(self):
        field = self._bank_account_fields()["custom_fints_capabilities"]
        self.assertEqual(field["fieldtype"], "Table")
        self.assertEqual(field["options"], caps.CHILD_DOCTYPE)

    def test_nobody_types_it_in(self):
        field = self._bank_account_fields()["custom_fints_capabilities"]
        self.assertEqual(field["read_only"], 1)

    def test_when_it_was_last_read_is_kept(self):
        field = self._bank_account_fields()["custom_capabilities_checked_on"]
        self.assertEqual(field["fieldtype"], "Datetime")

    def test_the_module_and_the_fields_agree_on_the_names(self):
        fields = self._bank_account_fields()
        self.assertIn(caps.FIELD_TABLE, fields)
        self.assertIn(caps.FIELD_CHECKED_ON, fields)


class TestARepeatedAnswerIsNotAChange(unittest.TestCase):
    """Rewriting the table renames every row, and a tracked Bank Account
    records that -- once per account per fetch, for a list that rarely moves."""

    def test_the_same_list_compares_equal(self):
        rows = caps._rows_for({"HKCCS": {}, "HKSAL": {}})
        self.assertTrue(caps._same_rows(rows, caps._rows_for(
            {"HKCCS": {}, "HKSAL": {}})))

    def test_a_new_permission_is_a_change(self):
        rows = caps._rows_for({"HKCCS": {}})
        self.assertFalse(caps._same_rows(rows, caps._rows_for(
            {"HKCCS": {}, "HKCSE": {}})))

    def test_a_withdrawn_permission_is_a_change(self):
        rows = caps._rows_for({"HKCCS": {}, "HKCSE": {}})
        self.assertFalse(caps._same_rows(rows, caps._rows_for({"HKCCS": {}})))

    def test_a_changed_limit_is_a_change(self):
        rows = caps._rows_for({"HKCCS": {"limit_amount": 5000}})
        self.assertFalse(caps._same_rows(rows, caps._rows_for(
            {"HKCCS": {"limit_amount": 10000}})))

    def test_the_label_is_not_compared(self):
        """It is display text and follows the reader's language; a German
        session must not rewrite every account an English one just wrote."""
        self.assertNotIn("business_transaction", caps.COMPARED)


class TestTheLabelFollowsTheSiteNotTheSession(unittest.TestCase):
    """A fetch normally runs as a background job, and a background job has no
    user and therefore no language. Left to `_()` it would write English
    labels onto a German site -- and they would stay, because a later fetch
    only rewrites the table when something MEANINGFUL changed."""

    def test_the_language_comes_from_the_site(self):
        import inspect

        source = inspect.getsource(caps._label)
        self.assertIn('get_single_value("System Settings", "language")',
                      source)

    def test_the_rows_use_it(self):
        import inspect

        source = inspect.getsource(caps._rows_for)
        self.assertIn("_label(label)", source)
        self.assertIn('_label("Other business transaction")', source)

    def test_an_older_signature_does_not_break_a_fetch(self):
        """A label is not worth failing a fetch over."""
        import inspect

        source = inspect.getsource(caps._label)
        self.assertIn("except TypeError:", source)

    def test_the_catalogue_loop_does_not_shadow_it(self):
        """`for _key, _segments, _label in CATALOGUE` at module level would
        bind `_label` to a string. The def below happens to win, but nobody
        should have to check the order of two unrelated lines."""
        import inspect

        source = inspect.getsource(caps)
        self.assertNotIn("for _key, _segments, _label in CATALOGUE", source)
        self.assertTrue(callable(caps._label))


class TestTheIbanNeverEndsUpInAMessage(unittest.TestCase):

    def test_a_skipped_account_is_named_by_its_last_four_digits(self):
        self.assertEqual(caps._mask("DE02 1203 0000 0000 2020 51"), "...2051")

    def test_nothing_in_no_iban(self):
        self.assertIsNone(caps._mask(None))
        self.assertIsNone(caps._mask(""))
