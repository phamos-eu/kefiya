# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Staging layer for previewed, not-yet-booked bank payments.

Kefiya Planned Payment records are a temporary preview of scheduled transfers,
standing-order occurrences and pending entries. They never touch the bank
balance or reconciliation. The flow is:

    bank fetch ---> refresh_planned_payments(login, items)   (idempotent upsert)
    real txn   ---> match_on_bank_transaction(bank_transaction)  (delete match)
    daily      ---> expire_stale_planned_payments()          (housekeeping)

NOTE: the actual retrieval of scheduled payments from the bank is not wired up
yet -- python-fints 5.x exposes no ready API for scheduled transfers / standing
orders, so this is bank- and library-dependent work. ``refresh_planned_payments``
is the source-agnostic entry point that such a fetch would call with a list of
plain dicts; everything below it is independent of how the data is obtained.
"""

import hashlib

import frappe
from frappe.utils import (
    now_datetime,
    getdate,
    add_days,
    add_months,
    flt,
    cint,
)

DOCTYPE = "Kefiya Planned Payment"

# match tolerance: a planned date may differ from the actual booking date by a
# few days (weekends, bank processing).
MATCH_DATE_TOLERANCE_DAYS = 3
# amounts are compared with a small epsilon to avoid float-equality surprises.
AMOUNT_EPSILON = 0.005
# how far past its planned date an unmatched Open entry may linger before it is
# marked Expired.
EXPIRE_GRACE_DAYS = 3


def compute_reference_key(bank_account, planned_date, amount, counterparty_iban,
                          counterparty_name, purpose):
    """Stable hash identifying one planned payment across fetches.

    The counterparty identity prefers the (more stable) name and only falls
    back to the IBAN, so the key does not change when a bank reports the same
    item with the IBAN populated in one fetch and empty in the next.
    """
    identity = (counterparty_name or "").strip().lower() \
        or (counterparty_iban or "").replace(" ", "").upper()
    parts = [
        (bank_account or "").strip(),
        str(getdate(planned_date)) if planned_date else "",
        "{0:.2f}".format(flt(amount)),
        identity,
        (purpose or "").strip(),
    ]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def _advance(date_obj, frequency):
    """Step a date forward by one occurrence of the given frequency."""
    freq = (frequency or "Monthly").lower()
    if freq == "daily":
        return add_days(date_obj, 1)
    if freq == "weekly":
        return add_days(date_obj, 7)
    if freq == "yearly":
        return add_months(date_obj, 12)
    # default / "monthly"
    return add_months(date_obj, 1)


def expand_standing_order_occurrences(next_date, frequency):
    """Occurrences of a standing order that fall within the next month.

    Only occurrences from today up to one month ahead are returned, so the
    staging table holds a short, rolling preview rather than the whole series.

    :param next_date: next execution date of the standing order
    :param frequency: 'Daily' | 'Weekly' | 'Monthly' | 'Yearly'
    :return: list[datetime.date]
    """
    if not next_date:
        return []
    today = now_datetime().date()
    horizon_end = getdate(add_months(today, 1))
    occurrences = []
    current = getdate(next_date)
    # safety bound so a misconfigured frequency cannot loop unbounded
    for _ in range(62):
        if current > horizon_end:
            break
        if current >= today:
            occurrences.append(current)
        current = getdate(_advance(current, frequency))
    return occurrences


def _first(mapping, keys):
    """Return the first non-empty value among ``keys`` in ``mapping``."""
    for k in keys:
        v = mapping.get(k)
        if v not in (None, "", []):
            return v
    return None


def _coerce_amount(value):
    """Best-effort extract a positive float amount from varied shapes.

    Banks/libraries report the amount as a bare number, a string, or a nested
    ``{"amount": .., "currency": ..}`` object. Returns ``None`` if nothing
    numeric can be found (the item is then skipped rather than guessed).
    """
    if isinstance(value, dict):
        value = _first(value, ["amount", "value"])
    if value in (None, ""):
        return None
    try:
        return abs(flt(value))
    except Exception:
        return None


def normalize_scheduled_debits(raw_items):
    """Map raw FinTS standing-order / scheduled-debit data to planned-payment
    items that :func:`refresh_planned_payments` understands.

    The shape returned by python-fints is bank- and version-dependent, so this
    reads a set of plausible key aliases defensively and SKIPS anything it
    cannot parse (a missing date or amount) instead of inserting a guessed row.
    Standing orders with a recurrence are expanded into their occurrences over
    the next month so the forecast shows the rolling preview.

    :param raw_items: list of jsonable dicts (already run through _to_jsonable)
    :return: dict with ``items`` (list) and ``skipped`` (int)
    """
    items = []
    skipped = 0
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            skipped += 1
            continue

        amount = _coerce_amount(
            _first(raw, ["amount", "value", "instructed_amount"]))
        base_date = _first(raw, [
            "next_execution_date", "next_date", "execution_date",
            "first_execution_date", "due_date", "date", "planned_date",
        ])
        if amount is None or not base_date:
            skipped += 1
            continue

        name = _first(raw, [
            "counterparty_name", "recipient_name", "creditor_name",
            "recipient", "name", "creditor",
        ])
        iban = _first(raw, [
            "counterparty_iban", "recipient_iban", "creditor_iban", "iban",
        ])
        purpose = _first(raw, [
            "purpose", "remittance_info", "reference", "usage", "description",
        ])
        frequency = _first(raw, [
            "frequency", "time_unit", "interval", "cycle",
        ])
        kind = _first(raw, ["payment_kind", "type"]) or "Standing Order"

        # A recurrence expands to its occurrences in the next month; a one-off
        # (no frequency) yields a single dated item.
        if frequency:
            dates = expand_standing_order_occurrences(base_date, frequency)
        else:
            try:
                dates = [getdate(base_date)]
            except Exception:
                dates = []
        if not dates:
            skipped += 1
            continue

        for d in dates:
            items.append({
                "planned_date": d,
                "amount": amount,
                "direction": "Outgoing",
                "payment_kind": kind,
                "counterparty_name": name,
                "counterparty_iban": iban,
                "purpose": purpose,
            })

    return {"items": items, "skipped": skipped}


def refresh_planned_payments(kefiya_login, items):
    """Idempotently upsert the planned payments reported for a login.

    Existing Open records that are no longer reported are marked Cancelled
    (the user removed the order at the bank). Records are keyed by a stable
    reference hash so repeated fetches do not create duplicates.

    :param kefiya_login: Kefiya Login name
    :param items: list of dicts with keys planned_date, amount, direction,
        payment_kind, counterparty_name, counterparty_iban, purpose
    :return: dict with created / updated / cancelled counts
    """
    login = frappe.get_doc("Kefiya Login", kefiya_login)
    bank_account = login.bank_account
    company = login.company
    now = now_datetime()

    seen_keys = set()
    created = updated = 0

    for item in items or []:
        key = compute_reference_key(
            bank_account,
            item.get("planned_date"),
            item.get("amount"),
            item.get("counterparty_iban"),
            item.get("counterparty_name"),
            item.get("purpose"),
        )
        seen_keys.add(key)

        existing = frappe.db.get_value(DOCTYPE, {"reference_key": key}, "name")
        if existing:
            doc = frappe.get_doc(DOCTYPE, existing)
            doc.status = "Open"
            doc.fetched_on = now
            doc.save(ignore_permissions=True)
            updated += 1
        else:
            doc = frappe.get_doc({
                "doctype": DOCTYPE,
                "kefiya_login": kefiya_login,
                "bank_account": bank_account,
                "company": company,
                "planned_date": item.get("planned_date"),
                "amount": flt(item.get("amount")),
                "direction": item.get("direction") or "Outgoing",
                "payment_kind": item.get("payment_kind") or "Scheduled Transfer",
                "counterparty_name": item.get("counterparty_name"),
                "counterparty_iban": item.get("counterparty_iban"),
                "purpose": item.get("purpose"),
                "status": "Open",
                "reference_key": key,
                "fetched_on": now,
            })
            doc.insert(ignore_permissions=True)
            created += 1

    # anything still Open for this login but no longer reported was cancelled
    # at the bank.
    cancelled = 0
    open_records = frappe.get_all(
        DOCTYPE,
        filters={"kefiya_login": kefiya_login, "status": "Open"},
        fields=["name", "reference_key"],
    )
    for rec in open_records:
        if rec.reference_key not in seen_keys:
            frappe.db.set_value(DOCTYPE, rec.name, "status", "Cancelled")
            cancelled += 1

    return {"created": created, "updated": updated, "cancelled": cancelled}


def _counterparty_matches(planned, txn):
    """True when planned payment and bank transaction refer to the same party."""
    p_iban = (planned.get("counterparty_iban") or "").replace(" ", "").upper()
    t_iban = (getattr(txn, "bank_party_iban", None) or "").replace(" ", "").upper()
    if p_iban and t_iban:
        return p_iban == t_iban

    p_name = (planned.get("counterparty_name") or "").strip().lower()
    t_name = (getattr(txn, "bank_party_name", None) or "").strip().lower()
    if p_name and t_name:
        return p_name in t_name or t_name in p_name

    # Insufficient counterparty signal to be sure: do NOT auto-delete on
    # amount + date alone (would wrongly remove same-amount entries such as
    # rent/salary). Leave it to expire for the user to review.
    return False


def match_on_bank_transaction(doc, method=None):
    """doc_events hook (Bank Transaction, after_insert).

    When a real transaction is booked, remove the Open planned payment it
    fulfils. Matched records are deleted immediately (no audit retention).

    Wrapped defensively: this runs in the same transaction as the Bank
    Transaction insert (after_insert), so a failure here must never abort the
    import of a real transaction -- it only does preview housekeeping.
    """
    try:
        if not doc.bank_account or not doc.date:
            return

        # Direction must agree: a withdrawal can only fulfil an Outgoing
        # planned payment, a deposit only an Incoming one.
        withdrawal = flt(doc.withdrawal)
        deposit = flt(doc.deposit)
        if withdrawal > 0:
            amount, direction = withdrawal, "Outgoing"
        elif deposit > 0:
            amount, direction = deposit, "Incoming"
        else:
            return

        txn_date = getdate(doc.date)
        candidates = frappe.get_all(
            DOCTYPE,
            filters={
                "bank_account": doc.bank_account,
                "status": "Open",
                "direction": direction,
                "planned_date": [
                    "between",
                    [
                        add_days(txn_date, -MATCH_DATE_TOLERANCE_DAYS),
                        add_days(txn_date, MATCH_DATE_TOLERANCE_DAYS),
                    ],
                ],
            },
            fields=["name", "amount", "counterparty_iban", "counterparty_name"],
        )

        for cand in candidates:
            if abs(flt(cand.amount) - amount) > AMOUNT_EPSILON:
                continue
            if _counterparty_matches(cand, doc):
                frappe.delete_doc(
                    DOCTYPE, cand.name, ignore_permissions=True, force=True
                )
                break
    except Exception:
        frappe.log_error(
            title="Kefiya planned-payment matching failed",
            message=frappe.get_traceback(),
        )


def expire_stale_planned_payments():
    """Scheduled (daily): mark Open entries whose planned date has passed
    without a matching booking as Expired, for user review."""
    cutoff = add_days(now_datetime().date(), -EXPIRE_GRACE_DAYS)
    stale = frappe.get_all(
        DOCTYPE,
        filters={"status": "Open", "planned_date": ["<", cutoff]},
        pluck="name",
    )
    for name in stale:
        frappe.db.set_value(DOCTYPE, name, "status", "Expired")
    return len(stale)
