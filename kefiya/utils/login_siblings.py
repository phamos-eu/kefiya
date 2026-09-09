# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein zweiter Zugang fuer ein Konto, das dieselben Zugangsdaten erreichen.

Eine VR-NetKey, eine PIN -- und die Bank nennt darunter vierzig Konten.
kefiya braucht je Konto einen Kefiya Login, und bisher wurde der von Hand
noch einmal getippt: URL, BLZ, Kennung, **PIN** und Produkt-ID. Genau die
beiden letzten sind das, was nicht ein zweites Mal durch einen Browser
laufen soll, und eine falsch getippte PIN sperrt bei drei Versuchen den
Zugang.

Hier steht die Regel dazu, ohne frappe und ohne fints: welche Konten eines
Zugangs noch keinen Login haben, was ein neuer Login vom alten uebernimmt
-- und vor allem, was er nicht uebernimmt. Der gespeicherte Dialogzustand
gehoert einem laufenden Gespraech mit der Bank: eine geparkte TAN, eine
offene VoP-Freigabe. Mitkopiert wuerde der neue Login glauben, es sei eine
TAN offen, die es nie gab.
"""

import json

#: Was ein zweiter Login des Zugangs uebernimmt: die Zugangsdaten und das,
#: was die Bank ueber den Zugang gesagt hat.
COPIED = (
    "fints_url",
    "blz",
    "fints_login",
    "fints_password",
    "product_id",
    "allowed_sync_days_in_past",
    "iban_list",
)

#: Die zwei davon, die verschluesselt liegen. Sie werden ueber
#: get_password() gelesen, nicht ueber get() -- und nie an den Browser
#: gereicht.
SECRETS = ("fints_password", "product_id")

#: Felder, die ein einzelnes Gespraech mit der Bank beschreiben, ohne dass
#: ihr Name es schon sagt.
_DIALOG = ("failed_connection", "last_fetch_attempt", "account_iban",
           "transfer_limit_amount", "transfer_limit_type",
           "transfer_limit_days", "transfer_limit_checked_on")


def normalise(iban):
    """Eine IBAN, wie sie verglichen wird: gross, ohne Leerzeichen."""
    return "".join(str(iban or "").split()).upper()


def named_by_bank(iban_list):
    """Die Konten, die die Bank fuer diesen Zugang genannt hat.

    ``iban_list`` ist das, was "Konten laden" gespeichert hat -- eine
    JSON-Liste. Steht dort nichts oder etwas Unlesbares, ist die Antwort
    leer: dann hat die Bank nichts gesagt, und geraten wird hier nicht.
    """
    if not iban_list:
        return []
    if isinstance(iban_list, (list, tuple)):
        roh = iban_list
    else:
        try:
            roh = json.loads(iban_list)
        except (ValueError, TypeError):
            return []
        if not isinstance(roh, (list, tuple)):
            return []
    return [normalise(x) for x in roh if normalise(x)]


def unclaimed(iban_list, already_used):
    """Die Konten des Zugangs, die noch keinen Login haben.

    In der Reihenfolge der Bank und ohne Wiederholung.
    """
    belegt = {normalise(x) for x in (already_used or [])}
    offen = []
    for iban in named_by_bank(iban_list):
        if iban not in belegt and iban not in offen:
            offen.append(iban)
    return offen


def belongs_to_one_dialog(fieldname):
    """Gehoert das Feld einem laufenden Gespraech statt dem Zugang?

    Die Frage steht hier, damit ein Test sie an COPIED stellen kann: was
    ein Gespraech beschreibt, darf ein zweiter Login nicht erben.
    """
    return (fieldname.startswith("stored_")
            or fieldname.startswith("vop_")
            or fieldname.endswith("_state_updated")
            or fieldname in _DIALOG)
