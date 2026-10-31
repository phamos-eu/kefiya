# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Text, den die Bank in einer pain.001 tragen kann -- schon bei der Erfassung.

WOZU. Am 08. und 09.10.2026 lehnte die Sparkasse denselben
Auftrag dreimal ab, jedes Mal erst nach der Freigabe in der Banking-App::

    9050 Die Nachricht enthaelt Fehler.
    9010 Der Auftrag wurde nicht ausgefuehrt.

``9050`` beanstandet die NACHRICHT, nicht die Auftragsdaten -- das waere
9210. Die Nachricht war gueltiges ISO-XML; ``sepa.export(validate=True)``
hatte sie gegen das Schema geprueft und durchgelassen. ``Max140Text`` laesst
jedes Unicode-Zeichen zu, die Bank nicht.

WAS WIRKLICH ERLAUBT IST. Die erste Fassung dieser Regel nahm den
Zeichensatz der Spezifikation und schrieb Umlaute zu "ae oe ue" um. Das war
falsch -- nicht gefaehrlich, aber unnoetig, und es verunstaltete jeden
zweiten Verwendungszweck. Entschieden hat es die Bank, nicht die
Spezifikation: 60.000 Buchungen dieser Instanz, Zeichen fuer Zeichen
gezaehlt, und zwar getrennt nach Richtung. Was in einem EINGEHENDEN
Verwendungszweck steht, hat die Bank eines Fremden durch das SEPA-Netz
geschickt und unsere Bank zugestellt -- ein Zeuge, den niemand
herbeigeredet hat::

    Zeichen   Eingang   Ausgang   Gegenseitenname
    ---------------------------------------------
    Ue          1728      4011         53
    ss          1261      1112       2784
    ue          1446      1942       1044
    ae           580       388        986
    oe           175       117        445
    Ae           114       477         50
    Oe            25        13         55
    &             23        26       1136
    %            169      1030          0
    *              1        13          0
    =              0        12          0
    _              0         2          0
    >              0         2          0

Und das Eurozeichen: **null** von 60.000. Steuerzeichen ebenfalls null. Der
eine Auftrag, der ein Eurozeichen trug, ist der eine, den die Bank abgelehnt
hat.

DIE REGEL, die daraus folgt. Erlaubt ist der SEPA-Grundzeichensatz

    a-z A-Z 0-9 und  / - ? : ( ) . , ' +  und das Leerzeichen

und dazu, was die Messung in BEIDEN Richtungen tausendfach zeigt: die
deutschen Umlaute, das scharfe s, das Kaufmanns-Und und das Prozentzeichen.
Die bleiben stehen, wie sie sind.

WARUM * = _ > TROTZDEM NICHT. Sie sind aufgetaucht, aber nur in unseren
eigenen abgehenden Texten und zwolf-, zwei-, zweimal. Hier sind die beiden
Fehler nicht gleich teuer: ein Zeichen zu Unrecht durchzulassen kostet einen
abgelehnten Auftrag, nachdem eine TAN darauf verbraucht ist -- genau der
Schmerz, um den es hier geht. Ein Zeichen zu Unrecht zu ersetzen kostet
einen leicht veraenderten Verwendungszweck. Wo der Zeuge duenn ist, wird der
billigere Fehler gemacht.

KORRIGIERT, NICHT ABGELEHNT, und zwar bei der Erfassung -- waehrend der
Auftrag ein Entwurf ist und der Text vor den Augen dessen steht, der ihn
freigibt. Nicht beim Senden: dort laege die Aenderung zwischen Freigabe und
Bank. Was geaendert wurde, meldet validate beim Speichern; automatisch
heisst nicht stillschweigend.

