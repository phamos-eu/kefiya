# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The outgoing-payments list, and the promises it makes about money.

The list used to be a stored script inside a Custom HTML Block on one site:
19 KB of JavaScript in a database row, with no review, no history and no test
around it -- on the page that sends money. It lives in this app now, and the
properties that matter are asserted rather than hoped for.

Four of them, and each has a way it goes wrong:

  * a batch button offers more than it will do -- somebody selects thirty
    orders, presses Approve, and only nine were drafts, so twenty-one silently
    did nothing;
  * a batch reports only its successes, and half a selection goes missing;
  * a send picks one paying account out of several, debiting the wrong one;
  * approving is presented as sending, so a click that was meant to lock an
    order in fact moves money.
"""

import os
import re
import unittest


def _source(name="payment_outbox.js"):
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "public", "js", "controllers", name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _function(source, header):
    """The body of one function, up to the next one at the same indent."""
    tail = source.split(header)[1]
    end = tail.find("\n\tfunction ")
    if end < 0:
        end = tail.find("\n    function ")
    return tail[:end if end > 0 else len(tail)]


class TestABatchOffersOnlyWhatItWillDo(unittest.TestCase):

    def test_every_batch_action_has_a_rule_for_what_it_may_touch(self):
        body = _function(_source(), "function applicable(what) {")
        for action in ("approve", "delete", "hold", "release", "send"):
            self.assertIn('=== "{0}"'.format(action), body,
                          "{0} would fall through and touch nothing, or "
                          "everything.".format(action))

    def test_approving_and_deleting_are_limited_to_drafts(self):
        body = _function(_source(), "function applicable(what) {")
        for action in ("approve", "delete"):
            line = [ln for ln in body.splitlines()
                    if '=== "{0}"'.format(action) in ln][0]
            self.assertIn("docstatus === 0", line,
                          "An approved order must not be re-approved or "
                          "deleted from a list.")

    def test_sending_follows_the_flag_the_server_sets(self):
        """`sendable` is the server's answer, which knows about hold, due date
        and payee verification. Re-deriving it here would drift from it.

        A draft is the one addition, and it is not a second opinion about a
        sendable order: it is the row the send button is about to approve.
        """
        body = _function(_source(), "function applicable(what) {")
        rule = body.split('if (what === "send")')[1].split("}")[0]
        self.assertIn("r.sendable", rule)
        self.assertIn("outbox_only_lacks_approval", rule)

    def test_the_buttons_count_what_is_applicable_and_not_the_selection(self):
        source = _source()
        for action in ("delete", "release", "hold", "approve", "send"):
            self.assertIn('applicable("{0}").length'.format(action), source,
                          "The button label must count what the action will "
                          "actually touch.")
        self.assertNotIn("selection().length)", source.split(
            "function footer()")[1].split("function render()")[0],
            "Labelling a button with the whole selection promises more than "
            "the action does.")


class TestNoBatchReportsOnlyItsSuccesses(unittest.TestCase):

    def test_the_batch_report_names_the_documents_that_refused(self):
        body = _function(_source(),
                         "function reportBatch(done, failed, okText, "
                         "failTitle) {")
        self.assertIn("if (!failed.length)", body)
        self.assertIn("f.reason", body,
                      "A refusal without its reason is not a report.")

    def test_approving_and_deleting_both_report_through_it(self):
        source = _source()
        self.assertIn('reportBatch(m.approved || [], m.refused || [],', source)
        self.assertIn("reportBatch(gone, failed,", source)

    def test_deleting_collects_failures_instead_of_stopping(self):
        """One draft that cannot be deleted must not abandon the rest."""
        body = _function(_source(), "function remove() {")
        self.assertIn("failed.push({ name: r.name, reason: errText(e) });",
                      body)
        self.assertIn("chain = chain.then(", body,
                      "Deleting runs in sequence so a refusal names its "
                      "order.")


class TestSendingRefusesRatherThanGuesses(unittest.TestCase):

    def test_several_paying_accounts_rule_out_the_collective_way(self):
        """A collective order leaves from exactly one account -- that is what
        makes it ONE order. Sent one after another, each order pays from its
        own, so a mixed selection is refused for the collective way and only
        there. It used to be refused outright, and the one way that works
        was not offered at all."""
        body = _function(_source(), "function send() {")
        self.assertIn("groupByAccount(rows)", body)
        self.assertIn("askHowToSend(rows, message, toApprove,"
                      " groups.length === 1)", body)
        frage = _function(_source(), "function askHowToSend(")
        self.assertIn("way.stored !== TOGETHER", frage)
        self.assertIn("leaves from exactly one account.", frage)

    def test_the_send_says_what_it_is_leaving_out(self):
        body = _function(_source(), "function send() {")
        self.assertIn("const skipped = selection().length - rows.length;",
                      body)
        self.assertIn("if (skipped > 0)", body)

    def test_the_send_is_confirmed_and_says_nothing_is_debited_yet(self):
        body = _function(_source(), "function send() {")
        self.assertIn("frappe.confirm(message, function () {", body)
        self.assertIn("Nothing is debited until then.", body)

    def test_the_bank_is_told_the_send_was_confirmed(self):
        """Split out of the send itself when the send button took on the
        approval: what goes to the bank now goes from handToBank(), and the
        approval happens inside that one call, behind the server's refusals."""
        body = _function(
            _source(), "function handToBank(names, login, approving) {")
        self.assertIn("confirmed: 1", body)
        self.assertIn("approve_drafts", body)


