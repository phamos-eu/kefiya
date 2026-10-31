# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Was aus einer geparkten Freigabe geworden ist -- und wann daraus
"Gesendet" werden darf.

Ein Modul ohne Importe, aus demselben Grund wie fints_errors.py: der
Controller und die TAN-Strecke brauchen beide dieselben vier Woerter, und
der Controller importiert die TAN-Strecke.

Warum es das gibt. Auftrag A, 03.09.2026, 12:45: Die Sparkasse
bat um die Freigabe in der App (3955), sagte auf die erste Statusabfrage
"noch ausstehend" (3956) und auf die zweite::

    9050 Die Nachricht enthaelt Fehler.
    9010 Der Auftrag wurde nicht ausgefuehrt.

python-fints macht aus der 9010 eine Exception. Der Konstruktor des
Controllers fing sie, raeumte auf -- und gab sie nicht weiter. Er lief
durch, und send_transfer_tan schloss aus "der Konstruktor lief durch" auf
"die Freigabe ist da" und schrieb "Gesendet". Im Online-Banking gab es die
Ueberweisung nicht. Im Fehlerprotokoll stand nichts.

Deshalb sagt der Controller jetzt ausdruecklich, was aus der Freigabe
wurde, und "Gesendet" gibt es nur fuer das eine Wort, das es verdient.
"""

#: Es war keine Anforderung geparkt. Der Konstruktor hatte nichts zu
#: beantworten -- und damit auch nichts freizugeben.
NOTHING_PARKED = "nothing_parked"

#: Die Bank hat die Freigabe noch nicht gesehen (3956). Weiter fragen.
PENDING = "pending"

#: Die Bank hat den Auftrag angenommen. Nur hier darf "Gesendet" stehen.
RELEASED = "released"

#: Die Antwort auf die Statusabfrage war ein Fehler. Der Auftrag ist nicht
#: freigegeben; was die Bank gesagt hat, steht daneben.
FAILED = "failed"


def may_mark_sent(outcome):
    """Darf ein Auftrag nach dieser Freigabe als gesendet gelten?

    Genau ein Wort erlaubt es. Alles andere -- auch None, auch ein Wort, das
    es noch nicht gibt -- heisst nein: ein Auftrag, ueber den nichts bekannt
    ist, ist kein gesendeter Auftrag.
    """
    return outcome == RELEASED
