# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import base64

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import decrypt, encrypt

from kefiya.utils import bank_logo

class KefiyaLogin(Document):
    def on_update(self):
        # Covers a logo uploaded or removed by hand in the form sidebar.
        if self.has_value_changed("bank_logo"):
            self.propagate_bank_logo()
        if not self.get("bank_logo") or self.has_value_changed("fints_url"):
            self.fetch_bank_logo()

    def clear_fints_caches(self):
        self.stored_client_blob = None
        self.stored_dialog_blob = None
        self.stored_tan_blob = None
        self.stored_tan_state_decoupled = None
        self.iban_list = None
        self.account_iban = None

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
        frappe.has_permission(ptype="write", doc=self, throw=True)
        self.clear_fints_caches()
        self.save()

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
