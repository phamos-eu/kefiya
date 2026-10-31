# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import base64

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import decrypt, encrypt

from kefiya.utils import bank_logo, fints_state_fields, login_siblings

class KefiyaLogin(Document):
    def on_update(self):
        # Covers a logo uploaded or removed by hand in the form sidebar.
        if self.has_value_changed("bank_logo"):
            self.propagate_bank_logo()
        # The lookup goes out to the bank's website. Not on every save: the
        # FinTS controller saves the login several times inside a bank dialog,
        # some of them while a TAN is pending, and a bank without a findable
        # icon would cost those saves a round of HTTP timeouts each. A new
        # login or a changed endpoint looks it up here; a missing logo is
        # filled in after the accounts were loaded (fetch_bank_logo_if_missing).
        if self.has_value_changed("fints_url") or (
                self.flags.in_insert and not self.get("bank_logo")):
            self.fetch_bank_logo()

    def sibling_logins(self):
        """Die anderen Logins desselben Bankzugangs.

        Ein Zugang -- eine Kennung, eine PIN -- traegt in kefiya je Konto
        ein eigenes Login. Der gespeicherte Verbindungszustand gehoert aber
        dem Zugang und nicht dem einzelnen Login: er wird unter den
        Geschwistern weitergereicht, damit eine TAN alle Konten freischaltet.
        """
        filters = login_siblings.same_access(
            self.name, self.blz, self.fints_login)
        if not filters:
            return []
        return [row["name"] for row in frappe.get_all(
            "Kefiya Login", filters=filters, fields=["name"],
            limit_page_length=0)]

    def discard_connection_state(self, siblings=True):
        """Den gespeicherten Verbindungszustand wegwerfen.

        Bei den Geschwistern mit, denn dort liegt derselbe. Wer nur diese
        eine Zeile leert, bekommt beim naechsten Abruf denselben Zustand
        vom Nachbarn zurueckgereicht -- _seed_client_state_from_sibling
        holt die frischeste Kopie, die es findet. Genau darum ging
        "Verbindung zuruecksetzen" ins Leere, solange es ein Login anfasste
        und der Zustand fuenfzehn gehoerte.

        Welche Felder das sind, steht in fints_state_fields.CLEARED, mit
        den Werten, die die Spalte auch annimmt. Was dem Konto gehoert --
        account_iban, iban_list -- bleibt stehen: sonst verloere ein
        Geschwister-Login, welches Konto es abruft.

        Gespeichert wird hier nichts. Das eigene Dokument braucht noch ein
        save(); die Geschwister sind bereits geschrieben.
        """
        for fieldname, value in fints_state_fields.CLEARED.items():
            self.set(fieldname, value)
        if not siblings:
            return
        for name in self.sibling_logins():
            frappe.db.set_value("Kefiya Login", name,
                                fints_state_fields.cleared(),
                                update_modified=False)

    def clear_fints_caches(self, siblings=False):
        """Den Verbindungszustand wegwerfen und dazu, was nur ein Mensch
        wegwerfen darf.

        Das ist fints_state_fields.IDENTITY: die Kontenliste der Bank, das
        Konto, das dieses Login abruft, und die Kundensystem-ID. Die drei
        gehoeren dem Login und nicht seinem Dialog, deshalb stehen sie nicht
        in CLEARED und ueberleben jedes automatische Verwerfen. Wer
        "Verbindung zuruecksetzen" drueckt, will sie trotzdem los -- bei der
        Kundensystem-ID heisst das, dass die Bank dieses Geraet neu kennen
        lernt und einmal eine TAN verlangt. Das ist der Sinn des Knopfes.

        Sie bleiben bei den Geschwistern stehen, auch wenn deren Zustand
        mitgeht.
        """
        self.discard_connection_state(siblings=siblings)
        for fieldname in fints_state_fields.IDENTITY:
            self.set(fieldname, None)

    @property
    def stored_client_blob(self):
        return self.read_crypted_string_to_blob(self.stored_client_state)

    @stored_client_blob.setter
    def stored_client_blob(self, value: bytes):
        self.client_state_updated = frappe.utils.now_datetime() if value else None
        self.stored_client_state = self.conv_blob_to_encrypted_string(value)

    @property
    def stored_dialog_blob(self):
        return self.read_crypted_string_to_blob(self.stored_dialog_state)

    @stored_dialog_blob.setter
    def stored_dialog_blob(self, value: bytes):
        self.dialog_state_updated = frappe.utils.now_datetime() if value else None
        self.stored_dialog_state = self.conv_blob_to_encrypted_string(value)

    @property
    def stored_tan_blob(self):
        return self.read_crypted_string_to_blob(self.stored_tan_state)

    @stored_tan_blob.setter
    def stored_tan_blob(self, value: bytes):
        self.tan_state_updated = frappe.utils.now_datetime() if value else None
        self.stored_tan_state = self.conv_blob_to_encrypted_string(value)

    @property
    def stored_vop_blob(self):
        return self.read_crypted_string_to_blob(self.stored_vop_state)

    @stored_vop_blob.setter
    def stored_vop_blob(self, value: bytes):
        self.stored_vop_state = self.conv_blob_to_encrypted_string(value)

    @property
    def stored_vop_id_blob(self):
        """The VoP-ID the parked TAN challenge has to be released with.

        Kept apart from the challenge because python-fints throws it away:
        NeedTANResponse.get_data() serialises the command and the TAN request
        and nothing else, so a challenge that goes into stored_tan_state with
        a VoP-ID comes back out without one. The approval segment
        HKVPA(vop_id=...) is then never sent and the bank answers 3945,
        "Freigabe ohne VOP-Bestaetigung nicht moeglich" -- which is the error
        every Volksbank transfer ended on.

        Same shape as the workaround one field up for `decoupled`, which the
        library drops for the same reason.
        """
        return self.read_crypted_string_to_blob(self.stored_vop_id_state)

    @stored_vop_id_blob.setter
    def stored_vop_id_blob(self, value: bytes):
        self.stored_vop_id_state = self.conv_blob_to_encrypted_string(value)

    @property
    def stored_gateway_blob(self):
        """Bei welchem Gateway der Bank dieser Zugang zuletzt war.

        Die Cookies der HTTPS-Sitzung, mehr nicht -- aber ohne sie landet
        die Wiederaufnahme eines pausierten Dialogs beim anderen Gateway
        der Bank, das ihn nicht kennt: "9800 FGW Gatewaywechsel A/B". Siehe
        gateway_session.
        """
        return self.read_crypted_string_to_blob(self.stored_gateway_state)

    @stored_gateway_blob.setter
    def stored_gateway_blob(self, value: bytes):
        self.stored_gateway_state = self.conv_blob_to_encrypted_string(value)

    @property
    def stored_vop_dialog_blob(self):
        return self.read_crypted_string_to_blob(self.stored_vop_dialog_state)

    @stored_vop_dialog_blob.setter
    def stored_vop_dialog_blob(self, value: bytes):
        self.stored_vop_dialog_state = self.conv_blob_to_encrypted_string(value)

    def clear_vop_state(self):
        """Drop a pending Verification-of-Payee challenge and its dialog."""
        self.stored_vop_blob = None
        self.stored_vop_dialog_blob = None
        self.vop_reference = None
        self.vop_result = None

    def read_crypted_string_to_blob(self, encoded_encrypted_string: str) -> bytes | None:
        """Decrypts ascii base64, and decrypts result to return blob"""
        if not encoded_encrypted_string:
            return None

        # decrypt to base64 string
        blob_str = decrypt(encoded_encrypted_string)

        # decode base64 string to blob
        return base64.b64decode(blob_str)

    def conv_blob_to_encrypted_string(self, blob: bytes) -> str | None:
        """Encrypts blob, and converts to ascii base64"""
        if not blob:
            return None

        # convert blob to base64 string
        blob_str = base64.b64encode(blob).decode()

        # encrypt the base64-blob-string
        return encrypt(blob_str)

    @frappe.whitelist(allow_guest=False)
    def reset_connection(self):
        """Der Knopf am Bankzugang. Er raeumt den ganzen Zugang auf, nicht
        nur diese Zeile -- sonst reicht der naechste Abruf den verworfenen
        Zustand vom Nachbar-Login zurueck."""
        frappe.has_permission(ptype="write", doc=self, throw=True)
        self.clear_fints_caches(siblings=True)
        self.save()

    @frappe.whitelist(allow_guest=False)
    def accounts_without_login(self):
        """Die Konten dieses Zugangs, die noch kein Login abruft.

        Die Bank hat sie genannt, als "Konten laden" gedrueckt wurde; was
        davon schon ein Login hat, steht in kefiya.
        """
        frappe.has_permission(ptype="read", doc=self, throw=True)
        belegt = [row.account_iban for row in frappe.get_all(
            "Kefiya Login", fields=["account_iban"], limit_page_length=0)]
        return login_siblings.unclaimed(self.iban_list, belegt)

    @frappe.whitelist(allow_guest=False)
    def add_account(self, iban, bank_account, erpnext_account,
                    login_name=None, account_kind=None):
        """Ein weiteres Konto desselben Zugangs abrufbar machen.

        Dieselbe Kennung, dieselbe PIN, ein anderes Konto: bisher wurde
        dafuer alles noch einmal getippt, PIN und Produkt-ID eingeschlossen.
        Die beiden werden hier serverseitig gelesen und weitergegeben -- sie
        verlassen die Instanz nicht.

        Die Bank entscheidet, ob es dieses Konto gibt: nur was in ihrer
        Kontenliste steht, bekommt einen Login.
        """
        frappe.has_permission(ptype="write", doc=self, throw=True)
        frappe.has_permission("Kefiya Login", ptype="create", throw=True)

        iban = login_siblings.normalise(iban)
        if iban not in login_siblings.named_by_bank(self.iban_list):
            frappe.throw(_("The bank does not list {0} under this access."
                           " Load the accounts first.").format(iban))
        if iban not in self.accounts_without_login():
            frappe.throw(_("{0} is already fetched by another access.")
                         .format(iban))

        konto = frappe.get_doc("Bank Account", bank_account)
        if login_siblings.normalise(konto.iban) != iban:
            frappe.throw(_("The bank account {0} has the IBAN {1}, not {2}.")
                         .format(bank_account, konto.iban or "-", iban))

        zugang = frappe.new_doc("Kefiya Login")
        for feld in login_siblings.COPIED:
            zugang.set(feld, self._carried_over(feld))
        zugang.login_name = login_name or konto.name
        zugang.account_iban = iban
        zugang.account_kind = account_kind or self.account_kind
        zugang.bank_account = konto.name
        zugang.company = konto.company
        zugang.erpnext_account = erpnext_account or konto.account
        zugang.insert()
        return zugang.name

    def _carried_over(self, fieldname):
        """Der Wert, den ein zweiter Login dieses Zugangs uebernimmt.

        Die verschluesselten Felder liegen im Dokument nicht als Klartext,
        sondern hinter get_password() -- ohne das erbt der neue Zugang eine
        Maske statt der PIN und laeuft in eine gesperrte Kennung.
        """
        if fieldname in login_siblings.SECRETS:
            return self.get_password(fieldname, raise_exception=False)
        return self.get(fieldname)

    # Bank logo -----------------------------------------------------------------

    def fetch_bank_logo_if_missing(self):
        """Look up the logo unless the login already has one."""
        if not self.get("bank_logo"):
            self.fetch_bank_logo()

    def fetch_bank_logo(self) -> str | None:
        """Look up the bank website's icon for the FinTS endpoint and attach it as the logo.

        Runs on save while the logo is missing or when the endpoint changed, and
        after the accounts were loaded. Returns the new file URL, or None when
        nothing was found. Never raises: a logo is cosmetic and must not block
        the login.
        """
        if not self.fints_url or _skip_lookups():
            return None
        try:
            found = bank_logo.fetch_bank_logo(self.fints_url)
            if not found:
                return None
            self.set_bank_logo(found[0])
        except Exception:
            frappe.log_error(title=_("Bank logo lookup failed for {0}").format(self.name))
            return None
        return self.bank_logo

    def set_bank_logo(self, content: bytes):
        """Attach PNG ``content`` as the bank logo, replacing an earlier one."""
        old_files = frappe.get_all("File", filters=self._bank_logo_filters(), pluck="name")

        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"{frappe.scrub(self.name)}-logo.png",
                "attached_to_doctype": self.doctype,
                "attached_to_name": self.name,
                "attached_to_field": "bank_logo",
                "is_private": 1,
                "content": content,
            }
        ).insert(ignore_permissions=True)

        self.db_set("bank_logo", file_doc.file_url)
        for name in old_files:
            frappe.delete_doc("File", name, ignore_permissions=True)

        self.propagate_bank_logo()
        self.notify_update()

    def propagate_bank_logo(self):
        """Copy the logo to the imports of this login so their list and form show it too."""
        frappe.db.set_value(
            "Kefiya Import",
            {"kefiya_login": self.name},
            "bank_logo",
            self.bank_logo,
            update_modified=False,
        )

    def _bank_logo_filters(self) -> dict:
        return {
            "attached_to_doctype": self.doctype,
            "attached_to_name": self.name,
            "attached_to_field": "bank_logo",
        }

    # TODO
    # @frappe.whitelist(allow_guest=False)
    # def solve_tan_challenge(self, tan: str | None=''):
    #     """
    #     If a tan was requested in a background task or some other previous status, the socket prompt might not have
    #     been shown. This method allows to trigger the challenge again to solve it.
    #     """
    #     frappe.has_permission(ptype="write", doc=self, throw=True)

    #     from erpnextfints.utils.fints_controller import FinTSController

    #     # Just init the FintsController instance should be enough to check for a tan challenge and make it continue:
    #     # TAN will be requested via socket communication during initialization time.
    #     FinTSController(self.name, {"docname": self.name, "enabled": True})


def _skip_lookups() -> bool:
    """No website lookups while migrating, installing or importing data."""
    flags = frappe.flags
    return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)