class TestTheConfirmationCanBeCheckedOnItsOwn(unittest.TestCase):
    """It used to be a recipient name and an amount.

    That is not enough to check anything: it did not say which account is
    debited, it did not show the recipient's IBAN, it did not name the
    reference, and it did not say whether the order goes out today or on a
    date. The person pressing the button is often not the person who entered
    the order -- which is the whole reason the payee check moved to entry
    time -- so what they confirm has to be readable on its own.
    """

    @staticmethod
    def _panel():
        return _source().split("kefiya.outbox_confirm_html = function")[1] \
            .split("\n};")[0]

    def test_the_paying_account_is_named_with_its_iban(self):
        """The row names the account but not its IBAN, and an IBAN is what
        somebody comparing this against online banking reads."""
        body = self._panel()
        self.assertIn('__("Paying account")', body)
        self.assertIn("payer.iban", body)
        self.assertIn("kefiya.iban_pretty", body)

    def test_every_field_of_a_payment_is_there(self):
        body = self._panel()
        for field in ('__("Recipient")', '__("IBAN")', '__("Amount")',
                      '__("Reference")', '__("Execution")',
                      '__("Transfer type")'):
            self.assertIn(field, body, field)

    def test_a_collective_order_shows_every_recipient(self):
        """One block per payment, not per order -- a row per order would hide
        all but the first recipient of a collective order."""
        body = self._panel()
        self.assertIn("(row.items || []).forEach", body)

    def test_the_execution_is_worded_by_the_shared_helper(self):
        """A confirmation that described the execution differently from the
        box the order was entered in would be worse than a short one."""
        body = self._panel()
        self.assertIn("kefiya.execution_sentence(row)", body)
        self.assertIn("kefiya.transfer_kind(row)", body)

    def test_the_style_travels_with_the_markup(self):
        """frappe.confirm renders into a modal in the main document, not
        inside the page's shadow root where the outbox stylesheet lives."""
        body = self._panel()
        self.assertIn("<style>", body)

    def test_the_send_confirmation_uses_it(self):
        """One block per paying account: a single block over a mixed
        selection would name one account and debit several."""
        body = _function(_source(), "function send() {")
        self.assertIn("kefiya.outbox_confirm_html(group.rows, group.payer)",
                      body)

    def test_the_payer_is_looked_up_and_may_be_missing(self):
        """An account that is no longer offered as a payer must not take the
        confirmation down with it."""
        body = _function(_source(), "function groupByAccount(")
        self.assertIn("view.payers.find", body)
        self.assertIn("|| null", body)
        panel = self._panel()
        self.assertIn("(payer && payer.bank_account)", panel)


