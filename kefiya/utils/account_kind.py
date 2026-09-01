# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What kind of account this is -- and what that means for its balance.

Not everything a bank shows under "accounts" is an account you can pay from,
and the single number the bank reports for it does not always mean the same
thing. Two entries of one customer make the point:

    Sofie KG Aval VB Kpf ...750    -1.409.672,26
    Sofie KG Aval VB Kpf ...769      -100.000,00

Those are Avale -- guarantee lines. The number is the line, not money on an
account. Nothing was ever booked there, nothing can be paid from there, and
counting a balance backwards over bookings that do not exist produces a column
of numbers that look like statements of fact and are not. The same holds for a
loan: the balance moves on the bank's schedule, not through a statement.

Cooperative shares are a third case, and a different one again. The two

    Dr. Alexander Finkeissen ...310    Geschaeftsanteile
    Maxi ...112                        Geschaeftsanteile

do hold an amount that is really there -- but it is held, not available, and it
is not the sum of any bookings either. So the amount is worth storing and worth
showing, and it has no business in a total of what can be paid out today.

So each login says what it is, and three questions are answered from that:

    keeps_a_running_balance()  -- may the balance be counted back over the
                                  bookings and written onto them?
    reports_a_credit_line()    -- is the number the bank states a line rather
                                  than a balance?
    counts_towards_liquidity() -- may it be added into a total of money that
                                  can be spent?

