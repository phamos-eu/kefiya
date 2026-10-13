# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Welchen Namen das SEPA-Schema im Auftrag traegt -- den der Bank.

Ohne frappe und ohne Bank, wie fints_response und pain_iban_only: das hier
entscheidet, was in einem Zahlungsauftrag steht.

Ein HKCCS traegt neben der pain.001 einen "SEPA Descriptor": den Namen des
Schemas, nach dem die Datei gebaut ist. Die Bank nennt in HISPAS, welche
Namen sie kennt, und der Auftrag muss einen davon tragen. python-fints
schickt, wenn niemand etwas anderes sagt, immer denselben::

    urn:iso:std:iso:20022:tech:xsd:pain.001.001.03

Die Sparkasse kennt genau diesen. Die Volksbank kennt ihn nicht -- sie
schreibt ihre Liste anders::

    sepade:xsd:pain.001.001.03.xsd
    sepade:xsd:pain.001.001.03_GBIC_2.xsd
    sepade:xsd:pain.001.001.03_GBIC_3.xsd
    sepade:xsd:pain.001.001.09.xsd
    ...

Jeder Auftrag an die Volksbank ging also mit einem Schemanamen hinaus, den
die Bank nicht fuehrt. Sie nahm ihn an, begann die Empfaengerpruefung --
und brach bei der zweiten Nachfrage mit "9000 Weitere Verarbeitung des
Auftrags aufgrund interner Probleme fehlgeschlagen" ab. Sechsmal, mit
Echtzeit und ohne, an drei Empfaenger. Hibiscus, das bei Volksbanken
ueberweist, nimmt den Namen aus der Liste der Bank.
"""

#: Was python-fints ohne Angabe schickt.
DEFAULT_PAIN_001 = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"


def choose(supported, schema="pain.001.001.03", default=DEFAULT_PAIN_001):
    """Der Name, unter dem diese Bank das Schema fuehrt.

    Genau die Eintraege der Bank, die das Schema nennen, kommen in Frage.
    Traegt die Bank die ISO-URN selbst, ist es die. Sonst der hoechste der
    uebrigen -- bei der Volksbank ist das ``..._GBIC_3.xsd``, die juengste
    DK-Auspraegung desselben Schemas, so wie es Hibiscus haelt. Nennt die
    Bank das Schema gar nicht, bleibt es beim Vorgabewert: dann ist die
    Liste das Problem, nicht der Name, und die Bank sagt es selbst.

    :param supported: was die Bank in HISPAS (oder HIIPZS) nennt
    :param schema: das Schema, nach dem die Datei gebaut ist
    :return: der Descriptor fuer den Auftrag
    """
    names = [str(s).strip() for s in (supported or ()) if s]
    fits = [name for name in names if schema in name]
    if not fits:
        return default
    if default in fits:
        return default
    return max(fits)


def formats_in(segment):
    """Die Schemanamen aus einem Parametersegment, wie auch immer python-fints
    es geparst hat.

    HISPAS kennt die Bibliothek und legt die Liste unter
    ``parameter.supported_sepa_formats`` ab -- als ``fints.types.ValueList``,
    was **keine** ``list`` ist. Ein ``isinstance(value, (list, tuple))``
    ging daran vorbei, und die Volksbank bekam ihren eigenen Namen deshalb
    nur bei der Echtzeitueberweisung: deren HIIPZS kennt die Bibliothek
    nicht und laesst die Daten als echte Listen liegen. Also wird hier
    jedes Iterable durchlaufen, das keine Zeichenkette ist.

    Beides wird zu einer flachen Liste von Zeichenketten, die "pain."
    enthalten. Nie eine Ausnahme: ein Segment, das sich nicht lesen laesst,
    nennt keine Formate.
    """
    if segment is None:
        return []
    found = []

    def walk(value, tiefe=0):
        if isinstance(value, str):
            if "pain." in value:
                found.append(value)
            return
        if tiefe > 4 or isinstance(value, (bytes, bytearray)):
            return
        try:
            items = list(value)
        except TypeError:
            return
        for item in items:
            walk(item, tiefe + 1)

    try:
        parameter = getattr(segment, "parameter", None)
        walk(getattr(parameter, "supported_sepa_formats", None))
        walk(getattr(segment, "_additional_data", None))
    except Exception:
        return found
    return found
