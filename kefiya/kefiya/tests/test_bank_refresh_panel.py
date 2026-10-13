# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A failure during the fetch has to be visible during the fetch.

The run panel showed a bar and a percentage while it worked, and offered its
log only once everything had finished. A collective fetch takes minutes, so an
access that broke in the first minute stayed invisible for the rest of them --
and by the time the log could be opened, the run had long moved past it.
"""

import os
import re
import unittest


def _source(name="bank_refresh.js"):
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "public", "js", "controllers", name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class TestTheLogIsReachableWhileTheRunGoes(unittest.TestCase):

    def test_the_link_no_longer_waits_for_the_run_to_end(self):
        source = _source()
        self.assertNotIn(
            'links = "";\n        if (!run.busy) {', source,
            "That guard is exactly what hid the log until the end.")
        self.assertIn("if (run.log && run.log.length) {", source,
                      "The log is offered as soon as there is one entry.")

    def test_refresh_view_stays_an_end_of_run_action(self):
        """Refreshing the surrounding view mid-run would fight the fetch that
        is still writing to it."""
        source = _source()
        self.assertIn("if (!run.busy && run.onRefreshView) {", source)


class TestFailuresAppearAsTheyHappen(unittest.TestCase):

    @staticmethod
    def _live_block():
        """The body of liveProblems(), up to the function that follows it."""
        tail = _source().split("function liveProblems()")[1]
        end = tail.find("\n    function ")
        return tail[:end if end > 0 else len(tail)]

    def test_the_panel_renders_a_live_problem_list(self):
        source = _source()
        self.assertIn("function liveProblems()", source)
        self.assertIn("+ liveProblems() +", source,
                      "Declared but never rendered would be worse than not "
                      "written at all.")

    def test_it_shows_failures_and_awaited_releases(self):
        """A parked release is not an error, but it is equally something the
        person watching needs to act on now rather than later."""
        block = self._live_block()
        self.assertIn('e.state === "err"', block)
        self.assertIn('e.state === "tan"', block)

    def test_the_list_is_bounded(self):
        """Thirty broken accesses must not push the rest of the page away."""
        block = self._live_block()
        self.assertIn("slice(-5)", block)
        self.assertIn("and {0} more", block,
                      "What was dropped has to be said, or five failures and "
                      "thirty look the same.")

    def test_every_access_name_and_reason_is_escaped(self):
        """Both come from outside: the login name is user data, the reason is
        a server message."""
        block = self._live_block()
        self.assertIn("esc(e.ln)", block)
        self.assertIn("esc(String(shortProblem(e))", block)

    def test_the_reason_prefers_what_the_server_actually_said(self):
        source = _source()
        block = source.split("function shortProblem(")[1][:600]
        self.assertLess(block.index("entry.detail"),
                        block.index('l.kind === "err"'),
                        "The server's own reason beats a reconstructed one.")


class TestAnOpenLogKeepsUp(unittest.TestCase):
    """The log used to be a snapshot of the moment it was opened, which during
    a running fetch is the least interesting moment there is."""

    def test_the_dialog_is_held_on_to(self):
        source = _source()
        self.assertIn("run.logDialog = dlg;", source)
        self.assertIn("logDialog: null,", source,
                      "The run state must declare it, or the first render "
                      "reads an undefined property.")

    def test_render_refreshes_it_while_it_is_visible(self):
        source = _source()
        self.assertIn("if (run.logDialog && run.logDialog.$wrapper", source)
        self.assertIn("logBody()", source)

    def test_it_is_released_when_the_dialog_closes(self):
        """Holding a hidden dialog would keep re-rendering into nothing."""
        source = _source()
        self.assertIn("hidden.bs.modal", source)
        self.assertIn("run.logDialog = null;", source)

    def test_the_body_is_built_once_and_reused(self):
        """Two builders would drift, and the live one is the one nobody
        checks."""
        source = _source()
        self.assertEqual(source.count("function logBody()"), 1)
        self.assertGreaterEqual(len(re.findall(r"logBody\(\)", source)), 3)
