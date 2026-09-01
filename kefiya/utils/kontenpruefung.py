# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein Konto gegen seinen Kontoauszug halten -- vorher und nachher.

auszug_pruefung stellt die drei Fragen, ohne frappe zu kennen. Hier steht
das eine Stueck, das die Instanz braucht: woher die Bewegungen des Systems
kommen, und wie das Ergebnis lesbar zurueckkommt.

Gedacht ist das fuer den Ablauf, den die Erfahrung erzwungen hat:

    pruefe()  ->  einlesen  ->  pruefe()

Kein Konto gilt als richtig, weil jemand es eingelesen hat. Es gilt als
richtig, wenn die zweite Pruefung keine Abweichung mehr meldet. Deshalb ist
dieses Modul ausdruecklich lesend: es aendert nichts, es urteilt nur, und es
kann darum vor jedem Eingriff ohne Sorge laufen.

Warum der Saldo und nicht die Anzahl der Buchungen: siehe auszug_pruefung.
Kurz -- kefiya holt beide Seiten einer Zaehlung selbst, aber den Kontostand
nennt die Bank.
"""

import frappe
from frappe import _

from kefiya.utils import auszug_pruefung


def bewegung_im_system(bank_account):
    """Eine Funktion (von, bis) -> Summe der Bewegungen, fuer vergleiche().

    Das Fenster ist ``von < date <= bis``: der Anfangssaldo eines Blattes
    gilt NACH dem Buchungstag, den er nennt, der Schlusssaldo NACH seinem.
    Waere die untere Grenze eingeschlossen, zaehlte jeder Blattwechsel den
    ersten Tag doppelt.
    """
    def zwischen(von, bis):
        summe = frappe.db.sql("""
            SELECT COALESCE(SUM(deposit), 0) - COALESCE(SUM(withdrawal), 0)
            FROM `tabBank Transaction`
            WHERE bank_account = %s AND date > %s AND date <= %s
              AND docstatus < 2
        """, (bank_account, von, bis))[0][0]
        return summe or 0
    return zwischen


def _konto_zur_nummer(nummer):
    """Das Bank Account zu einer Kontonummer aus :25:.

    Gesucht wird ueber das Ende der IBAN, weil bank_account_no in dieser
    Instanz mal mit Leerzeichen ("33 2866 60"), mal ohne und mal gar nicht
    gefuellt ist -- die IBAN ist der verlaessliche Teil.
    """
    passend = frappe.db.sql("""
        SELECT name FROM `tabBank Account`
        WHERE iban IS NOT NULL AND iban != ''
          AND RIGHT(iban, %s) = %s
    """, (len(nummer), nummer))
    return [zeile[0] for zeile in passend]


@frappe.whitelist()
def pruefe(file_url, bank_account=None):
    """Einen angehaengten Kontoauszug gegen das System halten.

    :param file_url: die .sta-Datei, wie sie im Dateimanager liegt
    :param bank_account: nur noetig, wenn die Kontonummer aus dem Auszug
        auf mehr als ein Bank Account passt
    :return: je Konto das Urteil aus auszug_pruefung.urteil, plus die
        Zahlen, die man beim Lesen sofort sehen will
    """
    _darf_pruefen()

    from kefiya.utils.statement_import import file_content
    text = file_content(file_url)
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")

    nach_konto = {}
    for blatt in auszug_pruefung.blaetter(text):
        nach_konto.setdefault(blatt.konto, []).append(blatt)

    if not nach_konto:
        frappe.throw(_("No statement sheets in {0}. A .sta file has a :25:"
                       " account line and :60F:/:62F: balances.")
                     .format(file_url))

    ergebnis = []
    for nummer, gelesen in sorted(nach_konto.items()):
        konto = bank_account or _eindeutig(nummer)
        gefaellt = auszug_pruefung.urteil(gelesen, bewegung_im_system(konto))
        ergebnis.append({
            "kontonummer": nummer,
            "bank_account": konto,
            "blaetter": len(gelesen),
            "von": gelesen[0].anfang_tag,
            "bis": gelesen[-1].ende_tag,
            "buchungen_im_auszug": sum(b.zeilen for b in gelesen),
            "kette_brueche": [_lesbar(b) for b in gefaellt["kette"]],
            "blaetter_die_nicht_aufgehen": [_lesbar(b)
                                            for b in gefaellt["summenprobe"]],
            "blinde_jahre": gefaellt["blind"],
            "auszug_brauchbar": gefaellt["brauchbar"],
            # Ohne diese Angabe liest sich ein leeres "abweichungen" wie ein
            # Freispruch, obwohl es auch heissen kann, dass ueber diesen
            # Zeitraum gar nicht geurteilt wurde.
            "spricht_fuer": [{"von": a, "bis": b}
                             for a, b in gefaellt["spricht_fuer"]],
            "abweichungen": [_lesbar_abweichung(a)
                             for a in gefaellt["abweichungen"]],
            "stimmt": gefaellt["brauchbar"] and not gefaellt["abweichungen"],
        })
    return {"konten": ergebnis,
            "alles_stimmt": all(e["stimmt"] for e in ergebnis)}


def _eindeutig(nummer):
    treffer = _konto_zur_nummer(nummer)
    if not treffer:
        frappe.throw(_("No bank account in this system ends in {0}."
                       " Pass bank_account explicitly.").format(nummer))
    if len(treffer) > 1:
        frappe.throw(_("{0} bank accounts end in {1}: {2}. Pass"
                       " bank_account explicitly.")
                     .format(len(treffer), nummer, ", ".join(treffer)))
    return treffer[0]


def _lesbar(bruch):
    return {"tag": bruch.tag, "erwartet": float(bruch.erwartet),
            "gefunden": float(bruch.gefunden), "fehlt": float(bruch.fehlt)}


def _lesbar_abweichung(abweichung):
    return {"von": abweichung.von, "bis": abweichung.bis,
            "bank": float(abweichung.bank),
            "system": float(abweichung.system),
            "fehlt": float(abweichung.fehlt)}


def _darf_pruefen():
    """Lesend, aber nicht fuer jeden: der Auszug nennt jede Buchung."""
    if frappe.session.user == "Administrator":
        return
    erlaubt = {"System Manager", "Accounts Manager", "Accounts User"}
    if not erlaubt & set(frappe.get_roles()):
        frappe.throw(_("Only accounting roles may read a bank statement."),
                     frappe.PermissionError)
