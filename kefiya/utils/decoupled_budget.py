# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""How long a run waits for a release given in the banking app.

Two ceilings, and the shorter one decides.

The bank names one: how many times it will answer a status query and how long
to leave between two. The job names the other, and that one was being ignored
-- the scheduled import polled for a release nobody was there to give and was
killed at the job timeout inside its own sleep, every morning::

    rq.timeouts.JobTimeoutException: Task exceeded maximum timeout value
        (300 seconds)
      File "kefiya/utils/fints_controller.py", line 1273, in _await_release
        time.sleep(pause)

No frappe import and no bank. What this decides is how long somebody stands in
front of a spinner and whether a background job survives the wait, and both
should be readable -- and testable -- without a site. The same reason
vop_rule, tan_challenge and duplicate_rule stand apart from the code that
queries for them.
"""

from datetime import datetime

#: Nobody watches a browser for longer than this, whatever the bank allows.
DECOUPLED_WAIT_CEILING_SECONDS = 300

#: What a run keeps back for itself when it waits inside a background job.
#: The wait is never the last thing that happens: the released command still
#: has to come back, the bookings still have to be written, the dialog still
#: has to close. Spending the job's last second on the wait means the work the
#: release unlocked is killed a moment after it was authorised.
JOB_BUDGET_RESERVE_SECONDS = 45


def job_budget_seconds(job=None, now=None, reserve=JOB_BUDGET_RESERVE_SECONDS):
    """How many seconds this background job has left to spend on waiting.

    A worker kills the job at its timeout, mid-sleep, with a
    JobTimeoutException -- and it did, every morning: the scheduled import
    reached a decoupled challenge, settled in to poll for it, and died at 300
    seconds inside the sleep. Nothing had gone wrong with the bank; the run
    simply spent its whole allowance waiting for a release nobody was there
    to give.

    :return: seconds still safely available, or None when not inside a job
             (a foreground request has no such deadline) -- and 0 when the
             allowance is already spent.
    """
    if job is None:
        try:
            from rq import get_current_job
            job = get_current_job()
        except Exception:
            return None
    if job is None:
        return None

    try:
        timeout = float(getattr(job, "timeout", 0) or 0)
    except Exception:
        return None
    if timeout <= 0:
        # RQ spells "no limit" as -1 or None. Nothing to budget against.
        return None

    started = getattr(job, "started_at", None)
    if started is None:
        spent = 0.0
    else:
        try:
            now = now or datetime.utcnow()
            if getattr(started, "tzinfo", None) is not None:
                started = started.replace(tzinfo=None)
            spent = (now - started).total_seconds()
        except Exception:
            return None

    return max(0.0, timeout - spent - reserve)


def decoupled_wait(parameters, fallback_pause, fallback_total, budget=None):
    """How long to wait for a release, and how often to ask.

    The bank says both in its TAN parameters: how many times it will answer a
    status query (decoupled_max_poll_number) and how long to leave between two
    (wait_before_next_poll). Ignoring them and waiting a flat two minutes meant
    stopping at 40 % of what this bank actually allows -- 150 polls, two
    seconds apart, is five minutes.

    The bank's ceiling is not the only one. A run inside a background job has
    a deadline of its own, and it is the shorter of the two that decides --
    pass what is left of it as ``budget``.

    Frappe-free and its own function: what it decides is how long somebody
    stands in front of a spinner, and that should be readable without a bank.

    :return: (seconds between polls, seconds in total)
    """
    pause = fallback_pause
    total = fallback_total
    try:
        said_pause = float(getattr(parameters, "wait_before_next_poll", 0) or 0)
        if said_pause > 0:
            pause = max(1.0, min(said_pause, 10.0))
    except Exception:
        pass
    try:
        polls = int(getattr(parameters, "decoupled_max_poll_number", 0) or 0)
        if polls > 0:
            total = max(fallback_total, polls * pause)
    except Exception:
        pass
    total = min(total, DECOUPLED_WAIT_CEILING_SECONDS)
    if budget is not None:
        total = min(total, max(0, budget))
    return pause, total
