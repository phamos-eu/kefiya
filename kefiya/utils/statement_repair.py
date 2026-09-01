# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Undoing an import that put the same booking on several accounts.

What happened, on 21.05.2026: a run created 211,340 Bank Transactions, and
29,700 of those bookings exist two, three, four or five times over, on
different accounts. An employee found it the way such things are always found
-- she saw a supplier paid three times, once from an account that could not
have paid him.

The shape of it is not "every row landed on one account". It is a cluster:

    Sofienstr. Baukonto Sparkasse   37,418 drafts, 29,693 of them copies
    Sofienstr. Mietkonto Sparkasse  19,804 drafts, every one a copy
    Brilu KG Mietkonto Sparkasse     9,291 drafts, every one a copy
    axessio Hausverwaltung x2       10,959 drafts, every one a copy
    ... and four smaller ones

while the largest accounts -- Volksbank Ch & A Finkeissen with 54,647, SKL
Sparkasse with 31,604, Privatkonto with 18,995 -- have no duplicates at all.
So a blanket deletion of the run would throw away some 130,000 rows that are
probably right, and that is why this module exists instead.

WHICH COPY IS THE WRONG ONE

The rows carry nothing that answers it. Three copies of one payment are
identical to the character -- same date, same amount, same reference "9310",
same purpose line -- and differ only in the account they were filed under and
in a generated id. The source files are gone: no import record, no
attachment. So the answer has to come from somewhere else, and there is
exactly one trustworthy source in the database.

The 6,067 SUBMITTED Bank Transactions. Those came from live FinTS fetches,
one account at a time, and nobody has ever doubted them. They say which
accounts really do business with which payees.

Two things follow, and the first is not a judgement call at all:

    The Baukonto has never had a single submitted transaction. Not one. An
    account no fetch has ever returned a booking for did not make 29,693
    payments that also appear elsewhere. Its copies go, and every booking
    survives on the account that does have a history.

    For what is left -- mostly Brilu KG Mietkonto against Sofienstrasse
    Mietkonto -- the payee decides: whichever of the two accounts has
    submitted transactions with that counterparty keeps the copy. Vrban:
    74 real payments from Brilu, 2 from Sofienstrasse. Brilu keeps them.

That second rule is an inference and this module says so wherever it reports.
Where the evidence is silent it falls back to the account with the larger
submitted history, which is weaker still, and it counts those separately so
the reader can see how much of the result rests on what.

WHAT IT WILL NOT DO

    It never deletes the last copy of a booking.
    It never touches a submitted or cancelled document.
    It never touches a row that has been reconciled or allocated.
    It only looks at the window it is given, and it plans before it acts.
