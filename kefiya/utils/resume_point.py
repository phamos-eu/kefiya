# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Kann sich python-fints merken, wo dieser Auftrag weitermacht?

Ein Auftrag, den die Bank zurueckhaelt -- fuer eine TAN oder fuer die
Bestaetigung des Empfaengers -- wird geparkt und in einer spaeteren Anfrage
fortgesetzt. Dafuer schreibt die Bibliothek auf, WO er weitermacht. Sie kann
das nur als Namen::

    class NeedVOPResponse(NeedRetryResponse):
        def __init__(self, ..., resume_method=None):
            if hasattr(resume_method, '__func__'):
                self.resume_method = resume_method.__func__.__name__
            else:
                self.resume_method = resume_method

und beim Fortsetzen::

    resume_func = getattr(self, challenge.resume_method)

Eine gebundene Methode wird also zu ihrem Namen. Eine Closure hat kein
``__func__`` -- sie bleibt als Funktionsobjekt stehen, und beim Aufschreiben
zerbricht es::

    File "kefiya/utils/fints_controller.py", line 1850, in _persist_vop_state
      self.kefiya_login.stored_vop_blob = response.get_data()
    File "fints/utils.py", line 40, in compress_datablob
      serialized = json.dumps(data).encode('utf-8')
    TypeError: Object of type function is not JSON serializable

Gemessen am 29.09.2026, 19:19 Uhr, Auftrag C ueber 20.000,00 EUR an
die Musterverwaltung GmbH: der erste Terminauftrag, den die Bank halten
sollte. Die Volksbank hatte die pain-Nachricht da schon bekommen und nach der
Empfaengerbestaetigung gefragt. Der Auftrag war nicht freigegeben -- kein TAN,
kein HKVPA -- aber die Ausnahme nahm die ganze Transaktion mit, und damit die
geparkte Anforderung: es gab nichts mehr, was eine Freigabe haette
freischalten koennen.

Und selbst wenn es sich hatte aufschreiben lassen: ``getattr(self, <Closure>)``
findet nichts. Eine Closure kann diese Runde durch die Datenbank nicht
ueberleben, und zwar nie.

Deshalb wird hier vor dem Senden gefragt statt hinterher. Ohne frappe: was
hier entschieden wird, ist eine Eigenschaft eines Python-Objekts und soll
ohne Bench zu pruefen sein.
"""


def can_be_written_down(resume):
    """Laesst sich dieser Wiederaufnahme-Punkt parken und wiederfinden?

    Wahr genau fuer eine gebundene Methode: sie hat ein ``__func__``, aus dem
    die Bibliothek den Namen nimmt, und ein ``__self__``, das belegt, dass
    der Name spaeter am Client auch gefunden wird.

    Ein Name als Zeichenkette gilt ebenfalls -- so steht er nach dem
    Wiedereinlesen im geparkten Zustand, und ein zweites Parken darf daran
    nicht scheitern.

    Alles andere -- eine Closure, eine freie Funktion, ein partial, None --
    ist falsch. Im Zweifel nein: ein Auftrag, der sich nicht parken laesst,
    darf gar nicht erst zur Bank.
    """
    if isinstance(resume, str):
        return bool(resume)
    return hasattr(resume, "__func__") and hasattr(resume, "__self__")