An account whose kind nobody has set counts as a payment account, which is what
every login was treated as before this existed: the classification can only
take things out of the balance logic, never silently put something new in.
"""

import frappe

# The values are stored in the database, so they are English like every other
# stored value in this app. What the user reads is the translation: the German
# wording lives in locale/de.po, next to the rest of it. A German value here
# would be data that changes meaning with the site language.
GIRO = "Current Account"
SAVINGS = "Savings"
CREDIT_CARD = "Credit Card"
LOAN = "Loan"
GUARANTEE = "Guarantee / Credit Line"
SHARES = "Cooperative Shares"
SECURITIES = "Securities Account"

KINDS = (GIRO, SAVINGS, CREDIT_CARD, LOAN, GUARANTEE, SHARES, SECURITIES)

#: Accounts whose balance is the sum of their bookings. Only for these does
#: counting backwards from today's balance describe anything real.
PAYMENT_KINDS = (GIRO, SAVINGS, CREDIT_CARD)

#: Accounts where the reported amount is a granted line, not money held.
CREDIT_LINE_KINDS = (GUARANTEE,)

#: Accounts a SEPA transfer can START from. Narrower than PAYMENT_KINDS,
#: because a credit card is a payment account that cannot originate one: the
#: card pays merchants, the settlement account pays the card.
#:
#: What made this necessary: the payer dropdown offered loans, guarantees and
#: securities accounts. Three filters were meant to prevent that -- the
#: Bank Account's account_type, a link to a Property Loan, and the bank's own
#: HIUPD capability list -- and all three missed, because account_type is not
#: maintained on those records, the loan link is not either, and HIUPD is
#: never fetched for an account nobody fetches. The kind was right on every
#: one of them the whole time; nothing asked.
TRANSFER_SOURCE_KINDS = (GIRO, SAVINGS)


def kind_of(kefiya_login):
    """The account kind of a login, defaulting to a payment account.

    :param kefiya_login: name of a Kefiya Login, or a document with the field
    :return: one of KINDS
    """
    if not kefiya_login:
        return GIRO

    value = None
    if isinstance(kefiya_login, str):
        # A field that has not been installed yet must not fail a fetch.
        if frappe.get_meta("Kefiya Login").has_field("account_kind"):
            value = frappe.db.get_value(
                "Kefiya Login", kefiya_login, "account_kind")
    else:
        value = getattr(kefiya_login, "account_kind", None)

    return value if value in KINDS else GIRO


def keeps_a_running_balance(kefiya_login):
    """May the balance be counted back over the bookings of this account?

    :return: True for a payment account, False for loan/guarantee/securities
    """
    return kind_of(kefiya_login) in PAYMENT_KINDS


def reports_a_credit_line(kefiya_login):
    """Does the number the bank states describe a line rather than a balance?"""
    return kind_of(kefiya_login) in CREDIT_LINE_KINDS


def counts_towards_liquidity(kefiya_login):
    """May this account's amount be added into a total of available money?

    A guarantee line is not money; a loan is not money that is there; and a
    cooperative share is money that is there and cannot be spent. All three
    belong in an overview, none of them belongs in its sum.
    """
    return kind_of(kefiya_login) in PAYMENT_KINDS


def can_send_transfers(kefiya_login):
    """May a SEPA transfer start from this account?

    The one question the payer dropdown has to ask. A loan, a guarantee, a
    share deposit and a securities account cannot send money; a credit card
    cannot either -- it is paid, it does not pay.
    """
    return kind_of(kefiya_login) in TRANSFER_SOURCE_KINDS


@frappe.whitelist()
def transfer_sources():
    """Every Kefiya Login a transfer may be entered against.

    The canonical answer, so a page does not have to reimplement it -- and so
    a page that asks gets the same list the app itself would use.

    get_list, not get_all: these are offered for selection, and an access the
    caller may not see must not appear in a dropdown.

    :return: [{"login", "bank_account", "company", "kind"}]
    """
    if not frappe.get_meta("Kefiya Login").has_field("account_kind"):
        # Before the field exists every login is a payment account, which is
        # what kind_of() answers anyway. Say so rather than return nothing.
        rows = frappe.get_list(
            "Kefiya Login", filters={"bank_account": ["is", "set"]},
            fields=["name", "bank_account", "company"], limit_page_length=0)
        return [{"login": r["name"], "bank_account": r["bank_account"],
                 "company": r.get("company"), "kind": GIRO} for r in rows]

    rows = frappe.get_list(
        "Kefiya Login",
        filters={"bank_account": ["is", "set"],
                 "account_kind": ["in", list(TRANSFER_SOURCE_KINDS)]},
        fields=["name", "bank_account", "company", "account_kind",
                "account_iban"],
        limit_page_length=0)

    # A disabled Bank Account is still a Bank Account; it is just not one an
    # order should be entered against.
    disabled = {r["name"] for r in frappe.get_all(
        "Bank Account", filters={"disabled": 1}, fields=["name"],
        limit_page_length=0)}

    refused = _accounts_that_cannot_pay()

    return [dict({"login": r["name"], "bank_account": r["bank_account"],
                  "company": r.get("company"), "kind": r.get("account_kind"),
                  "iban": r.get("account_iban")},
                 **account_standing(r["bank_account"]))
            for r in rows
            if r["bank_account"] not in disabled
            and r["bank_account"] not in refused]


#: Bank Account.account_type values that name a facility rather than an
#: account. They are German because that is what somebody typed into the field
#: on this instance; the kind above is the reliable answer and this is the
#: belt to its braces.
NOT_AN_ACCOUNT_TYPE = ("Darlehen", "Aval", "Collar", "Call")


def _accounts_that_cannot_pay():
    """Bank Accounts no transfer may start from, whatever their kind says.

    Two answers, and both were already being asked -- by the page, not by the
    app. The page is being taken apart, so they move here, where the one
    helper that answers "which accounts may pay" can apply them.

        the bank's own word   HIUPD said "transfer" is not allowed here
        the account type      somebody wrote "Darlehen" or "Aval" on it

    The page asked a third: whether a Property Loan names the account. That
    one is NOT carried over, and deliberately.

    It reads a field this app does not own, and the meaning of that field is
    not settled -- "the loan's own account" and "the account the loan is
    serviced from" are both plausible readings, and only the first makes the
    exclusion correct. Under the second reading, a company that pays its
    mortgage from its main giro account loses that giro account from every
    payer list, and the transfer form says "no account available" with no way
    to find out why. The rule cannot fire on anything BUT a giro or savings
    account, because account_kind has already removed the loans -- so its only
    possible effect is the false positive.

    And it earns nothing: on this instance every account it would remove is
    already removed by the bank's own refusal or by the account type. A rule
    whose upside is zero and whose downside is a payer list that cannot pay is
    not a belt, it is a hazard.

    get_all rather than get_list on purpose: what is wanted is WHICH accounts
    to take out. Nothing here is shown, so a reader without the right to see
    every Bank Account gets a stricter list, never a wider one.
    """
    refused = set()

    for row in frappe.get_all(
            "Kefiya Account Capability", parent_doctype="Bank Account",
            filters={"parenttype": "Bank Account", "capability": "transfer",
                     "allowed": 0},
            fields=["parent"], limit_page_length=0):
        refused.add(row["parent"])

    for row in frappe.get_all(
            "Bank Account",
            filters={"account_type": ["in", list(NOT_AN_ACCOUNT_TYPE)]},
            fields=["name"], limit_page_length=0):
        refused.add(row["name"])

    return refused


def _currency_of(account):
    """The currency of a ledger Account, or None. Never raises.

    None is a usable answer: the browser formats in the company's default
    currency when it is not told otherwise, and being told nothing beats
    being told wrong.
    """
    if not account:
        return None
    try:
        return frappe.get_cached_value("Account", account, "account_currency")
    except Exception:
        return None


@frappe.whitelist()
def account_standing_for(bank_account):
    """account_standing() for a caller that came in over the wire.

    Separate from the function itself, and deliberately: the gate belongs to
    the endpoint, not to the reader. transfer_sources() calls the reader for
    every login get_list already let the user see -- putting the check inside
    would re-ask a question already answered, per row, and would break the
    whole list over one account rather than omit it.
    """
    if not bank_account:
        return {}
    # What this answers is how much money is on a named account. An endpoint
    # any logged-in user can call has to say no itself.
    frappe.has_permission("Bank Account", ptype="read", doc=bank_account,
                          throw=True)
    return account_standing(bank_account)


def account_standing(bank_account):
    """What is on the account and what the bank lets it go below.

    Asked at the moment somebody picks an account to pay from, because that is
    when it decides anything. A balance that has to be looked up on another
    page is a balance nobody looks up, and an order entered against an account
    that cannot carry it comes back from the bank days later.

    The overdraft line matters as much as the balance and is easy to forget:
    664.028,54 EUR on the account with a line of 250.000,00 EUR means 914.028,54
    can leave it, and a balance of 7.278,16 with no line at all means 7.278,16.
    Both are stated, and so is the sum, because the sum is the number the
    person entering the order is actually asking about.

    The date is stated too. These fields are written by a fetch, so their age
    is the difference between a fact and a guess -- and an account nobody has
    fetched has no balance at all rather than a balance of zero.

    :return: {"balance", "credit_line", "available", "as_of", "currency"} with
        balance None where nothing was ever fetched
    """
    if not bank_account:
        return {}

    meta = frappe.get_meta("Bank Account")
    # "account", not "account_currency". A Bank Account has no currency of its
    # own -- it names the ledger Account it books to, and the currency is that
    # Account's. Asking Bank Account for account_currency was answered here by
    # has_field(), which quietly dropped the field and left every standing
    # without a currency; asked from the browser the same way, the server
    # refused it outright, which is the "Feld in der Abfrage nicht erlaubt:
    # account_currency" that came up on opening a transfer.
    wanted = [f for f in ("custom_account_balance", "custom_credit_line",
                          "account", "last_integration_date")
              if meta.has_field(f)]
    if not wanted:
        return {}

    row = frappe.db.get_value(
        "Bank Account", bank_account, wanted, as_dict=True) or {}

    balance = row.get("custom_account_balance")
    line = row.get("custom_credit_line")

    # A zero that nobody fetched is not a balance, it is an empty field.
    #
    # This module already said "an account nobody has fetched has no balance
    # at all rather than a balance of zero", and then let a stored 0.00
    # through as a fact -- because it only checked for None. On this instance
    # that is 788 of 839 accounts: the field defaults to 0, and
    # last_integration_date has never been written by anything. So the entry
    # form told the person picking an account that it held 0.00 EUR, in the
    # same confident line it uses for the account that really holds
    # 664,028.54. A wrong number about money reads exactly like a right one.
    #
    # Deliberately narrow: a real zero that a fetch did record keeps its date
    # and stays a real zero. It is only the untouched pair -- no date AND
    # exactly zero -- that is treated as "nothing is known".
    if not row.get("last_integration_date") and not balance:
        balance = None

    standing = {
        "balance": balance,
        "credit_line": line,
        "currency": _currency_of(row.get("account")),
        "as_of": row.get("last_integration_date"),
    }
    if balance is not None:
        standing["available"] = float(balance) + float(line or 0)
    return standing
