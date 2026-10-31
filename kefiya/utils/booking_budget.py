# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Was ein Konto schon hat -- und ob eine eingelesene Zeile davon eine ist.

Beim Einlesen einer Datei ist die Frage nicht "gibt es so eine Buchung?",
sondern "gibt es noch eine unverbrauchte davon?". Deshalb ein Budget und
keine Menge: der Auszug eines Kontos enthaelt neun Gebuehren von 5,10 EUR
am selben Tag, jede mit eigener Rechnungsnummer. Unter "gibt es so eine?"
haette das Konto eine davon bekommen und acht verloren.

Warum das Datum nicht genuegt
-----------------------------
Die Pruefung verglich Tag und Betrag auf den Tag genau. Am 15.08.2026 hat
ein CSV-Einlesen 863 Buchungen angelegt, von denen **230 schon da waren**;
gemessen am 25.09.2026 beim Aufraeumen:

    gleicher Tag     157
    1 Tag daneben     26
    2 Tage             9
    3 Tage             8
    4 Tage             8
    5 bis 7 Tage       3
    kein Partner     633   (echte Luecken, die die Datei gefuellt hat)

Die Verschiebung ist kein Zufall und kein Fehler der Datei: die Bank nennt
Buchungstag und Valuta, und welcher von beiden in einer Exportdatei steht,
entscheidet das Programm, das sie geschrieben hat. kefiya speichert den
Buchungstag (siehe CLAUDE.md). 73 der 230 Doppel waren allein deshalb nicht
zu erkennen.

Also ein Fenster. Es ist bewusst schmal, und die Richtung des Irrtums ist
bewusst gewaehlt: eine zurueckgehaltene echte Buchung faellt beim
Saldenvergleich auf und ist nachtragbar, eine doppelte Buchung hat beim
letzten Mal 3.000 Eintraege gekostet, bis sie jemand bemerkte.

Ohne frappe: was hier entschieden wird, entscheidet ueber Buchungen, und
eine Regel, die nur gegen eine Instanz zu pruefen ist, prueft niemand.
"""

import datetime

#: Wie weit ein Tag daneben liegen darf. Vier Tage decken 208 der 230
#: gemessenen Doppel; sieben deckten alle, dafuer waere das Fenster breit
#: genug, um zwei echte Mieten derselben Woche zu verschlucken.
TOLERANZ_TAGE = 4


def _tag(wert):
    """Ein Datum als date, oder None."""
    if isinstance(wert, datetime.datetime):
        return wert.date()
    if isinstance(wert, datetime.date):
        return wert
    try:
        return datetime.datetime.strptime(str(wert)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def budget_from(rows):
    """Was das Konto schon hat, gezaehlt.

    :param rows: Paare (Tag, Betrag in Cent)
    :return: {Cent: {Tag: Anzahl}} -- nach Betrag gruppiert, weil der Betrag
        das Sichere ist und der Tag das Wackelige.
    """
    budget = {}
    for tag, cent in rows:
        wann = _tag(tag)
        if wann is None:
            continue
        nach_tag = budget.setdefault(int(cent), {})
        nach_tag[wann] = nach_tag.get(wann, 0) + 1
    return budget


def consume(budget, tag, cent, tolerance=TOLERANZ_TAGE):
    """Eine vorhandene Buchung verbrauchen, wenn es eine gibt.

    Zuerst der Tag selbst -- was am selben Tag steht, ist dieselbe Buchung
    ohne jede Annahme. Erst wenn dort nichts (mehr) frei ist, der naechste
    Tag im Fenster; von zwei gleich weit entfernten der fruehere, damit das
    Ergebnis nicht von der Reihenfolge des Durchlaufs abhaengt.

    :return: "" wenn nichts verbraucht wurde, sonst "tag" oder "fenster" --
        der Aufrufer kann damit sagen, worauf die Erkennung beruht.
    """
    wann = _tag(tag)
    if wann is None:
        return ""
    nach_tag = budget.get(int(cent))
    if not nach_tag:
        return ""

    if nach_tag.get(wann):
        nach_tag[wann] -= 1
        return "tag"

    if tolerance and tolerance > 0:
        passend = [t for t, n in nach_tag.items()
                   if n > 0 and abs((t - wann).days) <= tolerance]
        if passend:
            passend.sort(key=lambda t: (abs((t - wann).days), t))
            nach_tag[passend[0]] -= 1
            return "fenster"
    return ""
