# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Ein Konto aus seinem Kontoauszug neu aufbauen -- und es beweisen.

Der Ablauf, den kontenpruefung.py ankuendigt, steht hier:

    pruefe()  ->  ersetzen  ->  pruefe()

Warum ersetzen und nicht ergaenzen: die Buchungen dieser Instanz stammen aus
drei Wegen -- einer Migration, dem FinTS-Abruf und frueheren Dateiimporten --
und die Pruefung gegen die Bank sagt fuer 22 von 22 Konten, dass das Ergebnis
nicht stimmt. Doppelt migriert, zweimal gelesene Seiten, Buchungen auf dem
falschen Konto, verworfene Stornos. Wer da nur die fehlenden Buchungen
nachtraegt, behaelt die doppelten. Was der Auszug sagt, ist die Wahrheit
ueber den Zeitraum, fuer den er spricht; was im System in diesem Zeitraum
steht, wird durch ihn ersetzt.

Drei Sicherungen, in dieser Reihenfolge:

1. Nur wo der Auszug SPRICHT. Ein Fenster, ueber das das Urteil schweigt --
   Kettenbruch, Blatt geht nicht auf -- wird nicht angefasst.
2. Nur was NIEMAND mehr braucht. Eine Buchung, an der Geld haengt -- ein
   Zahlungsbeleg zugeordnet, abgestimmt -- bleibt stehen. Der Auszug erkennt
   sie dann als vorhanden und bucht sie nicht ein zweites Mal.
3. Ein ZWEITER LESER. Die Blattpruefung liest die :61:-Zeilen selbst; der
   Import liest sie ueber die Bibliothek mt940. Bevor eine Buchung geloescht
   wird, muessen beide auf jedem Blatt dieselbe Summe finden. Die Bibliothek
   hatte einmal ein Vorzeichen falsch, und der Import haette es geerbt.

Und dann die Probe: nach dem Einlesen wird noch einmal geprueft, und erst
diese zweite Pruefung sagt, ob das Konto jetzt stimmt. Kein Konto gilt als
richtig, weil jemand es eingelesen hat.
"""

import frappe
from frappe import _

from kefiya.utils import auszug_pruefung
from kefiya.utils.kontenpruefung import bewegung_im_system, urteile
from kefiya.utils.statement_import import book_entries, read_entries

#: Womit eine Buchung vor dem Ersetzen geschuetzt ist: an ihr haengt Geld.
#: Ein Zahlungsbeleg ist zugeordnet, sie ist abgestimmt, oder ein Beleg in
#: der Kindtabelle verweist auf sie. Solche Buchungen bleiben stehen.
_GESCHUETZT = """
    bt.allocated_amount > 0
    OR bt.status IN ('Reconciled', 'Settled')
    OR EXISTS (SELECT 1 FROM `tabBank Transaction Payments` zahlung
               WHERE zahlung.parent = bt.name)
