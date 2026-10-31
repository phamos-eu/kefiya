# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A run that nobody is watching must not wait for a release.

The scheduled import met a decoupled challenge, settled in to poll for it,
and was killed at the job timeout inside its own sleep::

    rq.timeouts.JobTimeoutException: Task exceeded maximum timeout value
        (300 seconds)
      File "kefiya/utils/fints_tan_session.py", line 1273, in _await_release
        time.sleep(pause)

Nothing had gone wrong with the bank. At six in the morning there is no
browser and no phone: the wait could not produce a release, it could only
spend the whole allowance and take the run down with it. Every morning.

Two rules come out of that, and both are tested here:

  - Nobody watching, no waiting at all. Park the challenge and end tidily.
  - When somebody IS watching, the wait is bounded by what the job has left,
    not only by what the bank allows.
"""

import os
import unittest

from kefiya.utils.decoupled_budget import (DECOUPLED_WAIT_CEILING_SECONDS,
                                           JOB_BUDGET_RESERVE_SECONDS,
                                           decoupled_wait, job_budget_seconds)


class Parameters:
    wait_before_next_poll = 2
    decoupled_max_poll_number = 150


class Job:
    """The shape rq.get_current_job() answers with."""

    def __init__(self, timeout, started_at=None):
        self.timeout = timeout
        self.started_at = started_at


class TestWhatTheJobHasLeft(unittest.TestCase):

    def test_a_fresh_job_has_its_timeout_less_the_reserve(self):
        from datetime import datetime

        now = datetime(2026, 9, 1, 6, 0, 0)
        self.assertEqual(
            job_budget_seconds(Job(300, now), now=now),
            300 - JOB_BUDGET_RESERVE_SECONDS)

    def test_time_already_spent_comes_off(self):
        from datetime import datetime

        started = datetime(2026, 9, 1, 6, 0, 0)
        now = datetime(2026, 9, 1, 6, 3, 0)  # 180s in
        self.assertEqual(
            job_budget_seconds(Job(300, started), now=now),
            300 - 180 - JOB_BUDGET_RESERVE_SECONDS)

    def test_a_spent_allowance_is_zero_not_negative(self):
        """A negative budget would read as "no budget given" downstream."""
        from datetime import datetime

        started = datetime(2026, 9, 1, 6, 0, 0)
        now = datetime(2026, 9, 1, 6, 10, 0)
        self.assertEqual(job_budget_seconds(Job(300, started), now=now), 0.0)

    def test_no_job_means_no_deadline(self):
        """A foreground request is not killed at a job timeout."""
        self.assertIsNone(job_budget_seconds(Job(0)))
        self.assertIsNone(job_budget_seconds(Job(-1)))
        self.assertIsNone(job_budget_seconds(Job(None)))

    def test_it_never_raises(self):
        self.assertIsNone(job_budget_seconds(object()))
        self.assertIsNone(job_budget_seconds(Job(300, "not a datetime")))


class TestTheWaitRespectsBoth(unittest.TestCase):

    def test_without_a_budget_the_bank_decides(self):
        """150 polls two seconds apart, capped by the ceiling."""
        self.assertEqual(decoupled_wait(Parameters(), 2, 120),
                         (2, DECOUPLED_WAIT_CEILING_SECONDS))

    def test_a_shorter_budget_wins(self):
        """This is the regression: the bank allowed 300s, the job had 255."""
        self.assertEqual(decoupled_wait(Parameters(), 2, 120, budget=255)[1],
                         255)

    def test_a_longer_budget_does_not_extend_the_ceiling(self):
        self.assertEqual(
            decoupled_wait(Parameters(), 2, 120, budget=100000)[1],
            DECOUPLED_WAIT_CEILING_SECONDS)

    def test_a_spent_budget_means_no_wait(self):
        self.assertEqual(decoupled_wait(Parameters(), 2, 120, budget=0)[1], 0)

    def test_a_negative_budget_is_no_wait_not_a_long_one(self):
        self.assertEqual(decoupled_wait(Parameters(), 2, 120, budget=-50)[1],
                         0)

    def test_the_old_calls_still_answer_the_old_way(self):
        """Every existing caller passes three arguments and must be unaffected."""
        self.assertEqual(decoupled_wait(None, 2, 120), (2, 120))
        self.assertEqual(decoupled_wait(object(), 2, 120), (2, 120))


def _source(*parts):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return handle.read()


def _await_release_code():
    """The body of _await_release, docstring stripped."""
    method = _source("utils", "fints_tan_session.py").split(
        "def _await_release(self, challenge):")[1]
    return method.split('"""', 2)[2]


class TestNobodyWatchingMeansNoWaiting(unittest.TestCase):

    def test_it_gives_up_before_it_sleeps(self):
        """Asserted as the return, and as coming before the sleep loop.

        A guard placed after the loop would read the same in a grep and
        would fix nothing.
        """
        body = _await_release_code()
        self.assertIn("if not self.interactive.enabled:", body)
        self.assertLess(body.index("self.interactive.enabled"),
                        body.index("time.sleep(pause)"))
        guard = body.split("if not self.interactive.enabled:")[1][:120]
        self.assertIn("return None", guard)

    def test_the_wait_is_asked_what_the_job_has_left(self):
        body = _await_release_code()
        self.assertIn("budget=job_budget_seconds()", body)

    def test_a_budget_too_small_for_one_ask_does_not_ask(self):
        body = _await_release_code()
        self.assertIn("if total < pause:", body)


if __name__ == "__main__":
    unittest.main()
