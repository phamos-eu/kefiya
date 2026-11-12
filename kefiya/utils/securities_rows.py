# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Turn python-fints holdings into the plain rows ``refresh_holdings`` stores.

Kept free of frappe and fints imports so the mapping can be tested without a
bench. Both FinTS controllers use it, so they cannot drift apart again.

python-fints names the field ``ISIN`` in capitals (``fints.models.Holding``).
The controllers used to read ``isin``, got None for every position, and
``refresh_holdings`` skipped each one for lack of an identity -- a fetch that
"worked" and stored nothing.
"""


def _first(holding, *names):
    """The first of ``names`` the holding carries with a value, else None."""
    for name in names:
        if isinstance(holding, dict):
            value = holding.get(name)
        else:
            value = getattr(holding, name, None)
        if value not in (None, ""):
            return value
    return None


def holding_rows(holdings, securities_account):
    """Map MT535 holdings to dicts for ``securities.refresh_holdings``.

    :param holdings: iterable of ``fints.models.Holding`` (or dicts with the
        same names)
    :param securities_account: the depot number the rows belong to
    :return: list of dicts (isin, security_name, quantity, price,
        market_value, acquisition_price, currency, valuation_date,
        securities_account)
    """
    rows = []
    for h in holdings or []:
        isin = _first(h, "ISIN", "isin")
        rows.append({
            "isin": str(isin).strip().upper() if isin else None,
            "security_name": _first(h, "name"),
            "quantity": _first(h, "pieces"),
            # python-fints calls the unit price "market_value" and the
            # position's value "total_value".
            "price": _first(h, "market_value"),
            "market_value": _first(h, "total_value"),
            "acquisition_price": _first(h, "acquisitionprice"),
            "currency": _first(h, "value_symbol"),
            "valuation_date": _first(h, "valuation_date"),
            "securities_account": securities_account,
        })
    return rows


def same_account_number(left, right):
    """Whether two account numbers name the same account.

    Banks pad account numbers with leading zeros in some segments and not in
    others, so ``0123`` and ``123`` are the same depot.
    """
    a = str(left or "").strip().lstrip("0")
    b = str(right or "").strip().lstrip("0")
    return bool(a) and a == b


def sold_positions(previous_isins, current_isins):
    """ISINs held at the previous snapshot and absent from this one.

    Those are the positions that were sold or transferred away. Without this
    they simply stop appearing: the last snapshot stays as history, and
    nothing says whether the position was closed or whether the fetch came
    back short. Put two days' snapshots side by side and a missing line is
    unreadable.

    Compared as upper-case ISINs, empty entries ignored, sorted so two runs
    produce the same order.
    """
    before = {str(isin).strip().upper()
              for isin in (previous_isins or []) if str(isin or "").strip()}
    now = {str(isin).strip().upper()
           for isin in (current_isins or []) if str(isin or "").strip()}
    return sorted(before - now)


def depot_fetch_due(last_fetch, now):
    """Whether a depot is due: not yet tried on the calendar day of ``now``.

    Holdings move with the market, not with bookings: one snapshot a day is
    enough, and every extra HKWPD is another bank dialog that may ask for a
    TAN. The gate used to be "20 hours since the last try". A fetch by hand
    in the evening then blocked the scheduled one the next morning, and every
    later click pushed the morning run further out. A calendar day keeps the
    morning run with the bank fetch, whatever was pressed the day before.

    :param last_fetch: datetime (or ISO string) of the last try, or None
    :param now: datetime (or ISO string) of this run
    """
    if not last_fetch:
        return True
    return str(last_fetch)[:10] < str(now)[:10]
