# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Was ein verworfener FinTS-Zustand ist -- Feld fuer Feld, mit dem Wert,
den die Datenbank auch annimmt.

WOZU. Ein gespeicherter Verbindungszustand, aus dem sich kein Dialog mehr
bauen laesst, muss weg; sonst holt ihn der naechste Versuch zurueck und
scheitert genauso. ``_forget_client_state()`` tut das an zwei Stellen: am
eigenen Login ueber das Dokument und an den Geschwister-Logins ueber
``frappe.db.set_value``. Zwei Stellen, zwei Feldlisten -- und sie waren
verschieden.

WARUM NICHT None FUER ALLES, und das ist der Punkt, an dem es Geld gekostet
hat: ``stored_tan_state_decoupled`` ist ein Check. In MariaDB ist das
``int(1) NOT NULL``. Ein Dokument-``save()`` wandelt None still in 0 um,
``frappe.db.set_value`` schreibt das None in rohes SQL::

    pymysql.err.IntegrityError: (1048, "Column 'stored_tan_state_decoupled'
    cannot be null")

Am 05.10.2026 brach genau daran das Verwerfen ab, nachdem eine Ueberweisung
der Mietkonto schon auf der Leitung war. Der Nutzer las "Der gespeicherte
Zustand wurde verworfen, der naechste Versuch baut die Verbindung neu auf" --
verworfen war nichts. Das ``frappe.db.commit()`` am Ende wurde nie erreicht,
und der ``frappe.throw`` danach drehte auch das zurueck, was das Dokument
schon geschrieben hatte. Der Zugang war damit dauerhaft blockiert, und die
Meldung sagte das Gegenteil.

WAS HIER NICHT HINEINGEHOERT: ``account_iban`` und ``iban_list``. Die sagen,
welches Konto dieses Login abruft -- das ist die Identitaet des Logins, nicht
der Zustand seiner Verbindung. ``clear_fints_caches()`` raeumt sie mit weg,
weil ein Nutzer, der "Reset Connection" drueckt, genau das will. Ein
automatisches Verwerfen nach einem Fehlschlag darf einem Geschwister-Login
nicht das Konto unter den Fuessen wegziehen.

UND DIE KUNDENSYSTEM-ID, aus demselben Grund und mit demselben Preis, wenn
man es falsch macht. Sie ist die Kennung, unter der die Bank diese
Installation als GERAET kennt. PSD2 verlangt eine starke Authentifizierung
fuer den Kontozugriff nur alle 90 Tage (bei vielen Instituten inzwischen
180); dazwischen verzichtet die Bank darauf -- solange sie dasselbe Geraet
wiedererkennt. Eine Umsatzabfrage selbst braucht ohnehin keine TAN, das
sagt die Bank in HIPINS je Geschaeftsvorfall. Die TAN gilt der Anmeldung.

Die ID lag bis hierher an genau einer Stelle: im Blob ``stored_client_state``.
Mit ihm ging sie, und python-fints holt sich dann eine neue::

    def _ensure_system_id(self):
        if self.system_id != SYSTEM_ID_UNASSIGNED: return
        response = dialog.init(HKSYN3(SynchronizationMode.NEW_SYSTEM_ID))

Ein neues Geraet. Volle Authentifizierung, und die 90 Tage fangen bei null
an -- nach jedem Dialogfehler aufs Neue, und seit das Verwerfen die
Geschwister mitnimmt, fuer alle Logins des Zugangs auf einmal. An einem
Sparkassen-Zugang mit fuenfzehn Logins kostete ein einziger Fehlschlag die
TAN-Freiheit von fuenfzehn Konten.

Die ID ueberlebt deshalb das Verwerfen. Die Bibliothek sieht das
ausdruecklich vor -- ``FinTS3PinTanClient(..., system_id=...)``, und
``fetch_tan_mechanisms`` hat einen eigenen Zweig fuer "system_id was
persisted and given to the client, but nothing else". Ein mitgegebener Blob
sticht sie, weil ``set_data`` nach dem Konstruktor laeuft: der frische
Zustand gewinnt, die aufgehobene ID greift nur, wenn keiner mehr da ist.

Ohne frappe, damit der Abgleich gegen kefiya_login.json ohne Bench laeuft:
ein Zustandsfeld, das hier fehlt, faellt im Test auf und nicht erst beim
Senden.
"""


# Der verworfene Zustand. Der Wert ist der, den die Spalte auch annimmt --
# None fuer die Textfelder, 0 fuer den Check.
CLEARED = {
    "stored_client_state": None,
    "stored_dialog_state": None,
    "stored_tan_state": None,
    "stored_tan_state_decoupled": 0,
    "stored_vop_state": None,
    "stored_vop_dialog_state": None,
    "stored_vop_id_state": None,
    "stored_gateway_state": None,
    # Die Zeitstempel gehoeren dazu: ein "zuletzt aktualisiert" neben einem
    # leeren Zustand behauptet etwas, was nicht mehr da ist.
    "client_state_updated": None,
    "dialog_state_updated": None,
    "tan_state_updated": None,
    # Eine offene Empfaengerpruefung gehoert zu dem Dialog, der gerade
    # weggeworfen wird.
    "vop_reference": None,
    "vop_result": None,
}


#: Das Feld, in dem die Kundensystem-ID liegt -- neben dem Blob, nicht in
#: ihm, damit sie das Verwerfen ueberlebt.
SYSTEM_ID_FIELD = "fints_system_id"

#: Was python-fints einsetzt, solange die Bank noch keine vergeben hat.
#: Aufzuheben lohnt sich nur eine echte.
SYSTEM_ID_UNASSIGNED = "0"

#: Was dem Login gehoert und nicht seinem Dialog. Steht mit Absicht nicht in
#: CLEARED: ein automatisches Verwerfen nach einem Fehlschlag fasst nichts
#: davon an. clear_fints_caches() raeumt die Liste mit weg -- dort hat ein
#: Mensch "Verbindung zuruecksetzen" gedrueckt und will genau das, die
#: Neuanmeldung des Geraets eingeschlossen.
IDENTITY = ("iban_list", "account_iban", SYSTEM_ID_FIELD)


def usable_system_id(value):
    """Eine Kundensystem-ID, die aufzuheben sich lohnt -- sonst None.

    Die Null ist keine: sie heisst "die Bank hat noch keine vergeben". Sie
    aufzuheben waere schlimmer als nichts aufzuheben, weil der naechste
    Versuch sie mitgibt und die Bibliothek dann nicht mehr erkennt, dass
    sie synchronisieren muss.
    """
    text = str(value or "").strip()
    if not text or text == SYSTEM_ID_UNASSIGNED:
        return None
    return text


def cleared():
    """Eine eigene Kopie, damit kein Aufrufer die Vorlage veraendert."""
    return dict(CLEARED)
