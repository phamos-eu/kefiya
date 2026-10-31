# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import inspect
import unittest

from kefiya.kefiya.doctype.kefiya_transfer.kefiya_transfer import (
    is_valid_iban,
    normalize_iban,
)
from kefiya.utils import client
from kefiya.utils.fints_controller import FinTSController


class TestIbanValidation(unittest.TestCase):
    """With free recipient entry there is no invoice to check the account
    against, so the mod-97 checksum is the only automatic defence between a
    typo and a stranger's account."""

    def test_accepts_valid_ibans(self):
        for iban in [
            "DE89370400440532013000",   # documentation example
            "GB82WEST12345698765432",
            "FR1420041010050500013M02606",
        ]:
            self.assertTrue(is_valid_iban(iban), iban)

    def test_rejects_single_digit_typo(self):
        """One wrong digit must not pass -- this is the case that matters."""
        self.assertTrue(is_valid_iban("DE89370400440532013000"))
        self.assertFalse(is_valid_iban("DE89370400440532013001"))

    def test_rejects_transposed_characters(self):
        self.assertFalse(is_valid_iban("DE98370400440532013000"))

    def test_rejects_malformed_input(self):
        for bad in [None, "", "   ", "DE89", "XX00", "DE89-INVALID-!",
                    "1234567890123456", "D" * 40]:
            self.assertFalse(is_valid_iban(bad), repr(bad))

    def test_normalisation_is_tolerant_of_formatting(self):
        spaced = "DE89 3704 0044 0532 0130 00"
        self.assertEqual(normalize_iban(spaced), "DE89370400440532013000")
        self.assertTrue(is_valid_iban(spaced))
        self.assertTrue(is_valid_iban("de89 3704-0044 0532 0130 00"))


class TestCollectiveTransfer(unittest.TestCase):
    """Several payments must go out as one collective order (HKCCM), which the
    bank authorises with a single TAN, instead of one order -- and one TAN --
    per payment."""

    def test_control_sum_required_for_collective_order(self):
        source = inspect.getsource(FinTSController.submit_sepa_transfer)
        self.assertIn(
            "control_sum", source,
            "A collective order must carry a control sum; the bank checks the "
            "batch against it.",
        )
        self.assertIn(
            "multiple", source,
            "The collective-order flag must be forwarded to the library.",
        )

    def test_single_transfer_sends_no_batch_arguments(self):
        """Banks reject a control sum on an individual transfer, so the batch
        arguments may only be present for a genuine collective order."""
        source = inspect.getsource(FinTSController.submit_sepa_transfer)
        self.assertIn(
            'kwargs["multiple"] = True', source,
            "Batch arguments must be added conditionally, not passed always.",
        )

    def test_send_endpoint_gates_on_confirmation_and_permission(self):
        source = inspect.getsource(client.submit_kefiya_transfer)
        self.assertIn("cint(confirmed)", source)
        self.assertIn("has_permission", source)
        self.assertIn(
            'ptype="submit"', source,
            "Sending money is a submit-level action, not a write-level one.",
        )

    def test_send_requires_submitted_document(self):
        source = inspect.getsource(client.submit_kefiya_transfer)
        self.assertIn(
            "docstatus != 1", source,
            "A draft transfer must never reach the bank -- submit is what "
            "separates the person entering a transfer from the one releasing "
            "it.",
        )

    def test_send_is_not_repeatable(self):
        source = inspect.getsource(client.submit_kefiya_transfer)
        self.assertIn('doc.status == "Sent"', source)
        self.assertIn(
            "cache()", source,
            "A double click must not send real money twice.",
        )

    def test_unconfirmed_send_is_refused(self):
        result = client.submit_kefiya_transfer(
            transfer_name="irrelevant", user_scope="irrelevant"
        )
        self.assertEqual(result["status"], "error")


class TestTransferApprovalSeparation(unittest.TestCase):
    def test_submitting_does_not_send(self):
        """Approving a transfer must never move money as a side effect."""
        from kefiya.kefiya.doctype.kefiya_transfer import kefiya_transfer

        source = inspect.getsource(kefiya_transfer.KefiyaTransfer)
        self.assertNotIn(
            "submit_sepa_transfer", source,
            "The controller must not contact the bank; sending is a separate, "
            "explicitly confirmed action.",
        )
        self.assertIn(
            "def before_submit", source,
            "Submit should mark the transfer approved, nothing more.",
        )


