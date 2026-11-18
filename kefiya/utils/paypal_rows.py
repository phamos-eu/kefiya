# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What a PayPal transaction becomes in the books -- without frappe.

PayPal speaks no FinTS. Its movements come from the REST API, "Transaction
Search" (GET /v1/reporting/transactions), one record per transaction with
the amount and the fee given separately. This module decides, from that
record alone, which bookings it makes; paypal.py asks the API and writes
them. Kept apart so the decisions run in a test without a bench or a PayPal
account.

The rules (agreed on 18.11.2026):

* **Amount and fee on lines of their own.** The amount as PayPal reports it,
  the fee as a second booking. The balance then matches PayPal's, and the
  fee can be assigned to its own account without being taken out of a
  customer's payment.
* **Only completed transactions** (status "S"). Pending ("P") and denied
  ("D") ones have not moved the balance; a reversal ("V") arrives as a
  transaction of its own and is booked as that.
* **Only the account's currency.** A payment in another currency is followed
  by PayPal's currency conversion, and the conversion's leg in the account's
  currency is what moved the balance. Booking the foreign amount as well
  would count it twice.
* **The transaction id is the reference**, so fetching the same window again
  -- which the overlap does every day -- books nothing twice.
"""

import datetime

#: PayPal answers at most 31 days per query.
MAX_WINDOW_DAYS = 31

#: The status of a transaction that has moved the balance.
COMPLETED = "S"

#: Prefix of the reference written on every booking from PayPal.
PREFIX = "PAYPAL-"


def windows(start, end, max_days=MAX_WINDOW_DAYS):
    """Split [start, end) into windows PayPal accepts, oldest first.

    :param start, end: datetimes
    :return: list of (start, end) pairs, each at most ``max_days`` long
    """
    out = []
    step = datetime.timedelta(days=max_days)
    cursor = start
    while cursor < end:
        upper = min(cursor + step, end)
        out.append((cursor, upper))
        cursor = upper
    return out


def as_iso(moment):
    """The timestamp format the API asks for, in UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+0000")


def _amount(money):
    try:
        return round(float((money or {}).get("value") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _party(detail):
    payer = detail.get("payer_info") or {}
    name = payer.get("payer_name") or {}
    full = name.get("alternate_full_name") or " ".join(
        part for part in (name.get("given_name"), name.get("surname")) if part)
    return (full or payer.get("email_address") or "").strip()


def _text(info, detail):
    parts = []
    for key in ("transaction_subject", "transaction_note", "invoice_id",
                "custom_field"):
        value = (info.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    items = ((detail.get("cart_info") or {}).get("item_details") or [])
    for item in items[:3]:
        value = (item.get("item_name") or "").strip()
        if value and value not in parts:
            parts.append(value)
    parts.append("PayPal " + (info.get("transaction_id") or ""))
    return " / ".join(parts)


def rows_from(detail, currency):
    """The bookings one PayPal transaction makes, as canonical entries.

    :param detail: one element of ``transaction_details``
    :param currency: the Bank Account's currency, e.g. "EUR"
    :return: list of {"date", "amount", "description", "counterparty",
        "iban", "reference"} -- empty when the transaction books nothing
    """
    info = detail.get("transaction_info") or {}
    if info.get("transaction_status") != COMPLETED:
        return []
    money = info.get("transaction_amount") or {}
    if (money.get("currency_code") or "") != currency:
        return []
    tx_id = info.get("transaction_id")
    if not tx_id:
        return []

    date = (info.get("transaction_initiation_date")
            or info.get("transaction_updated_date") or "")[:10]
    party = _party(detail)
    text = _text(info, detail)

    rows = []
    amount = _amount(money)
    if amount:
        rows.append({"date": date, "amount": amount, "description": text,
                     "counterparty": party, "iban": None,
                     "reference": PREFIX + tx_id})
    fee = _amount(info.get("fee_amount"))
    if fee:
        # PayPal reports the fee with its own sign: negative when it was
        # charged, positive when it came back with a refund.
        rows.append({"date": date, "amount": fee,
                     "description": "PayPal fee / " + text,
                     "counterparty": "PayPal", "iban": None,
                     "reference": PREFIX + tx_id + "-FEE"})
    return rows
