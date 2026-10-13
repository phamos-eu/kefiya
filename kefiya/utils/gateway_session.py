# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Denselben Gateway wiederfinden, wenn ein Dialog spaeter fortgesetzt wird.

Die Volksbank (Atruvia) betreibt hinter
``https://fints2.atruvia.de/cgi-bin/hbciservlet`` mehrere Gateways. Welchen
man erwischt, entscheidet sich beim Verbindungsaufbau; danach bleibt man
dort, solange dieselbe HTTPS-Sitzung benutzt wird -- python-fints haelt
dafuer eine ``requests.session()`` je Verbindung, und ihre Cookies sind es,
die einen bei demselben Gateway halten.

Ein geparkter TAN-Dialog wird aber in einer **spaeteren Anfrage**
fortgesetzt, und die baut eine neue Verbindung mit einem leeren
Cookie-Glas. Landet sie beim anderen Gateway, kennt der den Dialog nicht::

    9800  FGW Gatewaywechsel A/B in Dialog/Nachricht BR6091007011049/3

kefiya meldet daraufhin "No TAN status received", verwirft die geparkte
Anfrage -- und die Freigabe, die der Nutzer gerade in seiner Banking-App
erteilt hat, ist verloren. Er gibt sie erneut, und wieder, und wieder. Alle
zehn Faelle dieses Fehlers im Fehlerprotokoll stehen auf genau diesem Weg;
innerhalb eines laufenden Dialogs kommt er nie vor -- da ist es dieselbe
Sitzung.

Also wird das Cookie-Glas mit dem pausierten Dialog aufgehoben und beim
Fortsetzen zurueckgelegt. Findet die Bank ihre Cookies nicht wieder oder
merkt sie sich den Gateway anders, aendert das nichts -- dann ist es genau
das, was heute geschieht.

Ohne frappe und ohne fints, wie fints_response und camt_shape: was hier
entschieden wird, muss ohne Bank und ohne Bench zu pruefen sein.
"""

import json


def jar_of(client):
    """Das Cookie-Glas der HTTPS-Verbindung, oder None.

    Greift durch drei fremde Objekte -- Client, Verbindung, Sitzung -- und
    darf dabei nie werfen: eine Bibliotheksfassung, die anders gebaut ist,
    kostet die Klebrigkeit, nicht den Abruf.
    """
    session = getattr(getattr(client, "connection", None), "session", None)
    return getattr(session, "cookies", None)


def cookies_of(client):
    """Die Cookies dieser Verbindung als {Name: Wert}.

    Leer, wenn es keine gibt -- und leer heisst hier "nichts zu merken",
    nicht "keine Cookies senden".
    """
    jar = jar_of(client)
    if jar is None:
        return {}
    try:
        # requests' RequestsCookieJar kann beides; ein einfaches dict in
        # einem Test nur das zweite.
        holen = getattr(jar, "get_dict", None)
        werte = holen() if callable(holen) else dict(jar)
    except Exception:
        return {}
    return {str(k): str(v) for k, v in (werte or {}).items() if k}


def restore(client, cookies):
    """Die gemerkten Cookies auf eine frische Verbindung legen.

    :return: True, wenn etwas gelegt wurde
    """
    if not cookies:
        return False
    jar = jar_of(client)
    if jar is None:
        return False
    setzen = getattr(jar, "set", None)
    try:
        for name, wert in cookies.items():
            if callable(setzen):
                setzen(name, wert)
            else:
                jar[name] = wert
    except Exception:
        return False
    return True


def to_blob(cookies):
    """Zum Wegschreiben: JSON in bytes, wie die anderen Zustaende auch.

    Nichts zu merken ergibt None -- dann loescht der Aufrufer das Feld,
    statt ein leeres Glas aufzuheben, das beim Fortsetzen nichts taete.
    """
    if not cookies:
        return None
    return json.dumps(cookies, sort_keys=True).encode("utf-8")


def from_blob(blob):
    """Zurueck aus dem, was to_blob geschrieben hat.

    Unlesbares ergibt {} -- ein kaputtes Glas darf den Wiederaufnahme-Weg
    nicht anhalten, es macht ihn nur wieder so unzuverlaessig wie vorher.
    """
    if not blob:
        return {}
    try:
        if isinstance(blob, (bytes, bytearray)):
            blob = blob.decode("utf-8")
        werte = json.loads(blob)
    except (ValueError, TypeError, UnicodeDecodeError):
        return {}
    if not isinstance(werte, dict):
        return {}
    return {str(k): str(v) for k, v in werte.items() if k}
