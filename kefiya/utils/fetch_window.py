# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ab wann der naechste Abruf fragt -- und warum nicht ab dem letzten Tag.

Der Abruf setzt dort an, wo der letzte aufgehoert hat: beim Datum der
juengsten gebuchten Zeile. Das ist eine Zeile zu spaet, sobald die Bank ein
Datum in der Zukunft liefert.

Gemessen am 30.09.2026, Musterverwaltung GmbH Volksbank
(DE19888888880004444444):

    29.09. 18:42   Abruf von Hand holt zwei Zeilen mit Datum 30.09.
                   (Stadtkasse 91,65; Rundfunk 3,46 -- Valuta voraus)
    29.09. abends  20.000,00 EUR gehen vom Konto 5555555 ein
    30.09. 06:01   Nachtlauf rechnet: juengste Buchung = 30.09.
                   -> er fragt die Bank nur nach dem 30.09.

Die Gutschrift traegt den 29.09. und lag damit VOR dem Fenster. Sie wurde
nie wieder erfragt: am naechsten Morgen ist die juengste Buchung wieder
gleich alt oder juenger. Auf der Gegenseite steht die Belastung sauber da --
dort war das Fenster 29.09. bis 30.09. Das Geld war angekommen, in kefiya
fehlte es.

Die Obergrenze "nie in der Zukunft anfangen" gab es schon. Sie half nicht:
aus dem Datum von morgen wird ueber Nacht das Datum von heute.

Also ein Stueck Ueberschneidung. Der Anfang liegt ein paar Tage VOR der
juengsten Buchung, und was dabei doppelt kommt, erkennt der Import am Inhalt
wieder (booking_fingerprint.of_row, seit dem 29.09.2026) -- ohne diese
Erkennung waere eine Ueberschneidung teuer gewesen, mit ihr kostet sie
nichts. Dieselbe Zahl wie die Toleranz beim Einlesen von Auszuegen: vier
Tage, gross genug fuer ein Wochenende mit Feiertag.

Ohne frappe: was hier entschieden wird, ist Datumsrechnung und soll ohne
Bench zu pruefen sein -- wie fetch_rhythm und booking_budget.
"""

import datetime

#: Wie viele Tage vor der juengsten Buchung der naechste Abruf ansetzt.
#: Vier, wie booking_budget.TOLERANZ_TAGE: ein Wochenende mit Feiertag.
OVERLAP_DAYS = 4


def _als_tag(wert):
    """Was uebergeben wurde, als date -- oder None."""
    if wert is None or wert == "":
        return None
    if isinstance(wert, datetime.datetime):
        return wert.date()
    if isinstance(wert, datetime.date):
        return wert
    text = str(wert).strip()[:10]
    for form in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(text, form).date()
        except ValueError:
            continue
    return None


def start_of_window(last_booked, today, max_days_in_past,
                    overlap=OVERLAP_DAYS):
    """Ab welchem Tag die Bank gefragt wird.

    Drei Grenzen, und die engste gewinnt nach unten:

      * die juengste gebuchte Zeile, minus die Ueberschneidung -- dort hoert
        der letzte Abruf verlaesslich auf, nicht einen Tag spaeter
      * heute: ein Anfang in der Zukunft ergaebe from_date > to_date
      * das Fenster, das der Zugang erlaubt (max_days_in_past) -- weiter
        zurueck gibt die Bank ohnehin nichts heraus

    Ohne Historie wird das ganze erlaubte Fenster gefragt. Im Zweifel also
    mehr statt weniger: eine Zeile doppelt zu holen erkennt der Import am
    Inhalt, eine ungeholte Zeile merkt niemand.
    """
    heute = _als_tag(today) or datetime.date.today()
    try:
        tage = int(max_days_in_past)
    except (TypeError, ValueError):
        tage = 0
    if tage < 0:
        tage = 0
    frueheste = heute - datetime.timedelta(days=tage)

    zuletzt = _als_tag(last_booked)
    if zuletzt is None:
        return frueheste

    try:
        rand = int(overlap)
    except (TypeError, ValueError):
        rand = 0
    if rand < 0:
        rand = 0

    anfang = min(zuletzt, heute) - datetime.timedelta(days=rand)
    if anfang > frueheste:
        return anfang
    return frueheste
