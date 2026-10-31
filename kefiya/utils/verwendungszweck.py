# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Wo zwischen zwei Teilfeldern des Verwendungszwecks ein Leerzeichen hingehoert.

Eine Bank schreibt den Verwendungszweck nicht am Stueck, sondern in die
Teilfelder ``?20`` bis ``?29`` des MT940-Feldes ``:86:``. Die Bibliothek
mt940 fuegt sie wieder zusammen, und zwar in ``_join_result()``::

    value = ' '.join(result.get(key, [])) if space else ''.join(...)

``fints.utils.mt940_to_array()`` baut ``Transactions()`` ohne Argumente, also
mit ``space=False``. Jeder Abruf dieser App bekommt den Verwendungszweck
deshalb ohne Trenner, und auf dem Kontoauszug steht::

    RechnungKosten SRZDauerrechnungsnummer.20250908-BW035-00038612361Einze

waehrend die Sparkasse dort sechs Zeilen geschickt hat::

    Rechnung
    Kosten SRZ
    Dauerrechnungsnummer.
    20250908-BW035-00038612361
    Einzelrechnungsnummer.
    20261001-BW035-00042748...

ES GIBT ABER NICHT EINE RICHTIGE ANTWORT, und das ist der Grund fuer dieses
Modul. Die Bibliothek bietet beides an -- ``space=False`` und ``space=True``
-- und beide sind in genau einem der zwei Faelle falsch:

  Von der Bank gesetzter Text (Rechnungsabschluss, Darlehensleistung,
  Avalprovision) steht zeilenweise in den Teilfeldern. Jedes ``?2x`` ist eine
  Zeile fuer sich. Ohne Trenner kleben die Zeilen aneinander -- der Fall oben.

  Vom Zahler geschriebener SEPA-Text steht als EIN Feld hinter ``SVWZ+`` und
  wird ueber die Teilfelder hart umbrochen, mitten im Wort. Mit Trenner
  zerfaellt er, und das steht in dieser Datenbank bereits drin::

      SVWZ+MIETE UND BETRIEBSKOST ENPAUSCHALE B UERO LUTHERST RASSE

Unterscheiden laesst sich das an den Schluesselwoertern der Deutschen
Kreditwirtschaft: ``EREF+``, ``KREF+``, ``SVWZ+`` und die uebrigen. Wo eines
vorkommt, ist der Text getaggt und damit hart umbrochen; wo keines vorkommt,
sind es Zeilen der Bank.

WAS DAMIT NICHT GELOEST IST, offen gesagt: auch von der Bank gesetzter Text
kann an der Feldgrenze mitten in einer Zahl enden -- ``Zinssatz 4,`` und
``250 %`` sind zwei Teilfelder. Die bekommen jetzt ein Leerzeichen, das sie
vorher nicht hatten. Wo die Grenze lag, steht in den Daten nicht mehr drin;
die Wahl ist also, welcher der beiden Fehler haeufiger ist, und das ist
eindeutig der erste: aneinandergeklebte Zeilen in jedem Rechnungsabschluss
gegen ein zusaetzliches Leerzeichen in einer Zahl.

Damit das keine Doppelbuchungen ausloest, ignoriert der Fingerabdruck
Leerraum vollstaendig -- siehe booking_fingerprint.tidy(). Sonst waere eine
Buchung, die vorher ohne Leerzeichen gespeichert wurde, nach dieser Aenderung
nicht wiederzuerkennen, und der naechste Ueberschneidungstag haette sie
erneut importiert.

Ohne frappe und ohne mt940, aus demselben Grund wie fints_response und
fetch_outcome: was ueber den Inhalt einer Buchung entscheidet, muss ohne Bank
und ohne Bench pruefbar sein.
"""

#: Die Schluesselwoerter der Deutschen Kreditwirtschaft im MT940-Verwendungs-
#: zweck. Jedes ist vier Zeichen lang und steht vor einem '+'. Die Liste ist
#: die der mt940-Bibliothek (GVC_KEYS), hier ohne sie zu importieren -- dieses
#: Modul soll ohne die Bibliothek pruefbar bleiben.
DK_SCHLUESSEL = (
    "EREF+", "KREF+", "MREF+", "CRED+", "DEBT+", "COAM+", "OAMT+", "SVWZ+",
    "ABWA+", "ABWE+", "IBAN+", "BIC+", "PURP+", "MDAT+", "SQTP+", "ORCR+",
    "ORMR+", "DDAT+",
)


def ist_getaggt(text):
    """Traegt dieser Verwendungszweck ein DK-Schluesselwort?

    Dann ist er ein hart umbrochenes Einzelfeld und darf nicht an den
    Feldgrenzen auseinandergezogen werden.

    :param text: der rohe ``:86:``-Inhalt oder der zusammengesetzte Zweck
    :return: bool
    """
    oben = (text or "").upper()
    for schluessel in DK_SCHLUESSEL:
        if schluessel in oben:
            return True
    return False


def mit_leerzeichen(roh):
    """Soll dieser ``:86:``-Inhalt mit Leerzeichen zusammengesetzt werden?

    Die eine Frage, die der Aufrufer an die Bibliothek weiterreicht: sie
    kennt den Schalter ``space`` und kann ihn nur fuer den ganzen Lauf
    setzen, nicht je Buchung. Genau das holt diese Funktion nach.

    :param roh: der rohe Inhalt des Feldes :86: dieser einen Buchung
    :return: bool -- True fuer Zeilen der Bank, False fuer getaggten SEPA-Text
    """
    return not ist_getaggt(roh)


def zusammensetzen(teile):
    """Teilfelder zu einem Verwendungszweck, nach derselben Regel.

    Fuer Aufrufer, die die Teilfelder selbst in der Hand haben -- der
    ``.sta``-Import und die Tests. Der Abrufweg laesst die Bibliothek
    zusammensetzen und steuert sie nur ueber mit_leerzeichen().

    :param teile: die Teilfelder in ihrer Reihenfolge
    :return: der Verwendungszweck als eine Zeichenkette
    """
    stuecke = [str(t) for t in (teile or []) if t is not None and str(t) != ""]
    if not stuecke:
        return ""
    if ist_getaggt("".join(stuecke)):
        return "".join(stuecke)
    return " ".join(s.strip() for s in stuecke if s.strip())