class TestApprovingIsNotSending(unittest.TestCase):

    def test_the_confirmation_says_what_approving_does_and_does_not_do(self):
        body = _function(_source(), "function approve() {")
        self.assertIn("No money", body)
        self.assertIn("separate step", body,
                      "The whole question this page raised was what "
                      "\"Freigeben\" means. It has to answer it where it is "
                      "asked.")

    def test_approving_goes_through_the_apps_own_endpoint(self):
        self.assertIn("kefiya.utils.client.approve_transfers", _source())


class TestApprovingABatchIsNotAllOrNothing(unittest.TestCase):
    """The endpoint behind the Approve button, read from its source.

    Two ways a batch approval goes wrong, and both are quiet: a document that
    refuses rolls back the ones approved before it, or it is skipped without
    anybody being told.
    """

    @staticmethod
    def _endpoint():
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "utils", "client.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        return source.split("def approve_transfers(")[1].split(
            "\n@frappe.whitelist()")[0]

    def test_each_document_is_checked_for_permission_on_its_own(self):
        self.assertIn('frappe.has_permission(\n            "Kefiya Transfer",'
                      ' ptype="submit", doc=name, throw=True)',
                      self._endpoint())

    def test_a_refusal_rolls_back_only_its_own_document(self):
        body = self._endpoint()
        self.assertIn("frappe.db.savepoint(point)", body)
        self.assertIn("frappe.db.rollback(save_point=point)", body,
                      "A bare rollback would discard the approvals that "
                      "already succeeded in this batch.")
        self.assertNotIn("frappe.db.rollback()", body)

    def test_every_refusal_comes_back_with_a_reason(self):
        body = self._endpoint()
        self.assertIn('refused.append({"name": name, "reason":', body)
        self.assertIn('"refused": refused', body)

    def test_an_already_approved_order_is_named_rather_than_resubmitted(self):
        body = self._endpoint()
        self.assertIn("if doc.docstatus != 0:", body)


class TestTheRowIsTheButton(unittest.TestCase):

    def test_a_row_opens_its_order(self):
        source = _source()
        self.assertIn("tr.onclick = open;", source)
        self.assertIn("kefiya.transfer_details(r, {", source)

    def test_no_per_row_action_buttons_are_rendered(self):
        """They were the reason the row could not be clicked: six small
        buttons per line, repeated on every one of them."""
        table = _source().split("function table()")[1].split(
            "function footer()")[0]
        for gone in ("zk-det", "zk-edit", "zk-free", "zk-hold", "zk-del",
                     "zk-mini"):
            self.assertNotIn(gone, table)

    def test_the_checkbox_still_only_selects(self):
        source = _source()
        self.assertIn('e.target.closest(".zk-cx")', source,
                      "A click on the checkbox must not also open the order.")

    def test_a_row_can_be_reached_without_a_mouse(self):
        source = _source()
        self.assertIn("tabindex='0'", source)
        self.assertIn('e.key === "Enter" || e.key === " "', source)


class TestTheChoicesAreRemembered(unittest.TestCase):

    def test_sorting_and_the_sent_filter_are_written_back(self):
        body = _function(_source(), "function remember() {")
        for key in ("sort_by", "sort_dir", "show_sent"):
            self.assertIn(key, body)

    def test_both_things_that_can_be_chosen_are_remembered(self):
        """Two places change a setting, and both have to store it: the sort
        headers and the "show sent" box."""
        source = _source()
        self.assertIn("remember();",
                      _function(source, "function sortBy(key) {"))
        sent = source.split('if (act === "sent") {')[1][:400]
        self.assertIn("remember();", sent)

    def test_a_third_click_restores_the_servers_order(self):
        """The server orders drafts first and then by due date. Once a column
        had been clicked that order would otherwise be unreachable."""
        body = _function(_source(), "function sortBy(key) {")
        self.assertIn("view.sortBy = null;", body)

    def test_reading_prefers_the_framework_over_the_browser(self):
        body = _source().split("kefiya.outbox_settings = {")[1].split(
            "kefiya.payment_outbox")[0]
        framework = body.find("frappe.model.user_settings")
        local = body.find("window.localStorage.getItem")
        self.assertTrue(0 < framework < local,
                        "Settings that follow the user to another browser win "
                        "over the ones that do not.")


