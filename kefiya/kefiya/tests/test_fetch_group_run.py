# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One bank access, one dialog, all of its accounts.

fetch_group has always existed and paid the FinTS handshake once per access.
The browser never called it: it fetched account by account, so thirty accounts
on one Volksbank access meant thirty handshakes and thirty strong
authentications in quick succession. These assertions are about the wiring
that finally uses it.

Read as source text rather than imported: client.py imports frappe at the top
and never loads outside a bench, and a test that cannot run is worse than no
test -- it passes.
"""

import os
import re
import unittest


def _app(*parts):
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), *parts)


def _read(*parts):
    with open(_app(*parts), encoding="utf-8") as handle:
        return handle.read()


def _py_function(source, name):
    """The body of one top-level def, by indentation."""
    hit = re.search(r"^def %s\(" % re.escape(name), source, re.M)
    assert hit, name
    rest = source[hit.start():]
    lines = rest.split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if line and not line.startswith((" ", "\t", ")")):
            break
        out.append(line)
    return "\n".join(out)


class TestTheBrowserHandsOverTheWholeAccess(unittest.TestCase):

    def setUp(self):
        self.js = _read("public", "js", "controllers", "bank_refresh.js")
        self.py = _read("utils", "client.py")

    def test_the_run_starts_a_group_not_an_account(self):
        self.assertIn("kefiya.utils.client.start_fetch_group", self.js)
        self.assertIn("fetchWholeAccess(mine, btn, total)", self.js)

    def test_the_endpoint_returns_before_the_bank_does(self):
        """Thirty accounts is minutes of talking. Held open as one request it
        is a gateway timeout, and a progress bar that cannot move."""
        body = _py_function(self.py, "start_fetch_group")
        self.assertIn("frappe.enqueue(", body)
        self.assertIn('queue="long"', body)
        self.assertIn("timeout=3600", body,
                      "The default 300s kills the run mid-access.")

    def test_the_worker_is_gated_before_anything_is_queued(self):
        """A caller without write rights must be refused where they can see
        it, not in a worker whose failure never reaches them."""
        body = _py_function(self.py, "start_fetch_group")
        self.assertIn("frappe.has_permission", body)
        # The calls, not the prose: the docstring names enqueue before either
        # of them appears, so a bare "enqueue" matched the wrong position and
        # the ordering was never actually checked.
        self.assertLess(body.index("frappe.has_permission"),
                        body.index("frappe.enqueue("))

    def test_every_account_is_reported_as_it_lands(self):
        self.assertIn("kefiya_fetch_progress", self.py)
        self.assertIn("kefiya_fetch_progress", self.js)
        self.assertIn("kefiya_fetch_group_done", self.py)
        self.assertIn("kefiya_fetch_group_done", self.js)

    def test_a_realtime_hiccup_does_not_take_down_the_fetch(self):
        """The bookings are already written by then. A panel line is worth
        less than the run."""
        body = _py_function(self.py, "_say")
        self.assertIn("except Exception:", body)
        self.assertIn("pass", body)


class TestTheReleaseStopsTheAccess(unittest.TestCase):
    """An access holds ONE dialog. Fetching on past a parked release opens a
    second and overwrites the challenge the release belongs to."""

    def setUp(self):
        self.py = _read("utils", "client.py")
        self.js = _read("public", "js", "controllers", "bank_refresh.js")

    def test_the_group_stops_at_a_parked_release(self):
        body = _py_function(self.py, "fetch_group")
        self.assertIn("if held:", body)
        self.assertIn("_not_attempted(held)", body)
        self.assertIn('.get("tan_required")', body)

    def test_what_was_skipped_says_which_account_it_waits_on(self):
        body = _py_function(self.py, "_not_attempted")
        self.assertIn("{0}", body)
        self.assertIn("tan_required", body,
                      "Marked so the retry link picks it up together with the "
                      "account it is waiting on.")

    def test_the_rule_exists_exactly_once(self):
        """It used to exist twice: once in fetch_group, once in an
        account-by-account fallback in the browser -- two implementations of
        one rule, in two languages, with two translated sentences. The one
        that gets forgotten is whichever is edited second.

        The fallback is gone. The client has no copy to drift from.
        """
        self.assertNotIn("fetchOneByOne", self.js)
        self.assertNotIn("held = ln", self.js)
        body = _py_function(self.py, "fetch_group")
        self.assertIn("if held:", body)


class TestNothingWaitsForever(unittest.TestCase):

    def setUp(self):
        self.js = _read("public", "js", "controllers", "bank_refresh.js")

    def test_a_silent_worker_is_given_up_on(self):
        """A worker that is killed publishes no closing event. Without this
        the panel sits at "fetching" until the page is reloaded."""
        self.assertIn("GROUP_SILENCE_MS", self.js)
        self.assertIn("clearTimeout(timer)", self.js)

    def test_what_never_reported_is_named_rather_than_dropped(self):
        self.assertIn("recorded(ln)", self.js)
        self.assertIn("The worker stopped reporting", self.js)

    def test_a_failed_start_names_every_account_it_could_not_reach(self):
        """No second fetch path to fall back to, so the honest answer is the
        one every other server error already gets. Silence would leave the bar
        short of its total for good."""
        # Both exits, counted -- not just the name. "failAll(logins," also
        # matches the definition, so asserting it once passed with every call
        # site deleted. That is the second time this file's assertions matched
        # a def instead of a call; the count is what stops it.
        self.assertEqual(self.js.count("failAll(logins,"), 3,
                         "One definition and both exits: no start, and a "
                         "start the server refused.")
        self.assertIn("failAll(logins, kefiya.call_error(r)", self.js)


class TestOneReaderForOneSummary(unittest.TestCase):

    def test_both_paths_read_the_summary_through_the_same_helper(self):
        """Two copies of this drifted once already: len() of the result dict
        counted its two keys, so every account in the run reported "2 neu"."""
        js = _read("public", "js", "controllers", "bank_refresh.js")
        self.assertEqual(
            js.count("run.tot += (t.new_count || 0);"), 1,
            "The worker path and the fallback must read one summary reader.")
        self.assertIn("noteResult(ln,", js)


class TestOneReleaseDoesNotBecomeALoop(unittest.TestCase):
    """A run that continues itself must not be able to start another one.

    What happened without this: the TAN box reported the same way whether the
    release had been accepted or refused, and the caller read that as
    "released, carry on". A refused release started a fetch, the bank asked
    again, the box came back -- and the user confirmed their way around that
    circle for as long as they were willing to. Accounts were fetched over and
    over.
    """

    def setUp(self):
        self.js = _read("public", "js", "controllers", "bank_refresh.js")
        self.box = _read("public", "js", "controllers", "tan_prompt.js")

    def test_the_box_says_whether_it_was_accepted(self):
        self.assertIn("if (done) done(true);", self.box)
        self.assertIn("if (done) done(false);", self.box)

    def test_a_refused_release_arms_nothing(self):
        self.assertIn("function (ok) {", self.js)
        self.assertIn("if (!ok) return;", self.js)

    def test_a_continuation_cannot_continue_itself(self):
        """Even an accepted release can come back needing another one. One
        automatic continuation per run the user started; what is still
        unfetched stays behind the link that exists for it."""
        self.assertIn("if (run && run.resumed) return;", self.js)
        self.assertIn("resumed: true", self.js)
        self.assertIn("resumed: !!options.resumed", self.js)
