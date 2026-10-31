# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Wie lange die Kette Luft laesst, nachdem eine Freigabe gegeben wurde.

Gemessen am 28.09.2026, Sammelabruf von Hand, 12:15 bis 12:19. Dreissig
Volksbank-Konten haengen an EINEM Zugang und laufen als EINE Kette,
nacheinander. Vierzehn davon waren an der Reihe, sieben haben eine Freigabe
in der App verlangt -- und zwar in diesem Takt::

    12:15:22  Konto 1
    12:15:46  Konto 2    24 s spaeter
    12:17:02  Konto 3    76 s
    12:17:14  Konto 4    12 s
    12:17:29  Konto 5    15 s
    12:17:55  Konto 6    26 s
    12:19:25  Konto 7    90 s

Die Abstaende sind nicht der Bank ihrer, sondern die Reaktionszeit des
Nutzers: kefiya wartet auf die Freigabe und geht in der Sekunde, in der sie
da ist, zum naechsten Konto -- das dann sofort die naechste Anfrage stellt.
Zwoelf Sekunden zwischen zwei Anfragen, waehrend das Telefon noch in der
Hand liegt. "Die Volksbank-Konten werden so schnell abgerufen, dass ich
keine Zeit habe, alle gleich in der App freizugeben."

Also eine Verschnaufpause: nach einer gegebenen Freigabe wartet die Kette,
bevor das naechste Konto desselben Zugangs fragt. Sie kostet nur dort Zeit,
wo tatsaechlich jemand freigegeben hat -- lag die letzte Freigabe laenger
zurueck als die Pause, ist die Antwort null, und ein Lauf ohne Freigaben
(der naechtliche zum Beispiel) haelt nirgends an.

Ausgelassen wird nichts. "Wenn ich den Abruf wieder will, soll er
ausgefuehrt werden."

Ohne frappe im Modulkopf, aus demselben Grund wie bei fints_dialog_state:
die Regel soll ohne Bench zu pruefen sein. Die beiden Zeilen, die sich den
Zeitpunkt merken, holen frappe dort, wo sie es brauchen.
"""

import datetime

#: Wie lange nach einer Freigabe gewartet wird, bevor das naechste Konto
#: desselben Zugangs die naechste anfordert. Lang genug, um das Telefon
#: wegzulegen; kurz genug, dass sieben davon den Lauf um fuenf Minuten
#: verlaengern und nicht um eine halbe Stunde.
BREATHER_SECONDS = 45

#: Wo der Zeitpunkt der letzten Freigabe im Lauf steht. Ein Job, ein
#: frappe.local -- und der Sammelabruf eines Zugangs ist genau ein Job.
STORE = "kefiya_last_release_at"


def _als_zeitpunkt(wert):
    """Was uebergeben wurde, als datetime -- oder None."""
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


def seconds_to_wait(last_release, now=None, breather=BREATHER_SECONDS):
    """Wie viele Sekunden das naechste Konto noch wartet.

    Null, wo nichts zu warten ist: keine Freigabe im Lauf, ein unlesbarer
    Zeitpunkt, eine Pause von null -- und eine Freigabe, die laenger zurueck
    liegt als die Pause. Im Zweifel wird nicht gewartet: eine Pause zu
    wenig kostet Bequemlichkeit, eine Pause zu viel kostet einen Abruf,
    der in der Job-Zeit nicht mehr fertig wird.

    Eine Uhr, die zurueckspringt, ergibt ebenfalls null und nicht das
    Doppelte der Pause.
    """
    try:
        pause = float(breather or 0)
    except (TypeError, ValueError):
        return 0.0
    if pause <= 0:
        return 0.0

    zuletzt = _als_zeitpunkt(last_release)
    if zuletzt is None:
        return 0.0

    jetzt = now or datetime.datetime.now()
    try:
        vergangen = (jetzt - zuletzt).total_seconds()
    except TypeError:
        return 0.0
    if vergangen < 0:
        return 0.0
    return max(0.0, pause - vergangen)


def note_release(now=None):
    """Sich merken, dass gerade jemand freigegeben hat.

    Best effort und nie werfend: der Abruf, den die Freigabe eben
    freigeschaltet hat, darf nicht daran scheitern, dass die Uhrzeit nicht
    notiert werden konnte.
    """
    try:
        import frappe

        setattr(frappe.local, STORE, now or datetime.datetime.now())
    except Exception:
        pass


def last_release():
    """Wann im laufenden Abruf zuletzt freigegeben wurde, oder None."""
    try:
        import frappe

        return getattr(frappe.local, STORE, None)
    except Exception:
        return None
