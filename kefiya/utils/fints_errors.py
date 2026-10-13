# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Die zwei Ausnahmen der FinTS-Strecke, an einer Stelle, die beide sehen.

Sie standen in fints_controller.py. Seit die TAN-Strecke in
fints_tan_session.py wohnt, brauchen beide Module sie -- und der Controller
importiert die TAN-Strecke, also kann die TAN-Strecke nicht den Controller
importieren. Ein Modul ohne eigene Importe loest das, und zwar so, dass es
auch beim naechsten Herausziehen noch traegt.

Der Controller reicht beide Namen weiter, damit
``from kefiya.utils.fints_controller import TanInteractionRequired``
weiter gilt -- client.py tut das an drei Stellen.
"""


class InitFailedException(Exception):
    """Der Zugang kam nicht zustande, oder ein Auftrag kam nicht durch."""


class TanInteractionRequired(InitFailedException):
    """Es liegt an einer TAN: jemand muss etwas freigeben.

    Absichtlich eine Unterart von InitFailedException: Aufrufer, die nur
    wissen wollen, dass es nicht geklappt hat, fangen weiter das Oberste;
    wer die Freigabe abwarten will, fangt dieses hier zuerst.
    """
