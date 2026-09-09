# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Was die Bibliothek zurueckgibt, wenn eine Bank camt statt MT940 liefert.

Ohne frappe und ohne fints, aus demselben Grund wie fints_response und
pain_dk: hier wird entschieden, ob ein Abruf Buchungen gebracht hat oder
rohe Dokumente, und das muss ohne Bank und ohne Bench zu pruefen sein.

python-fints holt Umsaetze auf zwei Wegen. MT940, wo die Bank es anbietet,
und sonst camt -- ein oder mehrere XML-Dokumente. Aus diesen Dokumenten
macht ``get_transactions()`` Transaction-Objekte, und zwar in der
oeffentlichen Methode. Wer eine Ebene tiefer einsteigt, weil er die
TAN-Anforderung behalten will, bekommt das Rohe:

    ([b"<?xml ... Document ...>"], [])
     ^ gebucht                     ^ vorgemerkt

Und genau dorthin kehrt auch die Wiederaufnahme nach einer Freigabe
zurueck. Bei einer Bank, die vor jedem Abruf eine Freigabe in ihrer App
verlangt -- die Volksbank tut das --, ist das der normale Weg. kefiya gab
das Ergebnis unveraendert an json.dumps weiter::

    TypeError: Object of type bytes is not JSON serializable

Drei Volksbank-Konten liessen sich damit ueberhaupt nicht mehr abrufen,
und die Meldung sagte "Die Umsaetze liessen sich nicht lesen".

Hier steht nur die Frage "ist das ein Paar von Dokumentlisten?" -- das
Auspacken selbst bleibt beim Aufrufer, der die Bibliothek dafuer hat.
"""

#: Was als Dokument durchgeht. Die Bibliothek liefert bytes; str kommt aus
#: Tests und aus Banken, die schon dekodiert haben.
_DOKUMENT = (bytes, bytearray, str)


def streams_in(result, include_pending=False):
    """Die camt-Dokumente in dem, was die Bibliothek zurueckgab.

    :param result: das Ergebnis eines Abrufs -- ein Paar von Listen, eine
        Liste von Buchungen, oder was eine spaetere Bibliotheksfassung
        daraus macht
    :param include_pending: auch die vorgemerkten Umsaetze
    :return: die Dokumente in ihrer Reihenfolge, oder **None**, wenn das
        Ergebnis kein Paar von Dokumentlisten ist -- dann sind es
        Buchungen, und der Aufrufer laesst sie, wie sie sind.
    """
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        return None

    gebucht, vorgemerkt = result
    for teil in (gebucht, vorgemerkt):
        if teil is None:
            continue
        if not isinstance(teil, (tuple, list)):
            return None
        for eintrag in teil:
            # None kommt vor: python-fints haengt statement_pending auch
            # dann an, wenn die Bank keinen vorgemerkten Block sendet.
            if eintrag is not None and not isinstance(eintrag, _DOKUMENT):
                return None

    dokumente = [x for x in (gebucht or []) if x]
    if include_pending:
        dokumente += [x for x in (vorgemerkt or []) if x]
    return dokumente
