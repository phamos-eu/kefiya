# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The bank's exchange rate on a Payment Entry made from a Bank Transaction.

Pure: no frappe. The override that builds the Payment Entry asks this module
which amounts and rates to set, so the arithmetic can be checked without a
site.

Where the statement stated the rate the bank applied (camt.053 ``CcyXchg``,
stored on the Bank Transaction as kefiya_fx_*), that is the rate the money
actually moved at. ERPNext would otherwise take its own rate of the posting
day, and the difference between the two becomes an exchange gain or loss
that never happened.

Only where ONE rate decides everything. A Payment Entry converts between up
to three currencies: the bank account's, the party account's and the
company's. With two distinct currencies among them, the bank's single rate
fixes every conversion and the base amounts of both sides agree by
construction. With three, the bank's rate would sit next to two system rates
whose cross rate is not the bank's -- the entry would not balance, or would
balance on a difference nobody can explain. Then nothing is changed and
ERPNext's own rates apply.
"""

from kefiya.utils.camt_statement import rate_between

#: The Payment Entry fields this module decides.
FIELDS = ("paid_amount", "received_amount", "source_exchange_rate",
          "target_exchange_rate")


def _rate(fx, from_currency, to_currency):
    if from_currency == to_currency:
        return 1.0
    return rate_between(fx, from_currency, to_currency)


def payment_entry_values(payment_type, bank_amount, bank_currency,
                         party_currency, company_currency, fx):
    """Amounts and rates of a Payment Entry at the bank's rate, or None.

    :param payment_type: "Receive" (money into the bank account) or "Pay"
    :param bank_amount: the Bank Transaction's amount, in bank_currency
    :param fx: {"from", "to", "rate"} as stored on the Bank Transaction
    :return: dict over FIELDS, or None where the bank's rate does not decide
        the entry on its own (see the module docstring)
    """
    if payment_type not in ("Receive", "Pay"):
        return None
    currencies = {bank_currency, party_currency, company_currency}
    if None in currencies or "" in currencies or len(currencies) != 2:
        # One currency: nothing to convert. Three: see above.
        return None

    bank_to_party = _rate(fx, bank_currency, party_currency)
    bank_to_company = _rate(fx, bank_currency, company_currency)
    party_to_company = _rate(fx, party_currency, company_currency)
    if not (bank_to_party and bank_to_company and party_to_company):
        # The bank's rate is about another pair of currencies.
        return None

    bank_amount = abs(float(bank_amount or 0))
    in_party_currency = bank_amount * bank_to_party

    if payment_type == "Receive":
        # From the party account, into the bank account.
        return {"paid_amount": in_party_currency,
                "received_amount": bank_amount,
                "source_exchange_rate": party_to_company,
                "target_exchange_rate": bank_to_company}
    return {"paid_amount": bank_amount,
            "received_amount": in_party_currency,
            "source_exchange_rate": bank_to_company,
            "target_exchange_rate": party_to_company}


def _cents(value):
    return int(round(float(value or 0) * 100))


def invoice_allocation(unallocated, outstanding, bank_to_party):
    """How much of a booking pays how much of an invoice, across currencies.

    The booking is in the bank account's currency, the invoice's outstanding
    amount in the party account's. Compared directly, 940 CHF against a
    1.000 EUR invoice reads as "the booking does not cover the invoice" --
    which is what the assignment wizard did. Converted at the rate the
    money moved at, it covers it exactly.

    Equal to the cent counts as equal: a rate with nine decimals never turns
    940,00 into exactly 1.000,00, and a remainder of a fraction of a cent on
    either side is a booking nobody can reconcile.

    :param unallocated: open amount of the booking, bank currency
    :param outstanding: open amount of the invoice, party currency
    :param bank_to_party: 1 bank currency = this much party currency
    :return: {"bank_amount", "party_amount", "remaining_bank",
              "remaining_party", "settles_invoice", "settles_booking"}
    """
    unallocated = abs(float(unallocated or 0))
    outstanding = abs(float(outstanding or 0))
    rate = float(bank_to_party)
    if rate <= 0:
        raise ValueError("an exchange rate must be positive")

    covers = unallocated * rate
    if _cents(covers) >= _cents(outstanding):
        # The booking pays the whole invoice; what is left stays on it.
        party_amount = outstanding
        # Never more than the booking has: rounding to the cent may not
        # take what is not there.
        bank_amount = min(round(outstanding / rate, 2), unallocated)
        if _cents(covers) == _cents(outstanding):
            bank_amount = unallocated
    else:
        bank_amount = unallocated
        party_amount = round(covers, 2)

    remaining_bank = round(unallocated - bank_amount, 2)
    remaining_party = round(outstanding - party_amount, 2)
    return {
        "bank_amount": bank_amount,
        "party_amount": party_amount,
        "remaining_bank": remaining_bank,
        "remaining_party": remaining_party,
        "settles_invoice": _cents(remaining_party) == 0,
        "settles_booking": _cents(remaining_bank) == 0,
    }
