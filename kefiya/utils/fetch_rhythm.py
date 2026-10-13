# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Wie oft ein Zugang im Sammelabruf an die Reihe kommt.

Die Volksbank verlangt fuer jeden Auftrag eine Freigabe in ihrer App -- und
der Sammelabruf stellt ihr 30 Zugaenge hintereinander. Vierzehn davon hatten
in 90 Tagen keine einzige Buchung: Geschaeftsanteile, Avale, ein Sparkonto,
ruhende Girokonten. Sie bringen nichts und kosten jedes Mal eine Freigabe.

Gezaehlt am 25.09.2026 auf der Instanz:

    Geschaeftsanteile        3 Zugaenge, 0 Buchungen in 90 Tagen
    Avale                    4 Zugaenge, 2 davon ohne jede Buchung
    Girokonten              24 Zugaenge, 8 davon ohne jede Buchung

Deshalb ein Rhythmus: was sich von Natur aus selten bewegt, wird selten
abgerufen. Die Vorgabe haengt an der Kontoart, die die Bank selbst gemeldet
hat (HIUPD, am Kefiya Login), und laesst sich je Zugang ueberschreiben.

**Die Null am Zugang heisst "keine eigene Angabe", nicht "bei jedem Lauf".**
Das war zuerst andersherum gedacht und hat die ganze Regel wirkungslos
gemacht: ein Int-Feld in Frappe kennt kein "nicht gesetzt", die Migration
schreibt seinen Vorgabewert in jede bestehende Zeile, und so stand nach dem
Deploy an allen 44 Zugaengen eine 0. Sie schlug die Vorgabe der Kontoart --
und der erste Lauf danach rief wieder alle dreissig Volksbank-Zugaenge ab
(60 Importe am 26.09., zweimal dreissig, unveraendert). Wer ein ruhendes
Konto trotzdem oft will, traegt 1 ein: hoechstens taeglich.

Ausgelassen wird nur der SAMMELabruf. Wer ein Konto von Hand abruft, bekommt
es abgerufen; sonst waere aus einer Schonung eine Sperre geworden.

Ohne frappe und ohne Datum aus der Datenbank: was hier entschieden wird,
muss ohne Bench zu pruefen sein -- wie bei camt_shape und login_siblings.
"""

import datetime

#: Wie oft eine Kontoart hoechstens gebraucht wird, in Tagen. Was hier nicht
#: steht, wird bei jedem Lauf abgerufen -- ein Girokonto bewegt sich taeglich.
#:
#: Die Namen sind die Auswahlwerte von Kefiya Login.account_kind.
DEFAULT_DAYS = {
    "Cooperative Shares": 30,
    "Guarantee / Credit Line": 30,
    "Loan": 30,
    "Securities Account": 7,
}

#: Was "bei jedem Lauf" heisst -- und zugleich, was am Zugang "keine eigene
#: Angabe" heisst. Ein Int-Feld in Frappe kann beides nicht auseinanderhalten:
#: es ist nach der Migration ueberall 0. Also entscheidet hier die Kontoart,
#: und eine Angabe ab 1 schlaegt sie.
EVERY_RUN = 0


def interval_days(account_kind, setting=None):
    """Der Rhythmus eines Zugangs in Tagen.

    :param account_kind: was die Bank ueber das Konto gesagt hat
    :param setting: was am Zugang eingetragen ist. Ab 1 gilt es; 0, leer und
        Unsinn heissen "keine eigene Angabe" und ueberlassen die Antwort der
        Kontoart. Wer ein ruhendes Konto oft will, traegt 1 ein.
    """
    try:
        gewaehlt = int(setting)
    except (TypeError, ValueError):
        gewaehlt = EVERY_RUN
    if gewaehlt >= 1:
        return gewaehlt
    return DEFAULT_DAYS.get((account_kind or "").strip(), EVERY_RUN)


def _als_zeitpunkt(wert):
    """Was aus der Datenbank kommt, als datetime -- oder None."""
    if wert is None or wert == "":
        return None
    if isinstance(wert, datetime.datetime):
        return wert
    if isinstance(wert, datetime.date):
        return datetime.datetime(wert.year, wert.month, wert.day)
    text = str(wert).strip()
    for form in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text[:26], form)
        except ValueError:
            continue
    return None


def due(last_attempt, interval, now=None):
    """Ist dieser Zugang im Sammelabruf wieder faellig?

    Im Zweifel ja. Ein Zugang, der noch nie abgerufen wurde, ein unlesbares
    Datum, ein Rhythmus von null -- alles das fuehrt zum Abruf. Ausgelassen
    wird nur, was nachweislich noch nicht so lange her ist.
    """
    try:
        tage = int(interval or 0)
    except (TypeError, ValueError):
        return True
    if tage <= EVERY_RUN:
        return True
    zuletzt = _als_zeitpunkt(last_attempt)
    if zuletzt is None:
        return True
    jetzt = now or datetime.datetime.now()
    return (jetzt - zuletzt) >= datetime.timedelta(days=tage)


def due_now(row, now=None):
    """Dieselbe Frage an eine Zeile aus `Kefiya Login`."""
    row = row or {}
    return due(row.get("last_fetch_attempt"),
               interval_days(row.get("account_kind"),
                             row.get("fetch_interval_days")),
               now=now)


#: Wie der naechtliche Zeitplan seine eigene Frequenz meint, in Tagen. Stand
#: bis jetzt als FREQUENCY_GAP_DAYS in kefiya_schedule.py -- und das war der
#: Grund, warum der Rhythmus oben zunaechst nichts aenderte.
SCHEDULE_DAYS = {"Daily": 1, "Weekly": 7, "Monthly": 30}


def schedule_interval_days(import_frequency, account_kind, setting=None):
    """Der Abstand fuer den naechtlichen Lauf -- derselbe Rhythmus, ein Gatter.

    Am 27.09.2026, am Morgen nach dem Deploy des Rhythmus, rief kefiya
    zwischen 06:01 und 08:00 trotzdem wieder alle dreissig Volksbank-Zugaenge
    ab (30 Kefiya Imports, angelegt von Administrator). Der Sammelabruf war
    es nicht: der fragt seit dem Deploy `due_now` und haette sechzehn davon
    geschont. Es war der Zeitplan, der seine eigene Frequenz mitbringt --
    alle 57 Zeilen der `Kefiya Schedule` stehen auf "Daily" -- und
    `fetch_rhythm` nie gefragt hat. Zwei Regeln fuer dieselbe Frage, und die
    aeltere gewann jede Nacht.

    Also entscheidet hier der GROESSERE der beiden Abstaende: die Zeile im
    Zeitplan sagt, wie oft sie das Konto haben will, die Kontoart sagt, wie
    oft das Konto es ueberhaupt wert ist. "Daily" auf einem Geschaeftsanteil
    heisst danach alle 30 Tage, "Monthly" auf einem Girokonto bleibt
    monatlich. Beide Wege koennen ein Konto nur seltener abrufen, nie
    oefter -- eine Schonung, die sich nicht aus Versehen aufheben laesst.

    :param import_frequency: was in der Zeitplan-Zeile steht
    :param account_kind: was die Bank ueber das Konto gesagt hat
    :param setting: was am Zugang eingetragen ist (ab 1 gilt es)
    """
    aus_dem_plan = SCHEDULE_DAYS.get((import_frequency or "").strip(), 1)
    return max(aus_dem_plan, interval_days(account_kind, setting))