class TestEverySortableColumnSortsByWhatItShows(unittest.TestCase):

    def test_each_column_carries_its_own_sort_key(self):
        block = _source().split("kefiya.OUTBOX_COLUMNS = [")[1].split("\n];")[0]
        keys = re.findall(r'key: "(\w+)"', block)
        self.assertEqual(len(keys), len(re.findall(r"sort: function", block)),
                         "A column without a sort key would render a header "
                         "that does nothing when clicked.")
        self.assertEqual(keys, ["state", "account", "recipient", "due",
                                "amount", "receipt", "note"])

    def test_an_undated_order_sorts_before_every_dated_one(self):
        """No date means "as soon as possible", which is sooner than any
        date -- sorting it to the end would bury what leaves first."""
        block = _source().split("kefiya.OUTBOX_COLUMNS = [")[1].split("\n];")[0]
        self.assertIn('r.execution_date || "0000-00-00"', block)


class TestTheSelectionCannotOutliveItsRows(unittest.TestCase):

    def test_a_vanished_order_is_dropped_from_the_selection(self):
        body = _function(_source(), "function load() {")
        self.assertIn("if (!alive[name]) delete view.selected[name];", body)


class TestTheStylesheetReachesThePage(unittest.TestCase):
    """A Custom HTML Block renders inside a shadow root.

    That is why the block is handed a `root_element` instead of reaching for
    document, and why it has a style field of its own. A stylesheet appended
    to document.head does not cross that boundary -- the page came up with
    every rule silently matching nothing, which is exactly what happened the
    first time this moved out of the block.
    """

    def test_the_stylesheet_goes_where_the_list_lives(self):
        body = _source("outbox_style.js").split(
            "kefiya.outbox_style = function (root) {")[1]
        body = body.split("\nkefiya.payment_outbox")[0]
        self.assertIn("root.getRootNode()", body,
                      "getRootNode answers the shadow root inside a block and "
                      "the document outside one.")
        self.assertIn("holder.appendChild(el)", body)
        self.assertNotIn("document.head.appendChild", body)

    def test_it_is_given_the_element_it_has_to_reach(self):
        self.assertIn("kefiya.outbox_style(root);", _source(),
                      "Called without the element it cannot find the shadow "
                      "root, and it is back to document.head.")

    def test_it_is_not_put_inside_the_element_that_gets_rewritten(self):
        """render() replaces #zk's innerHTML on every draw."""
        body = _source("outbox_style.js").split(
            "kefiya.outbox_style = function (root) {")[1]
        body = body.split("\nkefiya.payment_outbox")[0]
        self.assertNotIn("root.appendChild(el)", body)

    def test_the_block_keeps_no_second_copy_of_it(self):
        """Two stylesheets for one list is the split this move undid."""
        block = _source("../blocks/payment_outbox_block.js")
        self.assertNotIn("#zk .zk-", block)


class TestTheTanPromptIsTheSharedOne(unittest.TestCase):

    def test_the_outbox_does_not_build_its_own_prompt(self):
        source = _source()
        self.assertIn("kefiya.tan_prompt(data, live.load)", source)
        self.assertNotIn("frappe.prompt(", source,
                         "A second TAN box drifted from the first once "
                         "already: it named neither bank nor account.")

    def test_the_shared_prompt_is_actually_exported(self):
        self.assertIn("kefiya.tan_prompt = tanPrompt;",
                      _source("tan_prompt.js"))

    def test_a_stale_view_cannot_answer_for_the_live_one(self):
        """The handler is bound once per page load; the list is rebuilt on
        every visit."""
        body = _function(_source(), "function bindTan() {")
        self.assertIn("const live = kefiya._outbox;", body)