"""


def _bestand(bank_account, nach, bis):
    """Die Buchungen des Kontos im Fenster ``nach < date <= bis``.

    :return: Liste von {name, docstatus, geschuetzt}
    """
    return frappe.db.sql("""
        SELECT bt.name, bt.docstatus, ({0}) AS geschuetzt
        FROM `tabBank Transaction` bt
        WHERE bt.bank_account = %s AND bt.date > %s AND bt.date <= %s
          AND bt.docstatus < 2
        ORDER BY bt.date, bt.name
    """.format(_GESCHUETZT), (bank_account, nach, bis), as_dict=True)


def _im_fenster(eintraege, nach, bis):
    return [e for e in eintraege if nach < str(e["date"])[:10] <= bis]


def _zweitleser(blaetter, eintraege, blind):
    """Wo Blattpruefung und Bibliothek sich NICHT einig sind.

    Dieselbe Frage wie Frage 3 -- trifft eine Bewegung den Auszug? -- nur
    dass die Bewegung hier aus den Eintraegen kommt, die gebucht wuerden.
    """
    lesung = auszug_pruefung.bewegung_aus(eintraege)
    uneinig = []
    for lauf in auszug_pruefung.abschnitte(blaetter):
        uneinig.extend(auszug_pruefung.vergleiche(lauf, lesung, ausser=blind))
    return uneinig


def planen(file_url, bank_account=None):
    """Was das Ersetzen tun wuerde, je Konto und Fenster -- ohne zu schreiben.

    :return: {"konten": [...], "einlesbar": bool}
    """
    profil, eintraege, _notizen = read_entries(file_url, bank_account)
    if profil != "mt940":
        frappe.throw(_("Only an MT940 statement (.sta) can rebuild an"
                       " account: it is the format that carries the bank's"
                       " balances."))

    je_konto = {}
    for eintrag in eintraege:
        je_konto.setdefault(eintrag["bank_account"], []).append(eintrag)

    konten = []
    for nummer, konto, blaetter in urteile(file_url, bank_account):
        gefaellt = auszug_pruefung.urteil(blaetter, bewegung_im_system(konto))
        meine = je_konto.get(konto, [])
        uneinig = _zweitleser(blaetter, meine, set(gefaellt["blind"]))

        # Ein Fenster je Blatt, nicht je Lauf: so sagt der Plan je Jahrgang,
        # was geht und was kommt, und das Ersetzen schreibt je Jahrgang fest.
        fenster = []
        for nach, bis in gefaellt["fenster"]:
            bestand = _bestand(konto, nach, bis)
            fenster.append({
                "nach": nach, "bis": bis,
                "im_system": len(bestand),
                "geschuetzt": sum(1 for z in bestand if z.geschuetzt),
                "zu_ersetzen": sum(1 for z in bestand if not z.geschuetzt),
                "davon_gebucht": sum(1 for z in bestand
                                     if not z.geschuetzt and z.docstatus == 1),
                "einzulesen": len(_im_fenster(meine, nach, bis)),
            })

        konten.append({
            "kontonummer": nummer,
            "bank_account": konto,
            "brauchbar": gefaellt["brauchbar"],
            "vorher": [_lesbar(a) for a in gefaellt["abweichungen"]],
            "zweitleser_uneinig": [_lesbar(a) for a in uneinig],
            "fenster": fenster,
            # Eingelesen wird nur, wenn der Auszug spricht UND beide Leser
            # sich einig sind. Eine Unstimmigkeit ist kein Warnhinweis, sie
            # ist ein Halt.
            "einlesbar": bool(gefaellt["brauchbar"] and not uneinig),
        })
    return {"konten": konten,
            "einlesbar": bool(konten) and all(k["einlesbar"] for k in konten)}


def _loeschen(zeilen):
    """Buchungen aus dem Weg raeumen, gebuchte zuerst stornieren."""
    for zeile in zeilen:
        if zeile.docstatus == 1:
            beleg = frappe.get_doc("Bank Transaction", zeile.name)
            beleg.flags.ignore_permissions = True
            beleg.cancel()
        frappe.delete_doc("Bank Transaction", zeile.name, force=1,
                          ignore_permissions=True)


def ersetzen(file_url, bank_account=None, nur_fenster=None):
    """Die Fenster eines Auszugs im System durch den Auszug ersetzen.

    Laeuft nur ueber Konten, die planen() als einlesbar ausweist; je Fenster
    werden erst die ungeschuetzten Buchungen geloescht, dann die Eintraege
    des Auszugs gebucht -- ueber book_entries, denselben Weg wie jeder
    andere Dateiimport, der eine geschuetzte Buchung als vorhanden erkennt.
    Nach jedem Fenster -- einem Blatt, meist einem Jahrgang -- wird
    festgeschrieben: ein Abbruch laesst ganze Fenster zurueck, keine halben.

    :param nur_fenster: optional (nach, bis) -- nur dieses Fenster, fuer
        einen Lauf in Teilen
    :return: {"konten": [{bank_account, geloescht, gebucht, nachher,
              stimmt}], "alles_stimmt": bool}
    """
    plan = planen(file_url, bank_account)
    profil, eintraege, _notizen = read_entries(file_url, bank_account)
    je_konto = {}
    for eintrag in eintraege:
        je_konto.setdefault(eintrag["bank_account"], []).append(eintrag)

    ergebnis = []
    for konto in plan["konten"]:
        if not konto["einlesbar"]:
            ergebnis.append(dict(konto, geloescht=0, gebucht=0,
                                 uebersprungen=True))
            continue
        name = konto["bank_account"]
        geloescht = gebucht = 0
        for fenster in konto["fenster"]:
            nach, bis = fenster["nach"], fenster["bis"]
            if nur_fenster and (nach, bis) != tuple(nur_fenster):
                continue
            weg = [z for z in _bestand(name, nach, bis) if not z.geschuetzt]
            _loeschen(weg)
            geloescht += len(weg)
            gebucht += book_entries(_im_fenster(je_konto[name], nach, bis),
                                    dry_run=False)["created"]
            frappe.db.commit()

        nachher = auszug_pruefung.urteil(
            next(b for _n, k, b in urteile(file_url, bank_account)
                 if k == name),
            bewegung_im_system(name))
        ergebnis.append(dict(
            konto, geloescht=geloescht, gebucht=gebucht,
            nachher=[_lesbar(a) for a in nachher["abweichungen"]],
            stimmt=nachher["brauchbar"] and not nachher["abweichungen"]))

    return {"konten": ergebnis,
            "alles_stimmt": bool(ergebnis)
            and all(k.get("stimmt") for k in ergebnis)}


def _lesbar(abweichung):
    return {"nach": abweichung.von, "bis": abweichung.bis,
            "bank": float(abweichung.bank), "system": float(abweichung.system),
            "fehlt": float(abweichung.fehlt)}


# --------------------------------------------------------------------------
# Der Weg von der Oberflaeche
# --------------------------------------------------------------------------

def _darf_ersetzen(bank_account):
    """Loeschen und Anlegen von Buchungen auf einem benannten Konto."""
    if bank_account:
        frappe.has_permission("Bank Account", ptype="write", doc=bank_account,
                              throw=True)
    frappe.has_permission("Bank Transaction", ptype="delete", throw=True)
    frappe.has_permission("Bank Transaction", ptype="create", throw=True)


@frappe.whitelist()
def plan(file_url, bank_account=None):
    _darf_ersetzen(bank_account)
    return planen(file_url, bank_account)


@frappe.whitelist()
def start(file_url, bank_account=None, docname=None):
    """Das Ersetzen anstossen -- im Hintergrund, mit Bericht am Dokument.

    Tausende Buchungen loeschen und neu anlegen dauert Minuten; ein Aufruf
    aus dem Browser wuerde abbrechen, und ein abgebrochener Lauf ist genau
    der halbe Zustand, den ersetzen() vermeidet. Der Bericht landet als
    Kommentar am Import-Dokument, damit er bleibt.
    """
    _darf_ersetzen(bank_account)
    if not planen(file_url, bank_account)["einlesbar"]:
        frappe.throw(_("The statement cannot rebuild this account: see the"
                       " plan. Nothing was changed."))
    frappe.enqueue(
        "kefiya.utils.auszug_einlesen.im_hintergrund", queue="long",
        timeout=4 * 3600, file_url=file_url, bank_account=bank_account,
        docname=docname, user=frappe.session.user)
    return {"eingeplant": True}


def im_hintergrund(file_url, bank_account=None, docname=None, user=None):
    ergebnis = ersetzen(file_url, bank_account)
    if docname:
        frappe.get_doc({
            "doctype": "Comment", "comment_type": "Info",
            "reference_doctype": "Kefiya Bank Statement Import",
            "reference_name": docname,
            "content": _bericht(ergebnis),
        }).insert(ignore_permissions=True)
    frappe.publish_realtime("kefiya_auszug_eingelesen", ergebnis, user=user)
    return ergebnis


def _bericht(ergebnis):
    zeilen = []
    for konto in ergebnis["konten"]:
        if konto.get("uebersprungen"):
            zeilen.append(_("{0}: skipped, the statement does not speak"
                            " for it.").format(konto["bank_account"]))
            continue
        zeilen.append(_("{0}: {1} bookings replaced by {2} from the"
                        " statement. {3}").format(
            konto["bank_account"], konto["geloescht"], konto["gebucht"],
            _("The account now matches the bank.") if konto["stimmt"]
            else _("{0} periods still deviate.").format(
                len(konto["nachher"]))))
    return "<br>".join(frappe.utils.escape_html(z) for z in zeilen)
