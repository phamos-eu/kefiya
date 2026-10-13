# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Everything the bank dialog says to the person waiting for it.

Progress and the release prompt, and nothing else. It sat inside
fints_controller.py, which is two thousand lines of FinTS protocol -- a file
nobody opens to find out what a dialog box says. The class never touched the
protocol; it only ever published realtime events, so it moves out whole.

fints_controller.py imports it back under its old name, so every existing
caller is unaffected.
"""

import frappe
from frappe import _

from kefiya.utils.fints_masking import mask_iban


class FinTSInteractive:
    def __init__(self, configuration):
        if not configuration:
            self.docname = None
            self.enabled = False
        else:
            self.docname = configuration["docname"]
            self.enabled = configuration["enabled"]
        # Which Kefiya Login this is about, as opposed to which form the user
        # is looking at. They are the same thing on the Kefiya Login form and
        # nowhere else: a transfer passes its own docname as the UI scope, so
        # everything that read the login out of `docname` was reading a
        # KEF-TRF-... name. The account context came back empty (the box named
        # no bank and no account, on the one screen where money moves), and a
        # box that tried to answer resolved against a document that is not a
        # login at all.
        self.fints_login = None
        self.progress = 0
        # Resolved on the first prompt, not here: a controller that never asks
        # for a TAN -- the scheduled run, most of the time -- should not pay a
        # lookup for a dialog it will not open.
        self._account_context_cache = None

    def login_name(self):
        """The Kefiya Login this run belongs to.

        Falls back to the UI scope, which is the same name on the Kefiya Login
        form -- so a caller that never sets the login behaves exactly as
        before.
        """
        return self.fints_login or self.docname

    def set_interactive_mode(self, enable):
        """Turn on/off interactive mode.

        :param enable: Turn on/off interactive mode
        :type enable: bool
        :return: None
        """
        self.enabled = enable

    def get_interactive_mode(self):
        """Get interactive mode.

        :return: bool
        """
        return self.enabled

    def show_progress_realtime(self, message, progress, reload=False):
        """Show a progressbar on client side.

        :param message: Message to display under the bar
        :param progress: 0 - 100
        :param reload: Reload the doc form, , defaults to False
        :type message: str
        :type progress: int
        :type reload: bool, optional
        :return: None
        """
        if self.enabled:
            frappe.publish_realtime(
                "fints_progressbar", {
                    "progress": progress,
                    "docname": self.docname,
                    "message": message,
                    "reload": reload
                }, user=frappe.session.user)

    def request_tan_mechanism(self, possible_tan_modes=None, possible_tan_mediums=None):
        """Request tan mechanism from user.

        :param possible_tan_modes: List of tan mechanisms
        :type mechanisms: list
        :return: None
        """
        self.request_tan_prompt(possible_tan_modes, possible_tan_mediums)

    def request_tan(self, possible_tan_modes=None, possible_tan_mediums=None, challenge=None):
        """Request a TAN from user

        :return: None
        """
        self.request_tan_prompt(possible_tan_modes, possible_tan_mediums,
                                request_tan=True, challenge=challenge)

    def request_mfa_confirmation(self, possible_tan_modes=None, possible_tan_mediums=None, challenge=None):
        """Request a solved MFA challenge from the user

        :return: None
        """
        self.request_tan_prompt(possible_tan_modes, possible_tan_mediums,
                                request_mfa_confirmation=True,
                                challenge=challenge)

    def account_context(self):
        """Which bank and which account this prompt is about.

        "Freigabe erforderlich -- TAN" said nothing else. During a collective
        fetch that is a dialog with no sender: a dozen accesses run one after
        the other, each may stop for a release, and the box on screen names
        none of them. The user then has to guess which banking app to open,
        and a release given in the wrong app is a release the waiting dialog
        never gets.

        So the prompt carries the access it belongs to. The IBAN is masked to
        its last four digits -- enough to tell two accounts of the same company
        apart, and no account number on a screen that may be shared.

        :return: dict with bank, account_name, iban (masked), label, detail
        """
        if self._account_context_cache is not None:
            return self._account_context_cache

        context = {}
        try:
            bank_account, iban = frappe.db.get_value(
                "Kefiya Login", self.login_name(),
                ["bank_account", "account_iban"]) or (None, None)

            bank = account_name = None
            if bank_account:
                bank, account_name, account_iban = frappe.db.get_value(
                    "Bank Account", bank_account,
                    ["bank", "account_name", "iban"]) or (None, None, None)
                # The login's own IBAN is the more specific one -- a login may
                # point at an account record that carries none.
                iban = iban or account_iban

            parts = [p for p in (bank, account_name or self.login_name()) if p]
            context = {
                "bank": bank,
                "account_name": account_name,
                "iban": mask_iban(iban) if iban else None,
                # Short enough for a dialog title, which truncates.
                "account_label": " · ".join(parts),
                # The full line, for a field inside the dialog.
                "account_detail": " · ".join(
                    parts + ([mask_iban(iban)] if iban else [])),
            }
        except Exception:
            # A prompt without its heading is worse than the old one; a prompt
            # that never appears because the heading could not be looked up is
            # worse still. The release matters, the label does not -- which is
            # why even the logging of the failure is guarded: an Error Log that
            # cannot be written must not be the reason a bank dialog is left
            # standing without anyone being asked to release it.
            try:
                frappe.log_error(
                    title="Kefiya: TAN prompt context could not be resolved",
                    message=frappe.get_traceback(),
                    reference_doctype="Kefiya Login",
                    reference_name=self.docname,
                )
            except Exception:
                pass

        self._account_context_cache = context
        return context

    def request_tan_prompt(self, possible_tan_modes, possible_tan_mediums=None, *, request_tan=False, request_mfa_confirmation=False, challenge=None):
        """Request tan mechanism from user.

        :param possible_tan_modes: List of tan mechanisms
        :type mechanisms: list
        :return: None
        """
        if self.enabled:
            params = {
                        "docname": self.docname,
                        # The login to answer against, which is NOT the form
                        # the user is looking at whenever money is moving.
                        # Every box resolves with this; without it a transfer's
                        # box answered against a KEF-TRF-... name.
                        "fints_login": self.login_name(),
                        "possible_tan_modes": possible_tan_modes,
                        "possible_tan_mediums": possible_tan_mediums,
                    }

            # Say which access is asking. Three prompts read this event -- the
            # form, the cockpit refresh and the outgoing-payments block -- and
            # each of them showed a bare "Verification required" before.
            params.update(self.account_context())

            # The challenge itself, where the bank sent one. A photoTAN image
            # travels as base64 in this payload: a few kilobytes, and the only
            # way the picture reaches the screen that has to show it.
            if challenge:
                params["challenge"] = challenge

            if request_tan:
                params["tan_required"] = True

            elif request_mfa_confirmation:
                params["mfa_required"] = True

            frappe.publish_realtime("fints_tan_interaction_required", params, user=frappe.session.user)
