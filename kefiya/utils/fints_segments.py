# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""FinTS segments for the dated SEPA credit transfer (Terminueberweisung).

python-fints ships every SEPA order segment except this one: it can send a
transfer now (HKCCS/HKCCM), send it as an instant payment (HKIPZ/HKIPM) and
READ the dated orders a bank already holds (HKDBS/HKCSB), but it cannot HAND
a dated order to the bank. That is the difference between "we keep the payment
until its date" and "the bank keeps it" -- and a user who picks the second one
in StarMoney expects the bank to hold it, not us.

The segments below fill exactly that gap. They are not invented: HKCSE has the
same shape as the dated direct debit HKDSE1 that the library does ship, which
is the same shape as HKCCS1 -- account, descriptor, pain message. The execution
date is not a segment field at all; it travels inside the pain.001 as
ReqdExctnDt. The collective form HKCME mirrors HKCCM1 the same way.

Defining a FinTS3Segment subclass is all the registration there is: the library
finds command and response classes through SubclassesMixin, by name. Importing
this module is therefore enough to make HKCSE available to the client, and
HICSE parse into a typed object instead of a nameless segment.

The bank still decides. If its BPD carries no HICSES parameter segment, the
account does not offer dated transfers and _find_highest_supported_command
raises FinTSUnsupportedOperation -- which the caller turns into "your bank
cannot hold this one, we will".
"""

from fints.fields import DataElementField, DataElementGroupField
from fints.formals import KTI1, Amount1, SupportedSEPAPainMessages1
from fints.segments.base import FinTS3Segment


class ScheduledTransferResponseBase(FinTS3Segment):
    """The order identifier the bank assigns to a dated order.

    Without it a scheduled transfer cannot later be changed (HKCSA) or
    cancelled (HKCSL), so it is stored on the document that produced it.
    """

    task_id = DataElementField(
        type="an", max_length=99, required=False,
        _d="Auftragsidentifikation")


class HKCSE1(FinTS3Segment):
    """Terminierte SEPA-Einzelueberweisung einreichen, version 1

    Source: FinTS Financial Transaction Services, Schnittstellenspezifikation,
    Messages -- Multibankfaehige Geschaeftsvorfaelle
    """

    account = DataElementGroupField(
        type=KTI1, _d="Kontoverbindung international")
    sepa_descriptor = DataElementField(
        type="an", max_length=256, _d="SEPA Descriptor")
    sepa_pain_message = DataElementField(type="bin", _d="SEPA pain message")


class HICSE1(ScheduledTransferResponseBase):
    """Einreichung terminierter SEPA-Einzelueberweisung bestaetigen, version 1

    Source: FinTS Financial Transaction Services, Schnittstellenspezifikation,
    Messages -- Multibankfaehige Geschaeftsvorfaelle
    """


class HKCME1(FinTS3Segment):
    """Terminierte SEPA-Sammelueberweisung einreichen, version 1

    Source: FinTS Financial Transaction Services, Schnittstellenspezifikation,
    Messages -- Multibankfaehige Geschaeftsvorfaelle
    """

    account = DataElementGroupField(
        type=KTI1, _d="Kontoverbindung international")
    sum_amount = DataElementGroupField(type=Amount1, _d="Summenfeld")
    request_single_booking = DataElementField(
        type="jn", _d="Einzelbuchung gewuenscht")
    sepa_descriptor = DataElementField(
        type="an", max_length=256, _d="SEPA Descriptor")
    sepa_pain_message = DataElementField(type="bin", _d="SEPA pain message")


class HICME1(ScheduledTransferResponseBase):
    """Einreichung terminierter SEPA-Sammelueberweisung bestaetigen, version 1

    Source: FinTS Financial Transaction Services, Schnittstellenspezifikation,
    Messages -- Multibankfaehige Geschaeftsvorfaelle
    """


class HKCDB1(FinTS3Segment):
    """Bestand SEPA-Dauerauftrag anfordern, version 1

    The library ships no standing-order segment at all -- not the query, not
    the order. This is the query, and only the query: reading cannot move
    money, and a request the bank does not understand is answered with a code,
    not with a wrong payment.

    Its shape is HKDBS1 without the date range, which is the segment the
    library does ship for the neighbouring business transaction (the bestand of
    scheduled direct debits). A standing order has no date window to ask for --
    it has a cycle -- so those two elements are the only difference.

    Source: FinTS Financial Transaction Services, Schnittstellenspezifikation,
    Messages -- Multibankfaehige Geschaeftsvorfaelle
    """

    account = DataElementGroupField(
        type=KTI1, _d="Kontoverbindung international")
    supported_sepa_pain_messages = DataElementGroupField(
        type=SupportedSEPAPainMessages1, _d="Unterstuetzte SEPA pain messages")
    max_number_responses = DataElementField(
        type="num", max_length=4, required=False,
        _d="Maximale Anzahl Eintraege")
    touchdown_point = DataElementField(
        type="an", max_length=35, required=False, _d="Aufsetzpunkt")


# HICDB is deliberately NOT declared.
#
# The answer carries the standing order's schedule -- cycle, rhythm, day of
# execution, first and last run -- and the exact order of those elements is
# the one thing about this business transaction that could not be checked
# against anything the library ships. Declaring it would mean guessing, and a
# guess that parses is worse than one that fails: it would put the day of
# execution where the rhythm belongs and look perfectly plausible doing it.
#
# An undeclared segment keeps every element in `_additional_data`, groups
# included, exactly as the bank sent them. So the answer is read positionally
# for the part that is certain (account, descriptor, pain message) and kept raw
# for the part that is not. The raw part is what will settle the field order
# against real orders from real banks -- and only then is it safe to write one.


def read_task_id(response):
    """Pull the bank's order identifier out of a dated-transfer response.

    Mirrors what python-fints does for the dated direct debit: the identifier
    normally arrives on HICSE/HICME, and where a bank omits it there, the TAN
    response carries it as a task reference instead.

    Returns None when neither says one -- a missing identifier only means the
    order cannot be changed remotely later, not that it was rejected.
    """
    if response is None:
        return None

    try:
        for segment in response.find_segments(ScheduledTransferResponseBase):
            task_id = getattr(segment, "task_id", None)
            if task_id:
                return str(task_id)
    except Exception:
        pass

    try:
        for segment in response.find_segments("HITAN"):
            reference = getattr(segment, "task_reference", None)
            if reference:
                return str(reference)
    except Exception:
        pass

    return None
