# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Reading what the bank already holds: standing orders and scheduled debits.

Two business transactions that are easy to confuse and were confused here.

  HKDBS  Bestand terminierter SEPA-Einzellastschriften -- money we COLLECT,
         once, on a date. python-fints ships it.
  HKCDB  Bestand SEPA-Dauerauftrag -- money we PAY, again and again, on a
         cycle. python-fints ships nothing for it at all.

The collective log called the first one "Daueraufträge" and never asked for the
second. So the line that claimed to report standing orders reported a different
business transaction entirely -- and reported it as empty, for a reason worth
writing down.

**The library's query cannot return anything.** get_scheduled_debits() asks
response_segments() to filter the bank's answer by ``"HKDBS"`` -- the name of
the *request*. An answer never contains that; it contains HIDBS. The filter
matches nothing, every time, at every bank::

    command_classes = (HKDBS1, HKDBS2)
    response_type = "HKDBS"          # <- HIDBS

So the query is issued here rather than through the library, with the response
type the bank actually sends. Nothing is patched: the library keeps its
behaviour, this module simply does not use the broken path.

For HKCDB the answer is read WITHOUT a declared response segment. The schedule
it carries -- cycle, rhythm, day, first and last run -- is the one part of this
business transaction that could not be checked against anything the library
ships, and a guessed field order that happens to parse is worse than one that
fails: it would put the day of execution where the rhythm belongs and look
entirely plausible. An undeclared segment keeps every element as the bank sent
it, so the certain part is read by position and the rest is kept raw. That raw
part is the evidence that will settle the order -- and only then is it safe to
write a standing order rather than read one.
"""

import re

import frappe
from frappe.utils import flt


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")
        except Exception:
            return ""
    return str(value)


def _pain_summary(payload):
    """Amount, recipient and purpose out of a pain.001 message.

    Deliberately by regular expression and not by an XML parser: the message
    arrives as raw bytes from a bank, the shape varies by pain version, and a
    strict parse that raises would cost the whole fetch. What is missing stays
    empty, which reads as "the bank did not say" -- the truthful answer.
    """
    xml = _text(payload)
    if not xml:
        return {}

    def one(pattern):
        match = re.search(pattern, xml, re.S)
        return (match.group(1).strip() if match else "")

    amount = one(r"<InstdAmt[^>]*>([^<]+)</InstdAmt>")
    return {
        "amount": flt(amount) if amount else 0.0,
        "currency": one(r'<InstdAmt[^>]*Ccy="([^"]+)"') or None,
        "recipient_name": one(r"<Cdtr>.*?<Nm>([^<]+)</Nm>"),
        "recipient_iban": one(r"<CdtrAcct>.*?<IBAN>([^<]+)</IBAN>"),
        "purpose": one(r"<RmtInf>.*?<Ustrd>([^<]+)</Ustrd>"),
        "end_to_end_id": one(r"<EndToEndId>([^<]+)</EndToEndId>"),
    }


def _schedule_from(raw):
    """Best reading of the standing order's schedule -- clearly marked as
    such. Read by shape, not by position; see bank_held_orders."""
    from kefiya.utils import bank_held_orders

    return bank_held_orders.schedule_from(raw)


def parse_standing_order(segment):
    """One HICDB segment into a readable row.

    Only the first three elements are read by position -- account, descriptor,
    pain message -- because only those are certain. Everything after them is
    kept as the bank sent it.
    """
    data = list(getattr(segment, "_additional_data", None) or [])
    row = {"raw_elements": len(data)}

    if len(data) > 2:
        row.update(_pain_summary(data[2]))
    if len(data) > 1:
        row["sepa_descriptor"] = _text(data[1])

    # The identifier and the two flags follow the pain message in every SEPA
    # bestand segment the library ships. Read defensively all the same: a
    # bank that orders them differently must not turn into a wrong schedule.
    tail = data[3:]
    for value in tail:
        if isinstance(value, (list, tuple)):
            row["schedule"] = _schedule_from(value)
            break
    text_tail = [_text(v) for v in tail if not isinstance(v, (list, tuple))]
    if text_tail:
        row["task_id"] = text_tail[0]
    row["flags"] = [v for v in text_tail[1:] if v in ("J", "N")]
    row["cancelable"] = "J" in row["flags"][:1]
    row["changeable"] = "J" in row["flags"][1:2]
    row["tail_raw"] = text_tail
    return row


#: How long one written-down answer per access is enough. The fetch runs
#: daily; the evidence is needed once per bank, not once per day.
EVIDENCE_EVERY_SECONDS = 7 * 24 * 3600


def _write_down_the_answer(login, command_seg, response, segments, rows):
    """The bank's answer, raw, as a private file on the Kefiya Login.

    Once a week per access. Not the Error Log: that is for errors, and an
    answer the bank gave without complaint is not one. The file sits where
    the access is, in the form's attachments.

    The schedule's field order is unconfirmed, and the raw elements are what
    confirms it. They used to be read and dropped: the fetch reported a count.
    Never raises; a fetch must not fail over its own notebook.
    """
    from kefiya.utils import standing_order_evidence

    if not login:
        return
    try:
        key = "kefiya:standing_order_evidence:{0}".format(login)
        if frappe.cache().get_value(key):
            return
        try:
            said = "; ".join(
                "{0} {1}".format(getattr(r, "code", "?"),
                                 getattr(r, "text", ""))
                for r in response.responses(command_seg))
        except Exception:
            said = ""
        frappe.get_doc({
            "doctype": "File",
            "file_name": standing_order_evidence.file_name(
                frappe.utils.today()),
            "attached_to_doctype": "Kefiya Login",
            "attached_to_name": login,
            "is_private": 1,
            "content": standing_order_evidence.message(
                login, segments, said=said, parsed=rows),
        }).insert(ignore_permissions=True)
        # Only an answer with orders in it settles the schedule. A refusal
        # must not keep the first real answer from being written down.
        if segments:
            frappe.cache().set_value(key, 1,
                                     expires_in_sec=EVIDENCE_EVERY_SECONDS)
    except Exception:
        pass


def _pain_messages(connection, command_class, family="pain.001"):
    """The mandatory "Unterstuetzte SEPA pain messages" of a bestand query,
    from the bank's own list (HISPAS).

    Left empty, both banks asked answered 9110/9160 -- see bank_held_orders.
    """
    from kefiya.utils import bank_held_orders, sepa_descriptor

    try:
        listed = sepa_descriptor.formats_in(
            connection.bpd.find_segment_first("HISPAS"))
    except Exception:
        listed = []
    deg = command_class._fields["supported_sepa_pain_messages"].type
    return deg(sepa_descriptors=bank_held_orders.pain_messages(
        listed, family=family))


def _fetch_pages(connection, dialog, make_segment, response_type,
                 on_answer=None):
    """Send a bestand query and follow the bank's touchdown points.

    The library's _fetch_with_touchdowns does the same, but it reads a 9xxx
    answer to the segment as an empty page. Here it raises
    BankRefusedQuery, so the fetch log says the bank refused the question
    instead of reporting that the bank holds nothing.

    :param make_segment: callable(touchdown) -> command segment
    :param response_type: the answer segment, e.g. "HICSB"
    :param on_answer: optional callable(command_seg, response, segments),
        called for every page before its verdict is read
    :return: list of answer segments over all pages
    """
    from kefiya.utils import bank_held_orders

    collected = []
    pages = [0]

    def resume(command_seg, response):
        segments = list(response.response_segments(
            command_seg, response_type))
        if on_answer:
            on_answer(command_seg, response, segments)
        refused = bank_held_orders.refusals(response, command_seg)
        if refused:
            raise bank_held_orders.BankRefusedQuery(
                command_seg.header.type, refused)
        collected.extend(segments)
        pages[0] += 1
        touchdown = bank_held_orders.touchdown_of(response, command_seg)
        if touchdown and pages[0] < bank_held_orders.MAX_PAGES:
            return connection._send_with_possible_retry(
                dialog, make_segment(touchdown), resume)
        return collected

    result = connection._send_with_possible_retry(
        dialog, make_segment(None), resume)
    if not isinstance(result, list):
        # A NeedTANResponse: the bank wants a TAN for reading its bestand.
        # Nothing parks it on this path, so say so instead of reading the
        # challenge as an empty bestand.
        raise bank_held_orders.BankRefusedQuery(
            response_type, ["the bank asks for a TAN to read this"])
    return collected


def fetch_standing_orders(connection, account, login=None):
    """Ask the bank which standing orders it holds for this account.

    :param login: the Kefiya Login asking, for the written-down answer
    :return: list of rows from parse_standing_order()
    :raises FinTSUnsupportedOperation: where the bank does not offer HKCDB
    :raises BankRefusedQuery: where the bank answers the query with 9xxx
    """
    from kefiya.utils.fints_segments import HKCDB1

    rows = []

    def note(command_seg, response, segments):
        page = []
        for segment in segments:
            try:
                page.append(parse_standing_order(segment))
            except Exception:
                # One unreadable order must not lose the others.
                frappe.log_error(
                    title="Kefiya: could not read a standing order",
                    message=frappe.get_traceback())
        rows.extend(page)
        _write_down_the_answer(login, command_seg, response, segments, page)

    with connection._get_dialog() as dialog:
        hkcdb = connection._find_highest_supported_command(HKCDB1)
        pain = _pain_messages(connection, hkcdb)
        _fetch_pages(
            connection, dialog,
            lambda touchdown: hkcdb(
                account=hkcdb._fields["account"].type.from_sepa_account(
                    account),
                supported_sepa_pain_messages=pain,
                touchdown_point=touchdown,
            ),
            "HICDB", on_answer=note)
    return rows


def fetch_scheduled_transfers(connection, account):
    """The dated transfers the bank holds for this account -- HKCSB.

    What StarMoney lists as "terminierte Ueberweisungen". The forecast used to
    ask only for the dated direct debits (HKDBS), which a payer never has.
    The query has the shape of the library's HKDBS; see fints_segments.

    :return: list of HICSB segments
    :raises FinTSUnsupportedOperation: where the bank does not offer HKCSB
    :raises BankRefusedQuery: where the bank answers the query with 9xxx
    """
    from kefiya.utils.fints_segments import HKCSB1

    with connection._get_dialog() as dialog:
        hkcsb = connection._find_highest_supported_command(HKCSB1)
        pain = _pain_messages(connection, hkcsb)
        return _fetch_pages(
            connection, dialog,
            lambda touchdown: hkcsb(
                account=hkcsb._fields["account"].type.from_sepa_account(
                    account),
                supported_sepa_pain_messages=pain,
                touchdown_point=touchdown,
            ),
            "HICSB")


def fetch_scheduled_debits(connection, account):
    """The bestand of scheduled direct debits -- HKDBS, asked correctly.

    Same query the library offers, with the response filtered by what a bank
    actually sends. See the module docstring for why the library's own path
    returns nothing.

    :return: list of HIDBS segments
    :raises FinTSUnsupportedOperation: where the bank does not offer HKDBS
    """
    from fints.segments.debit import HKDBS1, HKDBS2

    with connection._get_dialog() as dialog:
        hkdbs = connection._find_highest_supported_command(HKDBS1, HKDBS2)
        return connection._fetch_with_touchdowns(
            dialog,
            lambda touchdown: hkdbs(
                account=hkdbs._fields["account"].type.from_sepa_account(
                    account),
                touchdown_point=touchdown,
            ),
            lambda responses: responses,
            # HIDBS, not HKDBS: the bank answers with its own segment, not
            # with a copy of the request.
            "HIDBS",
        )