"""

import frappe
from frappe import _
from frappe.utils import cint

from kefiya.utils.duplicate_rule import (
    BY_ACCOUNT, BY_PAYEE, UNDECIDED, fingerprint, pick_home,
)
from kefiya.utils import rerun_rule

#: How many documents are deleted between commits. Small enough that an
#: interruption leaves a finished prefix rather than a rolled-back hour.
BATCH = 200


def _payee_key(value):
    """A counterparty name reduced for comparison.

    The same rule payee_check uses, borrowed rather than rewritten: two
    spellings of one supplier must not look like two suppliers here while
    they look like one everywhere else in the app.
    """
    from kefiya.utils.payee_check import normalise_name

    return normalise_name(value)


def real_history():
    """What the submitted transactions say about accounts and payees.

    The only ground truth available. These came from live fetches, one
    account at a time.

    :return: ({(payee key, account): count}, {account: count})
    """
    by_payee = {}
    by_account = {}
    for row in frappe.get_all(
            "Bank Transaction", filters={"docstatus": 1},
            fields=["bank_account", "bank_party_name"],
            limit_page_length=0):
        account = row.get("bank_account")
        if not account:
            continue
        by_account[account] = by_account.get(account, 0) + 1
        key = _payee_key(row.get("bank_party_name"))
        if key:
            by_payee[(key, account)] = by_payee.get((key, account), 0) + 1
    return by_payee, by_account


def _untouched(row):
    """A draft nobody has worked on yet.

    Reconciliation is the point at which a booking stops being an import
    artefact and starts being somebody's work. This module does not delete
    anybody's work.
    """
    return (row.get("docstatus") == 0
            and not row.get("allocated_amount")
            and (row.get("status") or "Pending") in ("Pending", "Unreconciled"))


#: Everything either planner reads. One list, because a field that only one
#: of them fetches is a field the other silently treats as absent.
GELESENE_FELDER = [
    "name", "bank_account", "date", "withdrawal", "deposit", "description",
    "bank_party_name", "bank_party_iban", "docstatus", "allocated_amount",
    "status", "creation",
]


def _grouped(created_from, created_to, only_account, identity):
    """The untouched drafts of one import run, grouped by identity.

    Both planners need exactly this and used to carry their own copy of it --
    same query, same window filter, same _untouched skip, same only_account
    check, differing in one field list and one grouping key. The two copies
    had already drifted: one fetched bank_party_iban and the other did not.

    :param identity: what makes two rows the same booking, as a callable
    :return: (rows read, {identity: [rows]}, how many were skipped)
    """
    rows = frappe.get_all(
        "Bank Transaction",
        filters={"creation": [">=", created_from], "docstatus": 0},
        fields=GELESENE_FELDER, limit_page_length=0)
    rows = [r for r in rows if str(r["creation"]) < str(created_to)]

    groups = {}
    skipped = 0
    for row in rows:
        if not _untouched(row):
            skipped += 1
            continue
        if only_account and row["bank_account"] != only_account:
            continue
        groups.setdefault(identity(row), []).append(row)
    return rows, groups, skipped


def plan(created_from, created_to, only_account=None):
    """What would be deleted, and why. Reads only.

    :param created_from: start of the import run, e.g. "2026-05-21"
    :param created_to: end, exclusive
    :return: {"delete": [names], "kept", "groups", "reasons", "skipped"}
    """
    rows, groups, skipped = _grouped(
        created_from, created_to, only_account, fingerprint)

    by_payee, by_account = real_history()

    doomed = []
    reasons = {BY_PAYEE: 0, BY_ACCOUNT: 0, UNDECIDED: 0}
    kept = 0
    multiple = 0
    for copies in groups.values():
        accounts = {r["bank_account"] for r in copies}
        if len(accounts) < 2:
            continue
        multiple += 1

        keeper, why = pick_home(
            copies, _payee_key((copies[0] or {}).get("bank_party_name")),
            by_payee, by_account)
        reasons[why] += 1
        if keeper is None:
            # Undecided: nothing is deleted, so nothing can be lost.
            continue

        kept += 1
        for row in copies:
            # By ACCOUNT, not by document. Where the booking sits twice on the
            # SAME account, both rows stay -- and that is not laziness, it is
            # the only defensible answer.
            #
            # 3,079 bookings in this run are doubled within one account, and
            # nothing distinguishes the two rows. "Basislastschrift Jack n
            # Jill Lounge, 3.77, 16.02.2026" appears twice on an account that
            # has no cross-account duplicates at all. That is either an
            # importer writing a line twice or a card charged twice in one
            # day, and no field here tells them apart. Keeping one row per
            # document would have deleted 18 of them as a side effect of a
            # decision that was never about them -- losing a real payment,
            # which is the one thing this module promises not to do.
            if row["bank_account"] != keeper["bank_account"]:
                doomed.append(row["name"])

    return {"delete": doomed, "kept": kept, "groups": multiple,
            "reasons": reasons, "skipped": skipped, "read": len(rows)}


@frappe.whitelist()
def plan_duplicates(created_from, created_to, only_account=None):
    """The plan, as JSON, without deleting anything."""
    _may_repair()
    answer = plan(created_from, created_to, only_account)
    # The names are the bulk of it and the caller does not need 14,000 of
    # them to decide; the counts are what a person reads.
    answer["delete_count"] = len(answer["delete"])
    answer["delete"] = answer["delete"][:20]
    return answer


def _carry_out(answer, created_from, confirm, limit, was):
    """Delete what a plan named. The only place that deletes anything.

    Both repairs -- the same booking under several accounts, and the same
    booking twice on one -- used to carry their own copy of this: thirty
    lines each, differing in which planner they called, the wording of one
    log line, and one extra key in the result. Everything that makes the
    deletion safe existed twice, character for character, in a path whose
    whole job is to destroy documents. A correction to one would have missed
    the other in silence.

    The promises, and they hold for every caller:

        it never deletes the last copy -- the planner decides that
        it never touches a submitted or cancelled document
        it never touches a row that has been reconciled or allocated
        it confirms by COUNT, so a stale plan deletes nothing
        it deletes softly, so a wrong rule can be undone
        it commits in blocks, so an interruption leaves a finished prefix

    :param answer: what a planner returned
    :param was: what is being removed, for the log line
    :return: {"deleted", "failed", "remaining", "reasons"}
    """
    doomed = answer["delete"]
    if cint(confirm) != len(doomed):
        frappe.throw(_(
            "The plan names {0} documents, the confirmation says {1}."
            " Nothing was deleted -- run the plan again and confirm the"
            " number it reports."
        ).format(len(doomed), cint(confirm)))

    if limit:
        doomed = doomed[:cint(limit)]

    frappe.logger("kefiya").info(
        "Kefiya repair: deleting %s %s from the %s run, requested by %s",
        len(doomed), was, created_from, frappe.session.user)

    deleted = 0
    failed = []
    for index, name in enumerate(doomed, 1):
        try:
            # Not delete_permanently. Frappe keeps a Deleted Document with
            # the full JSON of every row it removes, and that archive is the
            # only way back here: the source files of this run are gone, so a
            # permanent delete would make a wrong rule unrecoverable.
            frappe.delete_doc("Bank Transaction", name,
                              ignore_permissions=False)
            deleted += 1
        except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
            failed.append({"name": name, "reason": str(exc)[:200]})
        if index % BATCH == 0:
            frappe.db.commit()  # noqa: DAR101 -- a finished prefix, not a lock
    frappe.db.commit()

    return {"deleted": deleted, "failed": failed,
            "remaining": len(answer["delete"]) - deleted,
            "reasons": answer["reasons"]}


@frappe.whitelist()
def delete_duplicates(created_from, created_to, confirm=None,
                      only_account=None, limit=None):
    """Delete the copies that sit on the wrong account. See _carry_out."""
    _may_repair()
    return _carry_out(
        plan(created_from, created_to, only_account),
        created_from, confirm, limit, "duplicate bank transactions")


def plan_reruns(created_from, created_to, only_account=None,
                seconds_apart=rerun_rule.SECONDS_APART):
    """The same booking twice on ONE account: what a second pass wrote.

    The case the module above declines, and it declines it correctly on the
    evidence it uses -- the two rows are identical in every field it reads.
    The field it does not read is ``creation``, and on this run that is what
    separates a file read twice from a booking the source listed twice. See
    rerun_rule.

    Reads only. Returns the surplus rows AND the ones it will not touch, so
    the pairs nobody can decide are a list somebody looks at rather than a
    silence.

    :return: {"delete", "review", "groups", "reasons", "skipped", "read"}
    """
    rows, groups, skipped = _grouped(
        created_from, created_to, only_account, rerun_rule.identity)

    doomed = []
    review = []
    reasons = {rerun_rule.FROM_A_RERUN: 0, rerun_rule.SAME_PASS: 0,
               rerun_rule.UNDECIDED: 0}
    multiple = 0
    for copies in groups.values():
        if len(copies) < 2:
            continue
        multiple += 1
        surplus, why = rerun_rule.surplus_of(copies, seconds_apart)
        reasons[why] += 1
        if surplus:
            doomed.extend(row["name"] for row in surplus)
        else:
            # Left alone, and named. A duplicate this rule cannot decide is
            # still a duplicate somebody has to look at -- and an accountant
            # who is told "3,000 were removed" and nothing else has no way
            # to know what is left.
            review.append({
                "konto": copies[0]["bank_account"],
                "datum": str(copies[0]["date"]),
                "eingang": copies[0]["deposit"],
                "ausgang": copies[0]["withdrawal"],
                "zahler": copies[0]["bank_party_name"],
                "zweck": (copies[0]["description"] or "")[:120],
                "zeilen": [row["name"] for row in copies],
                "grund": why,
            })

    return {"delete": doomed, "review": review, "groups": multiple,
            "reasons": reasons, "skipped": skipped, "read": len(rows)}


@frappe.whitelist()
def plan_rerun_duplicates(created_from, created_to, only_account=None):
    """The plan, as JSON, without deleting anything."""
    _may_repair()
    answer = plan_reruns(created_from, created_to, only_account)
    answer["delete_count"] = len(answer["delete"])
    answer["review_count"] = len(answer["review"])
    answer["delete"] = answer["delete"][:20]
    answer["review"] = answer["review"][:50]
    return answer


@frappe.whitelist()
def delete_rerun_duplicates(created_from, created_to, confirm=None,
                            only_account=None, limit=None):
    """Delete the later copy of each booking a second pass wrote.

    See _carry_out for the promises. The one thing this adds is the count of
    pairs it deliberately did NOT decide -- an accountant told "2,508 were
    removed" and nothing else has no way to know what is left.
    """
    _may_repair()
    answer = plan_reruns(created_from, created_to, only_account)
    result = _carry_out(answer, created_from, confirm, limit,
                        "re-run copies")
    result["review_count"] = len(answer["review"])
    return result


def _may_repair():
    """Deleting thousands of documents is not an ordinary write."""
    frappe.only_for("System Manager")
    frappe.has_permission("Bank Transaction", ptype="delete", throw=True)
