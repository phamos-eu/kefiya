# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Wann ein Abruf als erfolgreich gilt -- und wann eben nicht.

Am Bankkonto steht bisher nur, wann es zuletzt *versucht* wurde
(``Kefiya Login.last_fetch_attempt``, gestempelt auf beiden Wegen, dem
gelungenen wie dem gescheiterten). Ein Versuch ist keine Auskunft. Was
niemand nachsehen konnte: wann die Bank zuletzt tatsaechlich geantwortet
hat.

Das hat drei Fehlalarme gekostet. ``ax_bank_transaction_freshness`` meldete
am 29./23.09.2026 fuer drei Konten "Bankabruf ohne frische Daten", weil der
juengste Umsatz aelter war als ein aus dem Buchungsrhythmus geschaetzter
Grenzwert. In allen drei Faellen lief der Abruf taeglich und einwandfrei --
die Bank hatte nur nichts zu melden. Beim Privatkonto waren es acht ruhige
Tage bei einem echten Abstand von 2,9 Tagen, bei ein Volksbank-Konto 23
ruhige Tage bei einem groessten beobachteten Abstand von 31.

Daraus die eine Regel, die dieses Modul ausspricht:

    **Keine Buchung ist eine Antwort.** Ein Abruf, der eine leere Liste
    zurueckbringt, war erfolgreich -- die Bank hat gesagt "in diesem Fenster
    liegt nichts". Genau dieser Fall sah bisher aus wie ein Ausfall.

Und die beiden Gegenstuecke:

    **Eine TAN-Aufforderung ist keine Antwort.** Die Bank hat die Frage nicht
    beantwortet, sondern zurueckgefragt. Wer das als Erfolg stempelt, sagt
    "abgerufen" ueber ein Konto, von dem nichts vorliegt -- und das ist die
    Luege, die der Waechter nicht mehr aufdecken koennte.

    **Ein 9xxx ist keine Antwort.** Dass die Antwort die Bibliothek erreicht
    hat, heisst nicht, dass die Bank geliefert hat. Siehe fints_response: die
    Sendestrecke hat genau diesen Unterschied einmal verwechselt.

Ohne frappe, aus demselben Grund wie fints_response, fetch_window und
release_pacing: was entscheidet, ob ein Konto als abgerufen gilt, muss ohne
Bank und ohne Bench pruefbar sein. Die einzige Abhaengigkeit ist
fints_response, das selbst nichts importiert.
"""

from kefiya.utils import fints_response

#: Das Feld am Bankkonto, in dem der Zeitpunkt steht. Custom Field auf der
#: Instanz -- wie custom_account_balance und custom_credit_line, und wie dort
#: wird stillschweigend nicht geschrieben, wo es nicht installiert ist.
STAMP_FIELD = "custom_last_successful_fetch"


def the_bank_answered(delivered, challenged=False, verdict=None):
    """Hat die Bank die Umsatzabfrage beantwortet?

    Nur dann darf am Konto stehen, dass abgerufen wurde.

    :param delivered: was aus der Umsatzabfrage zurueckkam. ``None`` heisst
        "gar nichts"; eine leere Liste heisst "die Bank sagt: nichts da",
        und das ist eine Antwort.
    :param challenged: die Bank hat mit einer TAN-/Freigabe-Aufforderung
        geantwortet statt mit Daten.
    :param verdict: falls vorhanden, das Urteil aus
        ``fints_response.verdict_of()``. Nur ein ausdrueckliches 9xxx
        blockiert; eine Warnung (3xxx) ist eine angenommene Antwort mit
        Anmerkung, genau wie in fints_response.refused().
    :return: bool
    """
    if challenged:
        return False
    if delivered is None:
        return False
    if verdict is not None and fints_response.refused(verdict):
        return False
    return True


def how_many(delivered):
    """Wie viele Umsaetze die Bank geliefert hat, fuer den Logeintrag.

    Rein beschreibend: die Zahl entscheidet nichts -- ``the_bank_answered``
    ist bei 0 genauso wahr wie bei 18. Sie steht im Fehlertext daneben,
    damit ein Leser den ruhigen Fall vom stummen unterscheiden kann.
    """
    try:
        return len(delivered)
    except TypeError:
        return 0 if delivered is None else 1
