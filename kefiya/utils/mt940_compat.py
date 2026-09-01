# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One repair for the mt940 parser, so pending entries can be read at all.

The bank puts a date-and-time indication in the pending block of an MT940
statement (tag :13D:), and the timezone offset in it is optional. The mt940
library's own pattern says so::

    (\\+(?P<offset>\\d{4})|)

A regex group that does not participate still lands in the match dictionary --
with the value None. The parser then decides what to do with it by asking
whether the KEY is there, not whether it has a value::

    elif 'offset' in kwargs:
        tzinfo = FixedOffset(kwargs.pop('offset'))

and FixedOffset does ``int(None)``. So every Sparkasse that sends the pending
block without a timezone crashes the parse with

    TypeError: int() argument must be a string, a bytes-like object or a real
    number, not 'NoneType'

which is what four accounts of one collective run reported under "Vorgemerkt".
The booked block parses fine, which is why only the pending fetch was affected.

The shim below makes the None case mean what the optional group says it means:
no timezone given. It cannot change any behaviour that works today -- the only
thing it replaces is an exception. It is installed once, lazily, and is a
no-op on a library version that has fixed this itself.
"""

import frappe

_INSTALLED = False


def ensure_optional_timezone_is_optional():
    """Install the repair once. Safe to call on every parse.

    :return: True when the parser can handle a missing offset afterwards
    """
    global _INSTALLED
    if _INSTALLED:
        return True

    try:
        from mt940 import models
    except Exception:
        return False

    original = models.DateTime.__new__

    def patched(cls, *args, **kwargs):
        # Only the broken case is touched: the key is present and empty.
        if kwargs.get("offset", "unset") is None:
            kwargs.pop("offset")
        return original(cls, *args, **kwargs)

    try:
        # A library that has fixed this itself needs nothing from us -- check
        # rather than assume, so this quietly retires when it becomes moot.
        models.DateTime(year="26", month="07", day="31", hour="09",
                        minute="00", offset=None)
        _INSTALLED = True
        return True
    except TypeError:
        pass
    except Exception:
        return False

    try:
        models.DateTime.__new__ = patched
        models.DateTime(year="26", month="07", day="31", hour="09",
                        minute="00", offset=None)
    except Exception:
        models.DateTime.__new__ = original
        frappe.log_error(
            title="Kefiya: could not repair the MT940 date parser",
            message=frappe.get_traceback(),
        )
        return False

    _INSTALLED = True
    return True
