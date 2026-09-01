# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Book what a statement said.

statement_formats.py reads files and feeds into canonical entries. This module
turns those entries into Bank Transactions -- and it is the ONLY module that
does, which is the point of it.

Before this, a Bank Transaction was assembled in five places: the file import,
its two encoding-specific twins, the pushed-entry endpoint and the credit-card
fetch. They disagreed about whether a booking carries a status, whether a party
is looked up, and whether it is submitted -- so the same booking came out
differently shaped depending on which path happened to create it. Worse, each
carried its own idea of what a duplicate is, and the endpoint that most needed
the content check did not have it.

One constructor, one traversal, one definition of duplicate. Everything else
is a caller.
"""

import base64
import re

import frappe
from frappe import _
from frappe.utils import cint, flt

from kefiya.utils import statement_formats as formats
from kefiya.utils.statement_formats import (  # noqa: F401 -- the public names
    PROFILES, decode, normalise_pushed, parse_amount, parse_date, read_rows,
    reference_number, to_entry,
)


# --------------------------------------------------------------------------
# What counts as already booked
# --------------------------------------------------------------------------

def already_booked(bank_account, entry):
    """Is this booking already on the account, under whatever reference?

    The reference hash only recognises what this app wrote. A booking the bank
    itself delivered by FinTS carries the bank's reference, so a hash
    comparison sees a stranger and books it a second time -- which is exactly
    the situation when a file or a feed fills a gap in an account that is
    otherwise fetched.

    So the second line of defence compares what a booking IS: same account,
    same day, same amount, same side. That is deliberately blunt. Two
    genuinely distinct bookings of the same amount on the same day do exist --
    two identical parking fees -- and this will hold the second one back.
    Reporting a real booking as "already present" is recoverable by looking at
    the day; a duplicate in the ledger is the error that took 3.000 entries to
    notice the last time.

    :return: name of the existing Bank Transaction, or None
    """
    amount = flt(entry["amount"])
    field, value = (("deposit", amount) if amount > 0
                    else ("withdrawal", -amount))

    rows = frappe.get_all("Bank Transaction", limit=1, fields=["name"],
                          filters={"bank_account": bank_account,
                                   "date": str(entry["date"]),
                                   field: value})
    return rows[0]["name"] if rows else None


def is_already_booked(bank_account, entry, reference=None):
    """The single answer to "do we have this booking already?"

    Two questions in one, and they have to stay in one: the reference this app
    would have written, and -- because a booking the bank delivered carries
    the bank's own reference -- what the booking IS. Kept apart, the caller
    that forgot the second check is the one that creates the duplicates, and
    that is exactly what happened to the pushed-entry endpoint.
    """
    reference = reference or reference_number(bank_account, entry)
    if frappe.db.exists("Bank Transaction", {"reference_number": reference}):
        return True
    return bool(already_booked(bank_account, entry))


# --------------------------------------------------------------------------
# The one constructor
# --------------------------------------------------------------------------

def create_booking(bank_account, entry, reference=None, company=None):
    """Create one Bank Transaction from one canonical entry.

    Every path that turns a statement into a booking comes through here, so a
    booking looks the same whether it arrived as a file, as a pushed record or
    as a credit-card fetch.

    Created as a DRAFT, always. Submitting is an approval, and an approval is
    given at the document by a person -- not by an import that ran unattended,
    and not by a checkbox on its form.

    :return: the new document's name
    """
    amount = flt(entry["amount"])
    if company is None:
        company = frappe.db.get_value("Bank Account", bank_account, "company")

    booking = frappe.get_doc({
        "doctype": "Bank Transaction",
        "date": str(entry["date"]),
        "status": "Unreconciled",
        "bank_account": bank_account,
        "company": company,
        "deposit": amount if amount > 0 else 0,
        "withdrawal": -amount if amount < 0 else 0,
        "description": " ".join(
            part for part in (entry.get("counterparty"),
                              entry.get("description")) if part),
        "bank_party_iban": entry.get("iban"),
        "bank_party_name": entry.get("counterparty") or None,
        "reference_number": reference or reference_number(bank_account, entry),
        "allocated_amount": 0,
        "unallocated_amount": abs(amount),
    })

    # The counterparty's IBAN identifies a party only if we hold that account.
    if entry.get("iban"):
        party, party_type = party_for_iban(entry["iban"])
        booking.party, booking.party_type = party, party_type

    booking.insert(ignore_permissions=True)
    return booking.name


def party_for_iban(iban):
    """The party behind a counterparty IBAN, or ("", "")."""
    name = frappe.db.get_value("Bank Account", {"iban": iban},
                               ["party", "party_type"], as_dict=True)
    if not name:
        return "", ""
    return name.party or "", name.party_type or ""


# --------------------------------------------------------------------------
# The one traversal
# --------------------------------------------------------------------------

def _token(entry):
    """What `already_booked` compares, as a hashable value.

    Day and signed amount -- deposit and withdrawal kept apart by the sign, so
    a 250,00 credit never cancels a 250,00 debit of the same day.
    """
    return (str(entry["date"])[:10], int(round(flt(entry["amount"]) * 100)))


def _existing_budget(bank_account, entries):
    """How many bookings of each day-and-amount this account ALREADY has.

    A counter, not a set, and this is the whole point. The content check exists
    because a booking the bank delivered carries the bank's reference, so the
    hash comparison cannot recognise it. But asking "does one like this exist?"
    per entry counts every further identical entry of the same run as a
    duplicate too -- and identical here means only same day, same amount.

    That is not a theoretical loss. The April/May statement of one account
    holds nine separate fees of 5,10 € from the Landratsamt on 26 May, each
    with its own invoice number, and three invoices of 178,50 € from one
    craftsman on 2 April. Under "does one exist?" the account would have
    received one of each: 41 real bookings across the file, silently gone.

    So the existing bookings are a budget. Each entry that matches consumes
    one; once the budget is spent, the next identical entry is what it looks
    like -- a booking that is not here yet.

    Read only across the days the file actually covers for this account: an
    account with ten years of history has no business being loaded to import
    two months.
    """
    days = [str(e["date"])[:10] for e in entries]
    if not days:
        return {}

    budget = {}
    for row in frappe.get_all(
            "Bank Transaction",
            filters={"bank_account": bank_account,
                     "date": ["between", [min(days), max(days)]]},
            fields=["date", "deposit", "withdrawal"], limit_page_length=0):
        amount = flt(row.get("deposit")) - flt(row.get("withdrawal"))
        key = (str(row["date"])[:10], int(round(amount * 100)))
        budget[key] = budget.get(key, 0) + 1
    return budget


def book_entries(entries, dry_run=True, sample_size=5):
    """Walk canonical entries once, deciding and counting the same way for all.

    Each entry must carry `bank_account`. Three ways the same booking can
    already be here, checked in order of cost: twice within this run, already
    written by this app, or already delivered by the bank under its own
    reference. The last one is why the plan and the import cannot have
    separate loops -- they would eventually disagree about what a duplicate
    is, and the one nobody looks at would be the lenient one.

    :return: dict(total, created|would_create, duplicates, accounts, sample)
    """
    result = {"total": 0, "created": 0, "would_create": 0, "duplicates": 0,
              "accounts": {}, "sample": [], "dry_run": bool(dry_run)}
    seen = set()
    companies = {}

    # What each account already holds, counted once before anything is booked.
    # Per account rather than globally: two accounts of one house see the same
    # rent on the same day, and they are two bookings, not one.
    by_account = {}
    for entry in entries:
        if entry.get("bank_account"):
            by_account.setdefault(entry["bank_account"], []).append(entry)
    budgets = {target: _existing_budget(target, rows)
               for target, rows in by_account.items()}

    for entry in entries:
        target = entry.get("bank_account")
        if not target:
            continue
        result["total"] += 1
        per = result["accounts"].setdefault(
            target, {"new": 0, "duplicates": 0})

        reference = entry.get("reference_number") or reference_number(
            target, entry)

        # Three ways this can already be here. The first two are identity --
        # this exact entry, twice in the file or written by an earlier run.
        # The third is the budget: a booking that looks like this one and was
        # not written by us, which is how a bank-delivered booking is
        # recognised at all.
        budget = budgets.get(target, {})
        token = _token(entry)
        duplicate = (reference in seen
                     or frappe.db.exists("Bank Transaction",
                                         {"reference_number": reference}))
        if not duplicate and budget.get(token):
            budget[token] -= 1
            duplicate = True

        if duplicate:
            result["duplicates"] += 1
            per["duplicates"] += 1
            continue
        seen.add(reference)
        per["new"] += 1

        if len(result["sample"]) < sample_size:
            result["sample"].append({
                "date": str(entry["date"]),
                "bank_account": target,
                "description": (entry.get("description") or "")[:60],
                "in": entry["amount"] if entry["amount"] > 0 else 0,
                "out": -entry["amount"] if entry["amount"] < 0 else 0,
            })

        if dry_run:
            result["would_create"] += 1
            continue

        # The Bank Account knows its company; the caller's is only a fallback.
        # A file covering twenty accounts covers a dozen companies with them,
        # and a booking under the wrong company lands in someone else's books.
        if target not in companies:
            companies[target] = frappe.db.get_value(
                "Bank Account", target, "company")
        try:
            create_booking(target, entry, reference, companies[target])
            result["created"] += 1
        except Exception:
            result.setdefault("failed", 0)
            result["failed"] += 1
            frappe.log_error(
                title="Kefiya: could not book a statement entry",
                message=frappe.get_traceback())

    return result


def without_entries(summary):
    """The summary as it may travel to a browser.

    plan() keeps the parsed entries so the import can book them without
    reading the file twice. Those entries are the whole statement -- every
    booking of every account in the file -- and a dry run has no business
    shipping that to a browser to show five sample rows.
    """
    return {k: v for k, v in (summary or {}).items() if k != "entries"}


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

def file_content(file_url):
    """The attached file's bytes, through the File document.

    Not site_path + file_url: that string only happens to be the path for a
    public file, and a private attachment lives somewhere else entirely.
    """
    name = frappe.db.get_value("File", {"file_url": file_url})
    if not name:
        frappe.throw(_("The attached file could not be found: {0}").format(
            file_url))
    return frappe.get_doc("File", name).get_content()


def _bank_account_for_iban(iban):
    """The Bank Account carrying this IBAN, or None.

    Compared without spaces and in upper case, because an IBAN is written both
    ways and a file must not fail to match over a blank.
    """
    cleaned = str(iban or "").replace(" ", "").upper()
    if not cleaned:
        return None
    for row in frappe.get_all("Bank Account", filters={"iban": ["is", "set"]},
                              fields=["name", "iban"]):
        if str(row["iban"] or "").replace(" ", "").upper() == cleaned:
            return row["name"]
    return None


def read_entries(file_url, bank_account=None):
    """Every readable entry of a file, each addressed to its Bank Account.

    A file may cover many accounts -- a StarMoney export covers every access
    the program knows. The row says which one it belongs to, so nobody has to
    split the file by hand and nobody can file April's bookings of one account
    against another. Only where the format names no account does the caller's
    choice apply.

    :return: (profile_name, entries, notes)
    """
    text = decode(file_content(file_url))

    # MT940 first, because it cannot be recognised the way the tables are: it
    # has no header row, so every CSV profile would decline it and the file
    # would be reported as an unknown layout -- which is what happened, while
    # the parser for it sat one module away, reading every FinTS fetch.
    if formats.looks_like_mt940(text):
        entries = formats.mt940_entries(text)
        notes = {"header_row": None, "unreadable": 0, "unmatched": {},
                 "format": "mt940"}
        return _address_entries("mt940", entries, bank_account, notes)

    profile_name, columns, rows, header_index = formats.read_rows(text)

    notes = {"header_row": header_index, "unreadable": 0, "unmatched": {}}
    if not profile_name:
        return None, [], notes

    profile = PROFILES[profile_name]
    parsed = []
    for row in rows:
        if not any(str(cell).strip() for cell in row):
            continue
        entry = formats.to_entry(row, columns, profile)
        if entry is None:
            notes["unreadable"] += 1
            continue
        parsed.append(entry)

    return _address_entries(profile_name, parsed, bank_account, notes)


def _address_entries(profile_name, entries, bank_account, notes):
    """Give every entry the Bank Account it belongs to.

    Shared by both readers on purpose. The routing is the part that went wrong
    once already -- a portfolio-wide export booked onto whichever account was
    picked on the form -- and a second copy of it for a second file format is
    how that comes back.

    :return: (profile_name, addressed entries, notes)
    """
    by_iban = {}
    addressed = []

    for entry in entries:
        target = bank_account
        if entry.get("own_iban"):
            if entry["own_iban"] not in by_iban:
                by_iban[entry["own_iban"]] = _bank_account_for_iban(
                    entry["own_iban"])
            target = by_iban[entry["own_iban"]]
            if not target:
                key = "..." + str(entry["own_iban"])[-4:]
                notes["unmatched"][key] = notes["unmatched"].get(key, 0) + 1
                continue

        entry["bank_account"] = target
        addressed.append(entry)

    return profile_name, addressed, notes


def plan(file_url, bank_account=None, dry_run=True):
    """Read a file and report -- or perform -- what booking it would do.

    :return: the book_entries summary, plus profile, label and the parsed
        entries. Use without_entries() before it travels anywhere.
    """
    profile_name, entries, notes = read_entries(file_url, bank_account)

    if not profile_name:
        return dict(notes, profile=None, label=None, entries=[],
                    total=0, created=0, would_create=0, duplicates=0,
                    accounts={}, sample=[], dry_run=bool(dry_run),
                    reason=_("No known column layout was recognised in this "
                             "file."))

    summary = book_entries(entries, dry_run=dry_run)
    summary.update(notes)
    summary["profile"] = profile_name
    summary["label"] = PROFILES[profile_name]["label"]
    summary["entries"] = entries
    return summary


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@frappe.whitelist()
def ingest(bank_account, entries, profile=None, charges_are_positive=None,
           dry_run=1):
    """Book what an outside service fetched, deciding here rather than there.

    The transport fetches, this validates and books. The sign convention, the
    deduplication and the permission check belong on this side, where they are
    the same for every source.

    Defaults to a dry run. A bulk write that only happens when it was asked
    for by name cannot be triggered by a misconfigured workflow.
    """
    # Permission gate: a whitelisted endpoint is callable by any logged-in
    # user, and this one creates bookings on a named account.
    frappe.has_permission("Bank Account", ptype="write", doc=bank_account,
                          throw=True)
    frappe.has_permission("Bank Transaction", ptype="create", throw=True)

    if isinstance(entries, str):
        entries = frappe.parse_json(entries)
    if not isinstance(entries, (list, tuple)):
        frappe.throw(_("ingest expects a list of entries."))

    if charges_are_positive is not None:
        charges_are_positive = bool(cint(charges_are_positive))

    canonical, unreadable = [], 0
    for raw in entries:
        try:
            entry = normalise_pushed(
                raw if isinstance(raw, dict) else {},
                profile_name=profile,
                charges_are_positive=charges_are_positive)
        except ValueError:
            frappe.throw(_(
                "Unknown statement format {0}. Pass a known format or state"
                " the sign convention explicitly -- a card feed and a giro"
                " feed count the opposite way round."
            ).format(profile))
        if entry is None:
            unreadable += 1
            continue
        entry["bank_account"] = bank_account
        canonical.append(entry)

    summary = book_entries(canonical, dry_run=cint(dry_run))
    summary["unreadable"] = unreadable
    return summary


@frappe.whitelist()
def attach_statement(bank_account, filename, content_base64, period=None):
    """File a statement document that came from outside FinTS.

    The PSD2 account information route delivers bookings and balances and no
    documents at all -- there is no statement PDF in it, by design. A card
    issuer that speaks no FinTS therefore leaves the bookings reachable and
    the statements not.

    So a document service that holds that connection can hand the PDF over
    here, and it lands exactly where a fetched one lands: attached to the BANK
    ACCOUNT as a private file, named so that handing the same one over twice
    is recognised rather than filed twice. Same rule as
    fetch_persistence.download_statements().

    :return: {"stored": bool, "file": name|None, "reason": str|None}
    """
    frappe.has_permission("Bank Account", ptype="write", doc=bank_account,
                          throw=True)

    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(filename or "")).strip("-")
    if not safe:
        safe = "Kontoauszug-{0}".format(period or "unbenannt")
    if period and period not in safe:
        safe = "{0}-{1}".format(period, safe)

    existing = frappe.db.exists("File", {
        "attached_to_doctype": "Bank Account",
        "attached_to_name": bank_account,
        "file_name": safe,
    })
    if existing:
        return {"stored": False, "file": existing, "reason": _("already present")}

    try:
        payload = base64.b64decode(content_base64 or "", validate=True)
    except Exception:
        frappe.throw(_("The document could not be decoded."))
    if not payload:
        frappe.throw(_("The document is empty."))

    doc = frappe.get_doc({
        "doctype": "File",
        "file_name": safe,
        "attached_to_doctype": "Bank Account",
        "attached_to_name": bank_account,
        "is_private": 1,
        "content": payload,
    }).insert(ignore_permissions=True)
    return {"stored": True, "file": doc.name, "reason": None}
