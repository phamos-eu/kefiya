# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The bank's answer to HKCDB, written down exactly as it came.

Without frappe and without a bank, like fints_response: what this formats is
the evidence that settles how a standing order's schedule is laid out, and a
formatter that only runs against a bank is checked by nobody.

Why it exists. HICDB is read without a declared segment (see
fints_segments): cycle, rhythm, day of execution, first and last run come in
an order nothing in python-fints confirms. standing_orders reads the certain
part by position and keeps the rest raw -- and then threw the raw part away,
because the fetch only reported a count. One real answer per bank is what
turns "probably the rhythm" into "the rhythm", and only then may kefiya write
standing orders into a list or, later, to the bank.

Every element is kept, with its position and its type. Nothing is
interpreted here. The text goes into a private file on the Kefiya Login, not
into the Error Log -- an answer the bank gave without complaint is no error.
"""

#: A pain message can be long. Enough for one order, not a whole statement.
MAX_TEXT = 4000

#: Nested groups deeper than this are a parse problem, not evidence.
MAX_DEPTH = 6


def describe(value, depth=0):
    """One element as text: groups as [ ... ], bytes decoded, None as such."""
    if depth > MAX_DEPTH:
        return "<too deep>"
    if value is None:
        return "None"
    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode("utf-8", "replace")
        except Exception:
            return "<bytes {0}>".format(len(value))
        return "b" + repr(text[:MAX_TEXT])
    if isinstance(value, str):
        return repr(value[:MAX_TEXT])
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(describe(v, depth + 1) for v in value) + "]"
    return "{0}({1})".format(type(value).__name__, repr(value)[:MAX_TEXT])


def segment_lines(segment):
    """One HICDB segment: its header, then every element by position."""
    header = getattr(segment, "header", None)
    lines = ["{0} v{1} #{2}".format(
        getattr(header, "type", "?"), getattr(header, "version", "?"),
        getattr(header, "number", "?"))]
    try:
        data = list(getattr(segment, "_additional_data", None) or [])
    except Exception:
        data = []
    if not data:
        lines.append("  (no elements kept)")
    for index, value in enumerate(data):
        lines.append("  [{0}] {1}".format(index, describe(value)))
    return lines


def file_name(day):
    """The attachment's name: one per access and day of fetching."""
    return "dauerauftraege-bankantwort-{0}.txt".format(day)


def message(login, segments, said=None, parsed=None):
    """The text of one answer, for the file on the login. Never raises."""
    try:
        out = ["login={0}".format(login),
               "HICDB segments: {0}".format(len(segments or []))]
        if said:
            out.append("What the bank said to HKCDB: {0}".format(said))
        for number, segment in enumerate(segments or [], start=1):
            out.append("")
            out.append("--- standing order {0} ---".format(number))
            out.extend(segment_lines(segment))
            if parsed and len(parsed) >= number:
                out.append("  read as: {0}".format(
                    describe_reading(parsed[number - 1])))
        return "\n".join(out)
    except Exception:
        return "login={0}\n(the answer could not be written down)".format(
            login)


def describe_reading(row):
    """What parse_standing_order made of it, for comparing with the raw."""
    if not isinstance(row, dict):
        return repr(row)[:MAX_TEXT]
    keys = ("amount", "recipient_name", "recipient_iban", "purpose",
            "task_id", "flags", "schedule")
    return ", ".join("{0}={1!r}".format(k, row.get(k)) for k in keys
                     if k in row)
