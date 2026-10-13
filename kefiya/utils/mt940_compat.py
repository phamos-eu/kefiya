# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Two repairs for the mt940 parser: the pending block, and the Verwendungszweck.

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


_SPACES_INSTALLED = False


def ensure_the_purpose_keeps_its_spaces():
    """Install the Verwendungszweck repair once. Safe to call on every parse.

    Die Teilfelder ``?20`` bis ``?29`` des Feldes :86: setzt die Bibliothek in
    ``_join_result()`` zusammen::

        value = ' '.join(result.get(key, [])) if space else ''.join(...)

    und ``fints.utils.mt940_to_array()`` baut ``Transactions()`` ohne
    Argumente, also mit ``space=False``. Deshalb steht auf jedem
    Rechnungsabschluss dieser Instanz::

        RechnungKosten SRZDauerrechnungsnummer.20250908-BW035-00038612361

    Der Schalter ``space`` gilt fuer den ganzen Lauf, die richtige Antwort
    aber je Buchung: Zeilen der Bank brauchen den Trenner, hart umbrochener
    SEPA-Text darf ihn nicht bekommen. Welcher Fall vorliegt, entscheidet
    verwendungszweck.mit_leerzeichen() an den DK-Schluesselwoertern.

    Angesetzt wird am Eintrag in ``Transactions.DEFAULT_PROCESSORS``, nicht am
    Modulnamen: der Eintrag ist eine direkte Referenz auf die Funktion, und
    jede neue ``Transactions``-Instanz teilt sich dieselbe Liste. Ein Ersatz
    von ``processors.transaction_details_post_processor`` wuerde deshalb
    nichts bewirken -- nachgesehen auf der Instanz, nicht angenommen.

    :return: True, wenn der Verwendungszweck danach seine Leerzeichen behaelt
    """
    global _SPACES_INSTALLED
    if _SPACES_INSTALLED:
        return True

    try:
        from mt940 import models, processors

        from kefiya.utils import verwendungszweck
    except Exception:
        return False

    try:
        kette = models.Transactions.DEFAULT_PROCESSORS["post_transaction_details"]
    except Exception:
        return False

    original = processors.transaction_details_post_processor

    def je_buchung(transactions, tag, tag_dict, result, space=False):
        # Der rohe :86:-Inhalt dieser einen Buchung. Faellt er aus, bleibt es
        # beim Verhalten der Bibliothek -- ein Verwendungszweck ohne
        # Leerzeichen ist schlechter lesbar, ein abgebrochener Abruf ist
        # schlimmer.
        try:
            roh = tag_dict.get("transaction_details") or ""
            space = verwendungszweck.mit_leerzeichen(str(roh))
        except Exception:
            pass
        return original(transactions, tag, tag_dict, result, space=space)

    ersetzt = 0
    try:
        for index, vorhanden in enumerate(list(kette)):
            if vorhanden is original:
                kette[index] = je_buchung
                ersetzt += 1
    except Exception:
        return False

    if not ersetzt:
        # Eine Bibliotheksfassung, die das selbst entscheidet oder ihre Kette
        # anders haelt, braucht von uns nichts. Still nichts tun ist richtig;
        # der Verwendungszweck ist dann entweder schon in Ordnung oder diese
        # Stelle ist nicht mehr die richtige.
        try:
            frappe.logger("kefiya").info(
                "Kefiya: mt940 purpose processor not found, left untouched")
        except Exception:
            pass
        return False

    _SPACES_INSTALLED = True
    return True
