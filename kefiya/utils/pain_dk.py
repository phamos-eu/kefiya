# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Was die deutschen Banken an einer pain.001 anders wollen als die ISO.

Ohne frappe, aus demselben Grund wie fints_response und duplicate_rule: das
hier entscheidet ueber den Inhalt einer Zahlungsdatei, und eine Regel, die
nur gegen eine Bank zu pruefen ist, prueft niemand.

Zwei Stellen bisher, und beide haben eine Ueberweisung gekostet.

**Das Institut des Zahlers.** Kein Bankkonto hier traegt eine BIC, und
sepaxml 2.7.0 schreibt dann::

    <DbtrAgt><FinInstnId /></DbtrAgt>

Das besteht die ISO-Schemapruefung -- im ISO-Schema ist in FinInstnId alles
optional -- und ist nicht das, was die Deutsche Kreditwirtschaft
vorschreibt. Deren pain.001.001.03 verlangt eine BIC oder, bei IBAN-only::

    <DbtrAgt><FinInstnId><Othr><Id>NOTPROVIDED</Id></Othr></FinInstnId></DbtrAgt>

**Das Ausfuehrungsdatum.** Eine Ueberweisung, die sofort ausgefuehrt werden
soll, traegt kein Datum -- und "kein Datum" schreibt die DK als den festen
Wert 1999-01-01. Wir schickten das heutige. Die Volksbank sagt es woertlich::

    0020 Ausfuehrungsbestaetigung nach Namensabgleich erhalten.
    9150 Ausfuehrungsdatum darf nicht belegt werden. Wert ist ungleich
         1999-01-01.

Die Sparkasse sagt zu demselben Auftrag nur "9010 Der Auftrag wurde nicht
ausgefuehrt" -- nach der Freigabe in der App, viermal an drei Tagen. Nur
ein terminierter Auftrag, den die BANK bis zu seinem Tag haelt (HKCSE),
traegt sein Datum wirklich.
"""

import datetime
import re

NOT_PROVIDED = "NOTPROVIDED"

#: "Kein Ausfuehrungsdatum", wie die Deutsche Kreditwirtschaft es schreibt.
#: Kein Platzhalter unsererseits: die Bank prueft genau auf diesen Wert.
IMMEDIATE_EXECUTION = datetime.date(1999, 1, 1)

#: Das leere Institut des Zahlers, wie sepaxml es ohne BIC schreibt -- in
#: beiden Schreibweisen, die ein Serializer dafuer hat.
_LEER = re.compile(r"<DbtrAgt>\s*(?:<FinInstnId\s*/>|<FinInstnId>\s*</FinInstnId>)\s*</DbtrAgt>")

_IBAN_ONLY = ("<DbtrAgt><FinInstnId><Othr><Id>" + NOT_PROVIDED
              + "</Id></Othr></FinInstnId></DbtrAgt>")


def debtor_agent_not_provided(xml):
    """Ein leeres Institut des Zahlers wird zu NOTPROVIDED.

    Nur das leere. Eine BIC bleibt, was sie ist, und eine Datei, in der das
    Element gar nicht vorkommt, bleibt unangetastet -- diese Funktion sagt
    nichts ueber die Datei, sie behebt genau eine Luecke.

    :param xml: die pain.001 als Text
    :return: die pain.001 als Text
    """
    if not xml:
        return xml
    return _LEER.sub(_IBAN_ONLY, xml)


def debtor_agent_is_empty(xml):
    """Sagt die Datei ueber das Institut des Zahlers nichts?"""
    return bool(xml) and _LEER.search(xml) is not None


def execution_date(wanted, bank_holds_it):
    """Das Datum, das in die Nachricht gehoert.

    Nur ein Auftrag, den die Bank bis zu seinem Tag haelt, traegt sein
    Datum. Alles andere ist eine sofortige Ueberweisung, und die traegt
    IMMEDIATE_EXECUTION -- nicht heute, nicht gestern, nicht das Datum,
    unter dem der Auftrag hier gefuehrt wird.

    :param wanted: der Tag, den der Auftrag meint
    :param bank_holds_it: haelt die BANK ihn bis dahin?
    """
    return wanted if bank_holds_it else IMMEDIATE_EXECUTION
