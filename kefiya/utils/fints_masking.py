# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Shortening an account number for anything a human or a log may read."""


def mask_iban(value):
    """Country code and the last four digits, nothing in between.

    Error messages end up in the Error Log and prompts end up on screens that
    may be shared. Four digits tell the accounts of one login apart without
    writing a full account number into either.
    """
    if not value:
        return "<no IBAN>"
    value = str(value)
    if len(value) <= 6:
        return value
    return "{0}***{1}".format(value[:2], value[-4:])