class TestTheDetailViewReadsAsATransferSlip(unittest.TestCase):
    """What the screenshot showed, and what must not come back.

    Three labels were wrong at once, and only one of them was a typo of ours.
    "State" was left to the framework on the assumption that an untranslated
    generic word is safe; the framework knows that word as the address field
    and rendered the order's state as "Bundesland". The stored status was
    printed raw in English next to its own German explanation. And a "Due
    date" row repeated the execution line above it, in English.
    """

    @staticmethod
    def _details():
        return _source("transfer_details.js")

    def test_the_state_is_not_labelled_with_a_word_the_framework_owns(self):
        source = self._details()
        self.assertNotIn('__("State")', source,
                         'The framework translates "State" as the address '
                         'field: the order state came out as "Bundesland".')
        self.assertIn('__("Order state")', source)

    def test_the_state_is_shown_through_the_shared_wording(self):
        """`row.status` is a stored English value. Printing it raw is how
        "Sent" ended up on a German screen."""
        source = self._details()
        self.assertIn("kefiya.outbox_state", source)
        self.assertNotIn('esc(row.status || "")', source)

    def test_the_execution_is_stated_once(self):
        """The "Due date" row said in English what the line above it had
        already said in German."""
        self.assertNotIn('__("Due date")', self._details())

    def test_a_locked_order_says_why_it_cannot_be_changed(self):
        """An absent Correct button explains nothing."""
        source = self._details()
        self.assertIn("if (row.docstatus === 1) {", source)
        self.assertIn("This order is with the bank", source)


class TestAReceiptIsFoundWhereItActuallyHangs(unittest.TestCase):
    """Nobody attaches the travel expense PDF to the transfer.

    It is created on the Business Trip and stays there; the transfer only says
    "Reisekosten BT-0001" in its purpose. Looking for files attached to the
    transfer alone reports "no receipt" for an order that plainly has one.
    """

    @staticmethod
    def _details():
        return _source("transfer_details.js")

    def test_the_purpose_is_searched_for_document_names(self):
        source = self._details()
        self.assertIn("kefiya.DOCNAME_IN_TEXT", source)
        self.assertIn("kefiya.transfer_referenced_documents", source)

    def test_the_lookup_covers_the_order_and_what_it_names(self):
        source = self._details()
        self.assertIn('attached_to_name: ["in", names]', source,
                      "Filtering on the transfer alone is the bug.")

    def test_the_pattern_does_not_match_a_recipients_invoice_number(self):
        """A looser pattern would send us looking for documents that are not
        ours. The names it has to catch are BT-0001 and RE-260427-034."""
        import re
        pattern = re.compile(r"\b[A-Z]{2,8}(?:-[A-Z0-9]{2,6})*-\d{3,}\b")
        for name in ("BT-0001", "RE-260427-034", "KEF-TRF-2026-00001"):
            self.assertTrue(pattern.search(name), name)
        for text in ("Rechnung 12345", "Miete 04/2026", "IBAN DE02"):
            self.assertIsNone(pattern.search(text), text)

    def test_a_receipt_from_elsewhere_says_where_it_came_from(self):
        """A file that appears out of nowhere is magic; one that names its
        document can be checked."""
        source = self._details()
        self.assertIn('__("from {0} {1}"', source)
        self.assertIn("fl.attached_to_name !== row.name", source)


