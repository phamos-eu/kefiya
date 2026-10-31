# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Which account kinds must not be counted as cash. No frappe, on purpose.

The rule is one comparison, and it is worth having where it can be read and
tested without a site -- the same reason duplicate_rule and fints_response
stand apart from the code that queries for them.
"""

#: The ledger type that makes a figure count as cash: Account.account_type.
LIQUID = "Bank"

#: Account kinds that are not money on a bank account, whatever the books say.
#:
#: Spelled out rather than derived from account_kind.PAYMENT_KINDS, because
#: this is a policy about the BOOKS and that one is a policy about payments.
#: They agree today; a kind added to one is not automatically right for the
#: other, and a derivation would hide that decision instead of asking for it.
#: (test_account_classification keeps the two lists honest with each other.)
#:
#: Credit Card is deliberately absent. It is a payment account, and whether a
#: card sits in the books as a bank account or as a liability is a matter of
#: convention rather than of fact -- flagging it would be an opinion dressed
#: up as a finding.
NOT_LIQUID = (
    "Loan",
    "Guarantee / Credit Line",
    "Cooperative Shares",
    "Securities Account",
)


def is_misclassified(kind, account_type):
    """True where the ledger counts as cash something that is not cash.

    :param kind: one of account_kind.KINDS
    :param account_type: the Account's account_type, or None
    :return: bool
    """
    return kind in NOT_LIQUID and account_type == LIQUID


def is_stated(kind):
    """True where the account kind is an answer rather than a default.

    account_kind's default is "Current Account", and kind_of() returns it for
    every login nobody has configured -- indistinguishable from a real giro
    account. So the field is evidence in one direction only: a kind that is
    not a payment account was chosen by somebody, a payment account may just
    be the field lying still.

    This is not theory. A grouping built on "the account kind wins, always"
    moved eight Volksbank Darlehen into the payment accounts -- and into the
    liquidity -- because their logins carry the untouched default. Same shape
    as duplicate_rule: an absent answer is not an answer.

    :param kind: one of account_kind.KINDS, or None
    :return: bool
    """
    return kind in NOT_LIQUID