Ohne frappe, aus demselben Grund wie fints_response: die Regel entscheidet
mit, ob eine Zahlung herausgeht, und eine Regel, die nur gegen eine echte
Bank laeuft, laeuft nie.
"""

#: Der SEPA-Grundzeichensatz.
_GRUND = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "/-?:().,'+ "
)

#: Was die Messung dazu erlaubt: beide Richtungen, tausendfach.
#: Umlaute und scharfes s 21.000-mal, das Und 2.400-mal (davon 1.136 in
#: Namen von Gegenseiten), das Prozentzeichen 1.200-mal.
_GEMESSEN = "\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df&%"

ERLAUBT = frozenset(_GRUND + _GEMESSEN)

#: Zeichen, fuer die es einen Namen gibt, den die Bank tragen kann. Laenger
#: als ein Zeichen sein zu duerfen ist der Punkt: "EUR" ist die Antwort auf
#: das Eurozeichen, nicht "E".
#:
#: Keines davon kommt in 60.000 Buchungen vor -- es gibt also keinen Zeugen
#: dafuer, dass das Netz sie traegt. Die Buchstaben mit Akzent bekommen den
#: Buchstaben ohne; das ist naeher am Gemeinten als ein Leerzeichen.
ERSATZ = {
    "\u20ac": "EUR",   # null von 60.000, und der eine abgelehnte Auftrag
    "\u00a3": "GBP",
    "$": "USD",
    "\u00a7": "Par.",
    "\u00b0": "Grad",
    "\u00e1": "a", "\u00e0": "a", "\u00e2": "a", "\u00e5": "a", "\u00e3": "a",
    "\u00e9": "e", "\u00e8": "e", "\u00ea": "e", "\u00eb": "e",
    "\u00ed": "i", "\u00ec": "i", "\u00ee": "i", "\u00ef": "i",
    "\u00f3": "o", "\u00f2": "o", "\u00f4": "o", "\u00f5": "o", "\u00f8": "o",
    "\u00fa": "u", "\u00f9": "u", "\u00fb": "u",
    "\u00fd": "y", "\u00ff": "y",
    "\u00e7": "c", "\u00f1": "n",
    "\u00c1": "A", "\u00c0": "A", "\u00c2": "A", "\u00c5": "A", "\u00c3": "A",
    "\u00c9": "E", "\u00c8": "E", "\u00ca": "E", "\u00cb": "E",
    "\u00cd": "I", "\u00cc": "I", "\u00ce": "I", "\u00cf": "I",
    "\u00d3": "O", "\u00d2": "O", "\u00d4": "O", "\u00d5": "O", "\u00d8": "O",
    "\u00da": "U", "\u00d9": "U", "\u00db": "U",
    "\u00c7": "C", "\u00d1": "N",
    "\u00e6": "ae", "\u00c6": "Ae",
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u00b4": "'", "`": "'",
    "\u201c": "'", "\u201d": "'", "\u201e": "'", '"': "'",
    "\u00ab": "'", "\u00bb": "'",
    "\u2026": "...",
    "\u2022": "-",
    "\u00bd": "1/2", "\u00bc": "1/4", "\u00be": "3/4",
    "\u00b2": "2", "\u00b3": "3",
    "\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u200b": " ",
}

#: Wie ein Steuerzeichen heisst, wenn eine Meldung es nennen muss. Es
#: unsichtbar in Anfuehrungszeichen zu zeigen hilft niemandem.
STEUERZEICHEN = {
    "\n": "Zeilenumbruch",
    "\r": "Zeilenumbruch",
    "\t": "Tabulator",
}


def _ersetzung(zeichen):
    """Was aus einem Zeichen wird, das nicht erlaubt ist."""
    if zeichen in ERSATZ:
        return ERSATZ[zeichen]
    # Notfalls ein Leerzeichen -- nichts wird einfach weggelassen, denn ein
    # fehlendes Zeichen faellt niemandem auf, eine Luecke schon.
    return " "


def clean(text):
    """Derselbe Text, wie die Bank ihn tragen kann.

    Zweimal angewandt kommt dasselbe heraus wie einmal: der Auftrag wird bei
    jedem Speichern geprueft, und ein Text, der sich dabei jedes Mal weiter
    veraendert, waere nach dem dritten Speichern nicht mehr der, den jemand
    freigegeben hat.
    """
    raus = []
    for zeichen in str(text or ""):
        if zeichen in ERLAUBT:
            raus.append(zeichen)
        else:
            raus.append(_ersetzung(zeichen))
    # Aus einem ersetzten Zeichen soll keine Luecke bleiben, und ein
    # Zeilenumbruch hinterlaesst sonst zwei Leerzeichen.
    return " ".join("".join(raus).split())


def changed(text):
    """Hat clean() etwas zu tun?"""
    text = str(text or "")
    return clean(text) != text


def unsendable(text):
    """Die Zeichen in ``text``, die die Bank nicht tragen kann.

    :return: Liste von dicts ``{"char", "name", "replacement"}``, in der
        Reihenfolge ihres ersten Auftretens und ohne Wiederholung.
    """
    gefunden = []
    gesehen = set()
    for zeichen in str(text or ""):
        if zeichen in ERLAUBT or zeichen in gesehen:
            continue
        gesehen.add(zeichen)
        gefunden.append({
            "char": zeichen,
            "name": STEUERZEICHEN.get(zeichen,
                                      "U+{0:04X}".format(ord(zeichen))),
            "replacement": _ersetzung(zeichen),
        })
    return gefunden


def complaint(text):
    """Was geaendert wurde, in einem Satz; leer, wenn nichts.

    Nennt die Zeichen beim Namen und das, was an ihre Stelle tritt. Ein
    Nutzer, der "9050 Die Nachricht enthaelt Fehler" liest, erfaehrt daraus
    nichts; "das Eurozeichen wurde EUR" ist eine Auskunft.
    """
    teile = []
    for eintrag in unsendable(text):
        sichtbar = (eintrag["char"] if eintrag["char"].strip()
                    else eintrag["name"])
        ersatz = eintrag["replacement"]
        teile.append("{0} -> {1}".format(
            sichtbar, ersatz if ersatz.strip() else "Leerzeichen"))
    return ", ".join(teile)
