# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Which copy of a booking is the real one.

Its own module, with no frappe in it, for the reason the dialog predicates
have theirs: this is the part that decides whether 14,492 documents are
deleted, and a rule that can only be exercised against a live site is a rule
nobody exercises. Everything here is a pure function over dicts.

The question it answers, and why it is hard: an import filed the same payment
under two or three accounts, and the rows are identical to the character --
same date, same amount, same reference, same purpose. Nothing in a row says
where it belongs. The source files are gone.

So the evidence comes from elsewhere: the transactions somebody really
fetched, one account at a time, over a live bank connection. Those say which
account does business with which payee. Three rules, tried in order, and they
are deliberately of decreasing strength so that the caller can report how
much of a result rests on which:

    payee      one of the accounts has really paid this counterparty, the
               others have not. The strong case, and the common one.
    account    nothing known about the payee, but one account has a real
               history and the other has none at all. This is what settles
               an account that no fetch has ever returned a booking for.
    undecided  two accounts, equally unknown. Nothing is deleted. Putting a
               payment on an account for no reason is worse than leaving a
               duplicate that is visibly a duplicate.
"""

#: How much of a purpose line is compared. The importer wrapped it
#: differently in places, and a date, an amount and a reference number
#: already identify a payment well before the eightieth character.
PURPOSE_CUT = 80

BY_PAYEE = "payee"
BY_ACCOUNT = "account"
UNDECIDED = "undecided"


def fingerprint(row):
    """What makes two rows the same booking, regardless of account."""
    return (
        str(row.get("date") or ""),
        str(row.get("withdrawal") or 0),
        str(row.get("deposit") or 0),
        (row.get("description") or "")[:PURPOSE_CUT],
    )


def pick_home(copies, payee_key, by_payee, by_account):
    """Which of these copies to keep, and which rule decided it.

    :param copies: rows of one booking, differing in bank_account
    :param payee_key: the normalised counterparty name, or an empty value
    :param by_payee: {(payee key, account): how many real transactions}
    :param by_account: {account: how many real transactions}
    :return: (the row to keep or None, one of BY_PAYEE/BY_ACCOUNT/UNDECIDED)
    """
    accounts = sorted({row.get("bank_account") for row in copies})
    if len(accounts) < 2:
        return None, UNDECIDED

    if payee_key:
        decided = _clear_winner(
            [(by_payee.get((payee_key, a), 0), a) for a in accounts])
        if decided:
            return _row_on(copies, decided), BY_PAYEE

    decided = _clear_winner([(by_account.get(a, 0), a) for a in accounts])
    if decided:
        return _row_on(copies, decided), BY_ACCOUNT

    return None, UNDECIDED


def _clear_winner(scored):
    """The one account ahead of every other, or nothing.

    Two conditions, and both matter. A score of zero is not evidence, it is
    the absence of evidence -- an account nobody has ever fetched must not
    win because the others are equally silent. And a tie is not a winner:
    picking the first alphabetically would be a coin toss wearing a rule's
    clothes.
    """
    scored = sorted(scored, reverse=True)
    if not scored or scored[0][0] <= 0:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


def _row_on(copies, account):
    for row in copies:
        if row.get("bank_account") == account:
            return row
    return None
