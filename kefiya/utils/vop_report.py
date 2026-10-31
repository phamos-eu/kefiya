# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der Payment Status Report der Bank, gelesen.

Ohne frappe und ohne Bank, wie vop_rule und fints_response: hier steht,
was einem Menschen ueber einen Empfaenger gezeigt wird, bevor er Geld
freigibt, und das muss ohne Instanz zu pruefen sein.

Zwei Formen, in denen eine Bank ihr Ergebnis der Empfaengerpruefung
zurueckgibt. Die eine kennt python-fints: ein EVPE im HIVPP-Segment, ein
Code und der Name, den die Bank haelt -- vop_rule liest sie. Die andere
steht in den Bankparametern der Volksbank::

    ParameterVoP(report_complete='V',
                 supported_report_formats='...pain.002.001.10')

'V' heisst: die Bank antwortet mit dem vollstaendigen Bericht, einem
pain.002-Dokument in HIVPP.payment_status_report -- und dann ist das EVPE
leer. In der Bibliothek kommt payment_status_report kein einziges Mal vor.
Der Dialog sagte deshalb ehrlich, aber nutzlos: "Diese Bank liefert ihr
Pruefergebnis in einem Format, das diese App nicht lesen kann." Ein
Mensch, der freigeben soll, hatte den Namen der Bank nie vor sich.

Was hier gelesen wird, ist genau das, was ein Mensch braucht:

    status      TxSts -- ACCP, RJCT, PDNG ...
    result      der VoP-Code aus StsRsnInf/Rsn (Cd oder Prtry)
    bank_name   der Name, den die BANK zur IBAN haelt
    detail      was die Bank sonst dazu sagt (AddtlInf)

Ohne Namensraeume gelesen: eine pain.002 traegt je nach Auspraegung
verschiedene, und ein Leser, der auf einen davon besteht, findet bei der
naechsten Bank nichts. Nie eine Ausnahme -- was sich nicht lesen laesst,
ist nichts, und der Dialog sagt dann weiter, dass er nichts lesen konnte.
"""

import xml.etree.ElementTree as ET

#: Wo der Name steht, den die Bank haelt -- in der Reihenfolge, in der ihm
#: zu trauen ist. Der Name unter StsRsnInf gehoert zur Begruendung; der
#: unter OrgnlTxRef ist der, gegen den die Bank geprueft hat.
_NAME_PATHS = (
    ("StsRsnInf", "AddtlInf"),
    ("SplmtryData", "Envlp", "Nm"),
    ("OrgnlTxRef", "Cdtr", "Nm"),
)


def _tag(element):
    """Der Name eines Elements ohne seinen Namensraum."""
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _kinder(element, name):
    return [kind for kind in element if _tag(kind) == name]


def _erstes(element, pfad):
    """Dem Pfad folgen, ohne Namensraeume. None, wenn er ins Leere geht."""
    hier = element
    for name in pfad:
        treffer = _kinder(hier, name)
        if not treffer:
            return None
        hier = treffer[0]
    return hier


def _text(element):
    return (element.text or "").strip() if element is not None else ""


def _transaktionen(wurzel):
    """Alle TxInfAndSts des Berichts, wie tief sie auch liegen."""
    return [el for el in wurzel.iter() if _tag(el) == "TxInfAndSts"]


def read(report, end_to_end_id=None):
    """Was die Bank ueber diesen Empfaenger sagt.

    :param report: das pain.002-Dokument, als bytes oder str
    :param end_to_end_id: die Zahlung, um die es geht -- bei einer
        Sammelueberweisung stehen mehrere im Bericht. Ohne Angabe zaehlt die
        einzige; sind es mehrere, wird nichts geraten.
    :return: {"status", "result", "bank_name", "detail"} -- leere
        Zeichenketten, wo der Bericht nichts hergibt
    """
    leer = {"status": "", "result": "", "bank_name": "", "detail": ""}
    if not report:
        return leer
    try:
        if isinstance(report, (bytes, bytearray)):
            report = bytes(report)
        wurzel = ET.fromstring(report)
    except Exception:
        return leer

    try:
        gefunden = _transaktionen(wurzel)
    except Exception:
        return leer
    if not gefunden:
        return leer

    if end_to_end_id:
        passend = [tx for tx in gefunden
                   if _text(_erstes(tx, ("OrgnlEndToEndId",))) == end_to_end_id]
        gefunden = passend or gefunden
    if len(gefunden) != 1:
        # Mehrere Zahlungen und keine benannt: eine davon zu zeigen waere
        # geraten, und geraten wird hier nicht.
        return leer

    tx = gefunden[0]
    return {
        "status": _text(_erstes(tx, ("TxSts",))),
        "result": _grund(tx),
        "bank_name": _name(tx),
        "detail": _zusatz(tx),
    }


def _grund(tx):
    """Der Code der Begruendung: Cd oder, wo die Bank ihn selbst vergibt,
    Prtry."""
    for pfad in (("StsRsnInf", "Rsn", "Cd"), ("StsRsnInf", "Rsn", "Prtry")):
        wert = _text(_erstes(tx, pfad))
        if wert:
            return wert
    return ""


def _name(tx):
    """Der Name, den die Bank zur IBAN haelt.

    Nur, wo er wie ein Name aussieht: AddtlInf traegt bei manchen Instituten
    den Namen und bei anderen einen Satz ueber die Pruefung. Ein Code oder
    eine Zahl ist kein Name und wird nicht als einer gezeigt.
    """
    for pfad in _NAME_PATHS:
        wert = _text(_erstes(tx, pfad))
        if wert and _sieht_aus_wie_ein_name(wert):
            return wert
    return ""


def _sieht_aus_wie_ein_name(wert):
    if len(wert) < 3 or len(wert) > 140:
        return False
    return any(zeichen.isalpha() for zeichen in wert) and " " in wert.strip()


def _zusatz(tx):
    """Was die Bank sonst zu dieser Zahlung sagt, ohne den Namen doppelt."""
    name = _name(tx)
    saetze = []
    for el in tx.iter():
        if _tag(el) != "AddtlInf":
            continue
        wert = _text(el)
        if wert and wert != name and wert not in saetze:
            saetze.append(wert)
    return " ".join(saetze)[:500]