class TestOutbox(unittest.TestCase):
    """The outbox collects orders and sends them together on one TAN.

    Its refusals matter more than its conveniences: a wrong batch debits the
    wrong account or pays twice, so each precondition aborts the whole send
    rather than quietly dropping or including a document.
    """

    def test_send_requires_confirmation(self):
        result = client.send_transfer_outbox(
            transfer_names=["KEF-TRF-0001"], user_scope="x"
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("not confirmed", result["message"].lower())

    def test_empty_selection_is_refused(self):
        result = client.send_transfer_outbox(
            transfer_names=[], user_scope="x", confirmed=1
        )
        self.assertEqual(result["status"], "error")

    def test_batch_refuses_mixed_accounts_already_at_build_time(self):
        """One pain.001 carries exactly one debtor; mixing would debit the
        wrong account."""
        from kefiya.kefiya.doctype.kefiya_transfer import kefiya_transfer

        source = inspect.getsource(kefiya_transfer.build_pain001_for)
        self.assertIn("same account", source)

        endpoint = inspect.getsource(client.send_transfer_outbox)
        self.assertIn("same account", endpoint)

    def test_batch_refuses_held_back_and_already_sent(self):
        source = inspect.getsource(client.send_transfer_outbox)
        self.assertIn(
            "doc.on_hold", source,
            "A held-back order must never ride along in a collective send.",
        )
        self.assertIn(
            'doc.status == "Sent"', source,
            "An already-sent order must not be paid a second time.",
        )
        self.assertIn(
            "docstatus != 1", source,
            "Unapproved orders must not reach the bank.",
        )

    def test_batch_marks_all_or_none_as_sent(self):
        """Leaving part of an accepted batch unsent invites paying it twice."""
        source = inspect.getsource(client.send_transfer_outbox)
        self.assertIn("for doc in docs:", source)
        self.assertIn(
            '"Scheduled at Bank" if scheduled else "Sent"', source,
            "Every document of an accepted batch is marked in the same pass. "
            "Which of the two states it gets depends on whether the bank now "
            "holds it for a later date or has already paid it.",
        )

    def test_hold_is_permitted_after_approval(self):
        """Holding changes when an order is sent, not what it says."""
        from kefiya.kefiya.doctype.kefiya_transfer import kefiya_transfer

        source = inspect.getsource(kefiya_transfer.KefiyaTransfer.set_hold)
        self.assertIn("docstatus != 1", source)
        self.assertIn('self.status == "Sent"', source)

    def test_hold_endpoint_checks_permission(self):
        source = inspect.getsource(client.set_transfer_hold)
        self.assertIn("has_permission", source)
        self.assertIn('ptype="submit"', source)


class TestOutboxHardening(unittest.TestCase):
    """Three ways the outbox could have paid twice or misassigned rights."""

    def test_duplicate_names_are_collapsed(self):
        """The same order listed twice would appear twice in one pain.001 --
        the recipient would be paid twice from a single order."""
        from kefiya.utils.client import _parse_transfer_names

        self.assertEqual(_parse_transfer_names(["A", "A", "B"]), ["A", "B"])
        self.assertEqual(_parse_transfer_names('["A", "A"]'), ["A"])

    def test_non_list_selection_is_refused(self):
        """A JSON string would be iterated character by character."""
        from kefiya.utils.client import _parse_transfer_names

        bad = ['"KEF-001"', '{"a": 1}', "123", "broken{", [1, 2], [""]]
        for payload in bad:
            with self.assertRaises(Exception, msg=repr(payload)):
                _parse_transfer_names(payload)

    def test_both_send_paths_lock_the_same_key(self):
        """A document must not be sendable through the single and the
        collective path at the same time."""
        single = inspect.getsource(client.submit_kefiya_transfer)
        batch = inspect.getsource(client.send_transfer_outbox)
        self.assertIn('"kefiya_transfer_doc:"', single)
        self.assertIn('"kefiya_transfer_doc:"', batch)

    def test_hold_is_permission_checked(self):
        """A whitelisted document method is callable by anyone who may read the
        document; releasing an order makes it eligible for the next send."""
        from kefiya.kefiya.doctype.kefiya_transfer import kefiya_transfer

        source = inspect.getsource(kefiya_transfer.KefiyaTransfer.set_hold)
        self.assertIn("has_permission", source)
        self.assertIn('ptype="submit"', source)


class TestADecoupledReleaseOnTheTransferPath(unittest.TestCase):
    """The Sparkasse uses a decoupled procedure, and this path could not do
    one at all.

    _await_release was reachable from exactly one place -- the statement
    fetch. A transfer that met a decoupled challenge parked it and answered
    "tan_required", and nothing polled afterwards: when the banking app
    reported an error there was no request in flight to report it to, and the
    user saw nothing at all.
    """

    def _controller(self):
        from kefiya.utils.fints_controller import FinTSController
        return FinTSController

    def test_every_money_path_settles_its_tan_the_same_way(self):
        """Three call sites, one shape. There were three different answers to
        one question in this file, and the debit still had the old one."""
        import inspect

        source = inspect.getsource(self._controller())
        self.assertEqual(source.count("response, pending = self._settle_tan(response)"), 3)
        self.assertEqual(source.count('"status": "tan_required"'), 1,
                         "The result is built in one place, not at each site.")

    def test_it_answers_a_pair_like_the_payee_check_does(self):
        """The same shape _confirm_or_park_vop uses for the same kind of
        question. It was a single channel where None meant two different
        things and non-None meant two others."""
        import inspect

        body = inspect.getsource(self._controller()._settle_tan)
        self.assertIn("return response, None", body)
        self.assertIn("return released, None", body)
        self.assertIn("return None, {", body)

    def test_a_decoupled_challenge_is_waited_out_before_it_is_parked(self):
        import inspect

        body = inspect.getsource(self._controller()._settle_tan)
        self.assertIn("self._publish_tan_prompt(response, decoupled=True)", body)
        self.assertLess(body.index("self._await_release(response)"),
                        body.index("self._park_tan_challenge(response)"))

    def test_an_ordinary_tan_is_left_alone(self):
        """Only a decoupled challenge is waited out; a typed TAN is answered
        by a person in a later request, as it always was."""
        import inspect

        body = inspect.getsource(self._controller()._settle_tan)
        self.assertIn('getattr(response, "decoupled", False)', body)
        self.assertIn("self.ask_for_tan(response)", body)


class TestTheBankIsAskedNotAssumed(unittest.TestCase):
    """Both endpoints that answer a parked challenge built a controller and
    called it success if that did not raise. Neither looked at the answer, so
    a refused order got the same green "authorised and submitted" as an
    accepted one."""

    def test_the_answer_is_read_where_the_tan_is_answered(self):
        """Once, in the controller -- so send_transfer_tan and
        resolve_tan_interaction both inherit it and neither has to reach into
        the connection for it."""
        import inspect

        from kefiya.utils.fints_controller import FinTSController
        resume = inspect.getsource(
            FinTSController._resume_and_answer_the_parked_tan)
        self.assertIn("self._refuse_a_refused_order()", resume)

        check = inspect.getsource(FinTSController._refuse_a_refused_order)
        self.assertIn("fints_response.verdict_of(", check)
        self.assertIn("fints_response.refused(verdict)", check)
        self.assertIn("frappe.throw", check)
        self.assertIn("NOT", check)

    def test_the_api_does_not_reach_into_the_controller(self):
        """It read controller.fints_connection.init_tan_response through two
        defensive getattrs -- the API layer knowing the controller's
        internals, with the defaults hiding an invariant nobody stated."""
        import inspect

        from kefiya.utils import client
        source = inspect.getsource(client.send_transfer_tan)
        self.assertNotIn("init_tan_response", source)
        self.assertNotIn("fints_connection", source)


class TestTheReleaseBoxAnswersTheRightDocument(unittest.TestCase):
    """The box resolved with the form it was shown on. That is the login only
    on the Kefiya Login screen; a transfer passes its own docname as the UI
    scope, so the box answered against a KEF-TRF-... name -- and the account
    context, looked up the same way, came back empty, which is why it named
    no bank and no account on the one screen where money moves."""

    def test_the_payload_carries_the_login(self):
        import inspect

        from kefiya.utils.fints_interactive import FinTSInteractive
        source = inspect.getsource(FinTSInteractive.request_tan_prompt)
        self.assertIn('"fints_login": self.login_name()', source)

    def test_the_context_is_looked_up_by_the_login(self):
        import inspect

        from kefiya.utils.fints_interactive import FinTSInteractive
        source = inspect.getsource(FinTSInteractive)
        self.assertIn('"Kefiya Login", self.login_name()', source)
        self.assertNotIn('"Kefiya Login", self.docname', source)

    def test_the_controller_says_which_login_it_is(self):
        import inspect

        from kefiya.utils.fints_controller import FinTSController
        self.assertIn("self.interactive.fints_login = self.kefiya_login.name",
                      inspect.getsource(FinTSController.__init__))

    def test_both_boxes_resolve_with_it(self):
        import os

        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        for name in ("fints_interactive.js", "tan_prompt.js"):
            with open(os.path.join(base, "public", "js", "controllers", name),
                      encoding="utf-8") as handle:
                self.assertIn("data.fints_login ||", handle.read(), name)

    def test_the_transfer_screen_opens_no_second_box(self):
        """Two boxes for one question, and only one of them could ever be
        right: the other had a mandatory TAN field for procedures that
        produce no code."""
        import os

        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        with open(os.path.join(base, "public", "js", "controllers",
                               "fints_transfer_flow.js"), encoding="utf-8") as h:
            source = h.read()
        self.assertNotIn("frappe.ui.Dialog", source)
        self.assertNotIn("send_transfer_tan", source)


class TestEveryMoneyPathAsksTheBank(unittest.TestCase):
    """The last question -- did the bank take it -- was asked on exactly one
    of the three paths.

    The payee release and the direct debit both ended with

        frappe.log_error("... completed without TAN challenge")
        return {"status": "submitted"}

    noting that the bank had said something and reporting success anyway.
    Same file, same question, three different answers -- the third time that
    shape turned up in this class.
    """

    def _bodies(self):
        import inspect

        from kefiya.utils.fints_controller import FinTSController
        return {name: inspect.getsource(getattr(FinTSController, name))
                for name in ("submit_sepa_transfer", "approve_pending_vop",
                             "submit_sepa_debit")}

    def test_none_of_them_reports_success_without_asking(self):
        for name, body in self._bodies().items():
            # Past the docstring: its :return: line names "submitted" too, and
            # matching that would compare against prose rather than code --
            # the assertion would then pass with the call in the wrong place.
            code = body[body.index('"""', body.index('"""') + 3) + 3:]
            self.assertIn('{"status": "submitted"}', code, name)
            self.assertIn("self._refuse_a_refused_order(response)", code, name)
            self.assertLess(code.index("self._refuse_a_refused_order(response)"),
                            code.index('{"status": "submitted"}'), name)

    def test_the_question_is_asked_in_one_place(self):
        """It was written out inline in submit_sepa_transfer -- log, throw,
        advice block -- and nowhere else."""
        import inspect

        import kefiya.utils.fints_controller as fc
        source = inspect.getsource(fc)
        self.assertEqual(source.count('title=_("Refused by the bank")'), 1)

    def test_the_release_also_refuses_an_unsigned_order(self):
        """It releases a transfer; an unsigned one is not executed, and
        calling it sent is how an invoice gets believed paid."""
        self.assertIn("self._refuse_unsigned(",
                      self._bodies()["approve_pending_vop"])

    def test_the_debit_says_why_it_has_no_unsigned_guard(self):
        """The guard reads a transfer's shape -- collective, dated, instant --
        and a debit is none of those. Silence here would read as an
        oversight; the comment says it is a gap."""
        body = self._bodies()["submit_sepa_debit"]
        self.assertNotIn("self._refuse_unsigned(", body)
        self.assertIn("does not have one yet", body)