class TestAnUnsignedOrderIsNotASentOrder(unittest.TestCase):
    """The most expensive kind of wrong: a payment recorded as made.

    An order of 70,40 EUR went out, the bank asked for no TAN, and the app
    wrote "Sent". In the online banking the transfer did not exist -- neither
    sent nor received. HIUPD had said all along what the account requires:

        HKCCS Ueberweisung          erlaubt, required_signatures 1
        HKIPZ Echtzeitueberweisung  erlaubt, required_signatures 1

    One signature. None was given. The old code could not tell "a bank that
    asks for no TAN" from "an order that was never signed", because it never
    asked the one question that separates them.
    """

    @staticmethod
    def _controller():
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "utils", "fints_controller.py")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    @staticmethod
    def _capabilities():
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "utils",
            "account_capabilities.py")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_a_tan_free_dialog_is_checked_before_it_counts_as_sent(self):
        source = self._controller()
        body = source.split("def submit_sepa_transfer(")[1].split(
            "\n    def ")[0]
        self.assertIn("self._refuse_unsigned(", body)
        refuse = body.find("self._refuse_unsigned(")
        submitted = body.find('result = {"status": "submitted"}')
        self.assertTrue(0 < refuse < submitted,
                        "Checked after the status is set is not checked.")

    def test_the_number_of_signatures_comes_from_what_the_bank_said(self):
        source = self._capabilities()
        self.assertIn("def required_signatures(", source)
        body = source.split("def required_signatures(")[1].split("\ndef ")[0]
        self.assertIn('cint(row.get("required_signatures"))', body)

    def test_nothing_stored_is_not_read_as_nothing_required(self):
        """The reading that turns a missing TAN into a successful payment."""
        source = self._capabilities()
        body = source.split("def required_signatures(")[1].split("\ndef ")[0]
        self.assertIn("return None", body)
        self.assertIn("if not rows:", body)

    def test_the_capability_asked_about_matches_the_order_that_was_sent(self):
        """An instant collective order is not the same business transaction as
        a single dated one, and they carry their own signature rules."""
        body = self._controller().split("def _refuse_unsigned(")[1].split(
            "\n    def ")[0]
        self.assertIn("payment_count=2 if multiple else 1", body)
        self.assertIn("scheduled=bool(scheduled)", body)
        self.assertIn("instant=bool(instant_payment)", body)

    def test_it_refuses_rather_than_guesses(self):
        body = self._controller().split("def _refuse_unsigned(")[1].split(
            "\n    def ")[0]
        self.assertIn("frappe.throw(", body)
        self.assertIn("has NOT been marked as sent", body)

    def test_the_refusal_tells_the_reader_to_check_before_repeating(self):
        """Refusing costs a repeated send; a repeated send that the bank did
        take costs twice the money."""
        body = self._controller().split("def _refuse_unsigned(")[1].split(
            "\n    def ")[0]
        self.assertIn("check the online banking before sending again", body)


class TestAParkedPayeeCheckIsNotASend(unittest.TestCase):
    """The one that cost a real transfer.

    The send handler had three branches -- error, tan_required, else -- and
    "else" reported "Handed to the bank: 1 orders" in green. A parked payee
    check returns "vop_mismatch", fell into that else, and an order that never
    left the building was reported as sent. There was no way to release it
    either: the box lived in a file this page cannot reach.
    """

    def test_the_send_handler_knows_the_parked_case(self):
        # Die Frage "was hat die Bank geantwortet" wohnt in sendOutcome,
        # seit auch der Einzelversand sie stellt. Der Bericht handelt danach.
        self.assertIn('m.status === "vop_mismatch"',
                      _function(_source(), "function sendOutcome("))
        body = _function(_source(), "function reportSendResult(")
        self.assertIn('outcome === "payee"', body)
        self.assertIn("kefiya.vop_prompt({", body)

    def test_the_parked_case_is_decided_before_the_success_branch(self):
        """Order matters: the success branch is the else of this chain."""
        body = _function(_source(), "function reportSendResult(")
        self.assertLess(body.index('"payee"'),
                        body.index("Handed to the bank: {0} orders"))

    def test_one_place_says_what_a_send_came_back_with(self):
        """The send and the release after a parked check come back the same
        three ways. Written out twice, the next status the server learns to
        return gets handled in one of them."""
        source = _source()
        self.assertEqual(source.count("Handed to the bank"), 1)
        self.assertEqual(source.count('status === "tan_required"'), 1)
        self.assertIn("reportSendResult(m, (m.sent || []).length",
                      _function(source, "function handToBank("))

    def test_the_box_is_in_the_bundle_so_this_page_can_reach_it(self):
        bundle = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "public", "js",
            "kefiya.bundle.js")
        with open(bundle, encoding="utf-8") as handle:
            self.assertIn('import "./controllers/vop_prompt";', handle.read())
        self.assertIn("kefiya.vop_prompt = vopPrompt;", _source("vop_prompt.js"))

    def test_the_transfer_form_uses_the_same_box(self):
        """Two copies of a money dialog drifted apart once already."""
        flow = _source("fints_transfer_flow.js")
        self.assertIn("kefiya.vop_prompt({", flow)
        self.assertNotIn("new frappe.ui.Dialog", flow.split(
            "function kefiya_prompt_vop_release(")[1].split("\n}")[0])

    def test_a_parked_order_is_not_shown_as_approved_for_sending(self):
        state = _function(_source(), "kefiya.outbox_state = function (r) {")
        self.assertIn("r.vop_pending", state)
        self.assertLess(state.index("r.vop_pending"),
                        state.index("Approved for sending"))
