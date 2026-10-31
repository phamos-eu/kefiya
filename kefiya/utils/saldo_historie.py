# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Zwei datierte Banksalden, damit sich ueberhaupt etwas vergleichen laesst.

``store_balance()`` schreibt den Saldo, den die Bank im HISAL-Segment nennt,
auf das Bankkonto. Das ist eine echte Aussage der Bank. Nur: sie wird bei
jedem Abruf ueberschrieben, und das mitgelieferte ``balance_date`` wird gar
nicht erst gespeichert. Damit gibt es genau einen Wert ohne Datum -- und mit
einem Wert laesst sich nichts vergleichen.

WOZU. Der Frischewaechter schaetzt heute aus dem Buchungsrhythmus, ob ein
Konto zu lange still ist, und hat damit dreimal falsch gemeldet. Die
Bank koennte die Frage beantworten statt sie zu schaetzen::

    Saldo_neu - Saldo_alt  ==  Summe der Umsaetze dazwischen?

Stimmt es, fehlt nichts -- egal wie alt der juengste Umsatz ist. Stimmt es
nicht, ist die Meldung berechtigt UND nennt den Betrag. Das ist die
Hausregel: die Bank ist der Schiedsrichter, nicht das System.

WAS NICHT ALS ZEUGE TAUGT, und das ist hier der entscheidende Punkt: der
``bank_balance`` an jeder Buchung. Den errechnet ``apply_running_balance()``
rueckwaerts aus genau diesem HKSAL-Saldo, indem es die Buchungen der Reihe
nach abzieht. Ein Abgleich dagegen geht per Konstruktion immer auf. Genau
darauf bin ich am 30.09.2026 hereingefallen und habe eine lueckenlose
Saldokette als Beweis ausgegeben, dass nichts fehlt. Sie war zirkulaer.

DIE FALLE BEIM FORTSCHREIBEN. Naheliegend waere, bei jedem Abruf den
bisherigen Saldo auf "vorher" zu schieben und den neuen als "jetzt" zu
setzen. Das zerstoert das Intervall: die Konten werden mehrmals am Tag
abgerufen -- der Zeitplan nachts, der Sammelabruf, ein Abruf von Hand --
und nach dem zweiten Abruf eines Tages waeren "vorher" und "jetzt"
derselbe Stand. Die Differenz waere 0,00 EUR, und zwar fuer immer und ohne
dass es auffaellt.

Geschoben wird deshalb nur, wenn das Datum der Bank weiterrueckt. Mehrere
Abrufe an einem Tag aktualisieren den Stand von heute; "vorher" bleibt der
letzte Stand eines frueheren Tages.

Ohne frappe, aus demselben Grund wie fetch_outcome und verwendungszweck.
"""


def _als_tag(wert):
    """Ein Datum als YYYY-MM-DD, egal in welcher Gestalt es ankam.

    Die Bank liefert ein date, die Datenbank eine Zeichenkette, und python
    -fints gelegentlich ein datetime. Verglichen wird als Text, weil die
    Ordnung auf YYYY-MM-DD dieselbe ist wie auf dem Datum.
    """
    if wert is None:
        return ""
    for name in ("date", "isoformat"):
        leser = getattr(wert, name, None)
        if leser is None:
            continue
        try:
            ergebnis = leser()
        except TypeError:
            continue
        except Exception:
            break
        if name == "date":
            wert = ergebnis
            continue
        return str(ergebnis)[:10]
    return str(wert).strip()[:10]


def naechster_stand(gespeichert, saldo, stand_am):
    """Was nach diesem Abruf in der Historie stehen soll.

    :param gespeichert: was am Konto steht, als dict mit den Schluesseln
        ``balance``, ``as_of``, ``previous``, ``previous_as_of``
    :param saldo: der Saldo, den die Bank gerade genannt hat
    :param stand_am: das Datum, auf das die Bank ihn bezieht
        (``balance_date`` aus dem HISAL-Segment)
    :return: dict der zu schreibenden Felder; leer, wenn nichts zu tun ist
    """
    neu = _als_tag(stand_am)
    if not neu or saldo is None:
        # Ein Saldo ohne Datum laesst sich nicht in eine Reihe stellen. Er
        # wird weiterhin als aktueller Saldo geschrieben -- das tut
        # store_balance() wie bisher --, aber er wird keine Geschichte.
        return {}

    gespeichert = gespeichert or {}
    alt = _als_tag(gespeichert.get("as_of"))

    if not alt:
        # Der erste datierte Stand. Es gibt noch kein Intervall, nur einen
        # Anfang.
        return {"as_of": neu, "balance_at": saldo}

    if neu < alt:
        # Eine Antwort, die hinter den bekannten Stand zurueckfaellt. Sie
        # darf die Reihe nicht ruecwaerts drehen; der Abruf selbst bleibt
        # davon unberuehrt.
        return {}

    if neu == alt:
        # Derselbe Tag, erneut abgerufen. Der Stand von heute wird
        # nachgefuehrt, das Intervall bleibt stehen -- sonst waere "vorher"
        # nach dem zweiten Abruf eines Tages dasselbe wie "jetzt".
        return {"as_of": neu, "balance_at": saldo}

    return {
        "as_of": neu,
        "balance_at": saldo,
        "previous_as_of": alt,
        "previous": gespeichert.get("balance_at"),
    }


def differenz(gespeichert):
    """Wie viel sich zwischen den beiden Staenden bewegt hat.

    Das ist die Zahl, gegen die die Summe der importierten Umsaetze stehen
    muss -- gezaehlt nach ``entry_date``, dem Buchungstag, nicht nach Valuta.

    :return: float oder None, solange es kein Intervall gibt
    """
    gespeichert = gespeichert or {}
    jetzt = gespeichert.get("balance_at")
    vorher = gespeichert.get("previous")
    if jetzt is None or vorher is None:
        return None
    if not _als_tag(gespeichert.get("previous_as_of")):
        return None
    return float(jetzt) - float(vorher)


def fenster(gespeichert):
    """Der Zeitraum, den die Differenz abdeckt.

    :return: (von, bis) als YYYY-MM-DD, oder (None, None)
    """
    gespeichert = gespeichert or {}
    von = _als_tag(gespeichert.get("previous_as_of"))
    bis = _als_tag(gespeichert.get("as_of"))
    if not von or not bis:
        return (None, None)
    return (von, bis)
