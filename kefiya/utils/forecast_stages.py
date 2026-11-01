# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One payment, one line: the forecast keeps a process at its highest stage.

Without frappe and without a bank, like scheduled_transfers: this decides
which lines of the forecast disappear, and a rule that hides money is the one
rule that must be checked by something other than the bank.

A payment passes through stages, and every stage may leave a line in the
forecast::

    10  plan                 anything planned ahead (and every kind this
                             module does not know -- a kind added by another
                             app is a plan until it says otherwise)
    20  order                Purchase Order, Sales Order
    30  invoice              Purchase Invoice, Sales Invoice
    40  payment, ours        a transfer or standing order this system holds
    50  payment, the bank's  what the bank reports it holds (from_bank = 1)
    60  pending at the bank  accepted, not yet booked
        booked               the bank transaction -- the line is Matched

Without a rule the same rent, the same supplier, the same transfer to
ourselves stands in the forecast once per stage it has reached, and the
liquidity preview counts it that often.

The rule: a line is superseded by the line of the next higher stage that is
the same payment. It is not deleted -- it is Cancelled with the reason
"Superseded by higher stage" and points at the line that took its place, so
the chain stays readable. If that line goes away because the bank no longer
holds the order, the lower line comes back.

"The same payment" is decided only where it is unambiguous: same account,
same direction, same counterparty IBAN, the amount to the cent, due within
MAX_DAYS of each other -- and exactly one candidate on each side. Anything
less is left standing and reported. Two lines for one payment are a visible
error; one line for two payments is an invisible one.
"""

import datetime

#: The reason a superseded line carries. Already in the forecast's options.
SUPERSEDED = "Superseded by higher stage"

#: States a line can be superseded from.
OPEN_STATES = ("Open", "Partially Matched")

#: States in which a line still stands for its payment.
ALIVE_STATES = ("Open", "Partially Matched", "Matched", "Booked")

#: Days two stages of one payment may lie apart.
MAX_DAYS = 7

#: Cent tolerance on amounts -- floating point, not leniency.
AMOUNT_EPSILON = 0.005

PLAN, ORDER, INVOICE, OURS, BANK, PENDING = 10, 20, 30, 40, 50, 60

STAGE_OF_KIND = {
    "Purchase Order": ORDER,
    "Sales Order": ORDER,
    "Purchase Invoice": INVOICE,
    "Sales Invoice": INVOICE,
    "Scheduled Transfer": OURS,
    "Bank Standing Order": OURS,
    "Scheduled Debit": OURS,
    "Pending": PENDING,
}

#: How a mirror line is recognised, and named: MIRROR_PREFIX + source name.
MIRROR_PREFIX = "MIR-"
MIRROR_GENERATOR = "kefiya_mirror"


def stage_of(source_kind, from_bank=0):
    """The stage of a forecast line. Unknown kinds are plans."""
    stage = STAGE_OF_KIND.get(source_kind or "", PLAN)
    if stage == OURS and int(from_bank or 0):
        return BANK
    return stage


def normalize_iban(value):
    return "".join(str(value or "").split()).upper()


def _date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _ibans(row):
    """The counterparty IBANs a line can be recognised by."""
    found = set()
    own = normalize_iban(row.get("counterparty_iban"))
    if own:
        found.add(own)
    for iban in row.get("party_ibans") or ():
        if normalize_iban(iban):
            found.add(normalize_iban(iban))
    return found


def same_payment(higher, lower, max_days=MAX_DAYS):
    """Could ``higher`` be the next stage of ``lower``?"""
    if not higher.get("bank_account") or \
            higher.get("bank_account") != lower.get("bank_account"):
        return False
    if (higher.get("direction") or "Outgoing") != \
            (lower.get("direction") or "Outgoing"):
        return False
    if abs(float(higher.get("expected_amount") or 0)
           - float(lower.get("expected_amount") or 0)) > AMOUNT_EPSILON:
        return False
    a, b = _date(higher.get("due_date")), _date(lower.get("due_date"))
    if not a or not b or abs((a - b).days) > max_days:
        return False
    # Both sides must name the counterparty. A line without one cannot be
    # told apart from the next payment of the same amount.
    mine = normalize_iban(higher.get("counterparty_iban"))
    return bool(mine) and mine in _ibans(lower)


def decide(rows, max_days=MAX_DAYS):
    """Which open lines are superseded, and by which.

    :param rows: forecast lines as dicts -- name, source_kind, from_bank,
        status, bank_account, direction, expected_amount, due_date,
        counterparty_iban, optional party_ibans
    :return: {"supersede": [(lower_name, higher_name)],
              "ambiguous": [(higher_name, [candidate names])]}
    """
    open_rows = [r for r in rows if r.get("status") in OPEN_STATES]
    # Only lines on the same account, in the same direction, of the same
    # amount can be one payment. Grouping first keeps a forecast of a few
    # thousand lines from being compared pair by pair.
    groups = {}
    for r in open_rows:
        r["stage"] = stage_of(r.get("source_kind"), r.get("from_bank"))
        key = (r.get("bank_account") or "", r.get("direction") or "Outgoing",
               int(round(float(r.get("expected_amount") or 0) * 100)))
        groups.setdefault(key, []).append(r)

    chosen = {}
    ambiguous = []
    for higher in open_rows:
        cents = int(round(float(higher.get("expected_amount") or 0) * 100))
        pool = []
        for c in (cents - 1, cents, cents + 1):
            pool.extend(groups.get((higher.get("bank_account") or "",
                                    higher.get("direction") or "Outgoing",
                                    c), []))
        candidates = [low for low in pool
                      if low["stage"] < higher["stage"]
                      and same_payment(higher, low, max_days)]
        if not candidates:
            continue
        # The next stage down, not any stage down: a plan that an invoice has
        # not yet replaced is the invoice's business, not the bank's.
        top = max(c["stage"] for c in candidates)
        nearest = [c for c in candidates if c["stage"] == top]
        if len(nearest) == 1:
            chosen.setdefault(nearest[0]["name"], []).append(higher["name"])
        else:
            ambiguous.append((higher["name"],
                              sorted(c["name"] for c in nearest)))

    supersede = []
    for lower_name in sorted(chosen):
        highers = chosen[lower_name]
        if len(highers) == 1:
            supersede.append((lower_name, highers[0]))
        else:
            # Two higher lines claim the same lower one: neither is sure.
            for name in highers:
                ambiguous.append((name, [lower_name]))
    return {"supersede": supersede, "ambiguous": ambiguous}


def to_reopen(superseded, by_name):
    """Superseded lines whose higher stage is gone.

    Only a line the bank reported can go away on its own: the bank no longer
    holds the order, and the sweep cancelled it -- without a reason. A line
    someone planned, or any line a person cancelled with a reason, is left
    alone: whoever cancelled it had a reason this module cannot see.

    :param superseded: lines with status Cancelled and reason SUPERSEDED
    :param by_name: {name: line} of the lines they point to
    :return: names to reopen
    """
    out = []
    for row in superseded:
        target = by_name.get(row.get("superseded_by") or "")
        if target is None:
            out.append(row["name"])
            continue
        if not int(target.get("from_bank") or 0):
            continue
        if target.get("status") in ALIVE_STATES:
            continue
        if target.get("status") == "Cancelled" and \
                not target.get("cancellation_reason"):
            out.append(row["name"])
    return sorted(out)


def _mirror_values(src, target, account_iban, own_name):
    return {
        "direction": "Incoming",
        "bank_account": target,
        "source_kind": src.get("source_kind"),
        "from_bank": int(src.get("from_bank") or 0),
        "due_date": src.get("due_date"),
        "expected_amount": float(src.get("expected_amount") or 0),
        "purpose": src.get("purpose"),
        "counterparty_iban": normalize_iban(
            account_iban.get(src.get("bank_account"))) or None,
        "counterparty_name": own_name.get(src.get("bank_account"))
        or src.get("bank_account"),
    }


def mirror_actions(lines, own, own_name=None):
    """Keep the incoming side of every transfer between our own accounts.

    A Kontoübertrag leaves one of our accounts and arrives at another. The
    line that announces it -- a plan, our own order, the bank's report -- is
    written on the paying account; without its mirror the forecast shows the
    money leaving and arriving nowhere, and the receiving account looks
    poorer than it will be.

    The mirror follows its source: same date, same amount, cancelled when the
    source is cancelled or expires. When a higher stage replaces the source,
    that stage has a mirror of its own, and the ladder replaces this mirror
    by that one -- one payment, one line, on both accounts.

    Pending entries are not mirrored: the receiving bank reports its side of
    those itself, and a mirror would count it twice.

    :param lines: the outgoing lines that pay one of our IBANs, in any
        status, and every existing mirror (mirror_of set)
    :param own: {normalized IBAN: Bank Account} of our accounts
    :param own_name: {Bank Account: display name}
    :return: {"create": [(source name, values)],
              "update": [(mirror name, values)],
              "cancel": [(mirror name, reason)]}
    """
    own_name = own_name or {}
    account_iban = {}
    for iban, account in own.items():
        account_iban.setdefault(account, iban)

    by_name = {}
    mirrors = {}
    for line in lines:
        if line.get("mirror_of"):
            mirrors[line["mirror_of"]] = line
        else:
            by_name[line["name"]] = line

    out = {"create": [], "update": [], "cancel": []}
    for name in sorted(by_name):
        src = by_name[name]
        if src.get("status") not in OPEN_STATES:
            continue
        if (src.get("direction") or "Outgoing") != "Outgoing":
            continue
        if src.get("source_kind") == "Pending" or not src.get("bank_account"):
            continue
        target = own.get(normalize_iban(src.get("counterparty_iban")))
        if not target or target == src.get("bank_account"):
            continue
        want = _mirror_values(src, target, account_iban, own_name)
        mirror = mirrors.get(name)
        if mirror is None:
            out["create"].append((name, want))
            continue
        if mirror.get("status") not in OPEN_STATES:
            continue
        changed = {}
        for field in ("due_date", "expected_amount", "purpose"):
            if str(mirror.get(field) or "") != str(want[field] or ""):
                changed[field] = want[field]
        if changed:
            out["update"].append((mirror["name"], changed))

    for source_name in sorted(mirrors):
        mirror = mirrors[source_name]
        if mirror.get("status") not in OPEN_STATES:
            continue
        src = by_name.get(source_name)
        if src is None:
            out["cancel"].append((mirror["name"], "Other"))
        elif src.get("status") == "Cancelled" and \
                src.get("cancellation_reason") != SUPERSEDED:
            out["cancel"].append((mirror["name"],
                                  src.get("cancellation_reason") or "Other"))
        elif src.get("status") == "Expired":
            out["cancel"].append((mirror["name"], "Other"))
    return out
