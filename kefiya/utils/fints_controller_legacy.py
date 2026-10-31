# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
import json
import re

from dateutil.relativedelta import relativedelta
from fints.client import FinTS3PinTanClient, FinTSClientMode
from fints.exceptions import FinTSConnectionError, FinTSSCARequiredError

from frappe import _
from frappe.utils import cint, escape_html, now_datetime
from frappe.utils.file_manager import (
    save_file,
    get_file,
    get_content_hash,
)
from .fints_bank_errors import LOGIN_ERRORS, FetchFailed, explain_login_rejection, fetch_fints_transactions
from .import_bank_transaction import (
    ImportBankTransaction,
    resolve_incremental_from_date,
)
from .auto_reconcile import run_after_import
from .assign_payment_controller import AssignmentController

# Bank codes for a request the bank rejects because it lacks strong
# customer authentication (PSD2). DKB answers HKSPA with 9210, or with
# HTTP 400 and no bank code at all.
SCA_REJECTION_CODES = ("9075", "9210")
SCA_REJECTION_HTTP_STATUS = "Bad status code 400"


class TanAuthenticationRequired(frappe.ValidationError):
    """The bank wants a TAN for the login, which the legacy controller can't do."""


def get_bank_answer(error):
    """Short form of a fints error for the message, e.g. "9210 Auftrag abgelehnt"."""
    if isinstance(error, FinTSConnectionError):
        return str(error).replace("Bad status code", "HTTP")
    answers = re.findall(r"code='(\d+)', reference_element=[^,]*, text='([^']*)'", str(error))
    return ", ".join("{0} {1}".format(code, text) for code, text in answers) or None


def throw_tan_authentication_required(kefiya_login, bank_answer=None):
    """Explain that the bank wants a TAN and how to switch TAN on for this login.

    With write access to the Kefiya Login, the dialog's button switches TAN
    authentication on and tries again (kefiya.interactive.use_tan_and_try_again).
    """
    can_edit = frappe.has_permission("Kefiya Login", "write", kefiya_login.name)
    login = frappe.bold(escape_html(kefiya_login.name))
    paragraphs = [
        _(
            "The bank did not send the accounts for {0}. It wants the login"
            " confirmed with a TAN, e.g. in your banking app (PSD2)."
        ).format(login),
        _("TAN authentication is off for this Kefiya Login, so Kefiya can't ask for a TAN."),
    ]
    if can_edit:
        paragraphs.append(_(
            "Click {0} to switch it on and load again. Then choose your TAN"
            " method and confirm in your banking app."
        ).format(frappe.bold(_("Use TAN and Try Again"))))
    else:
        paragraphs.append(_(
            "Ask someone who can edit this Kefiya Login to tick {0}."
        ).format(frappe.bold(_("Use TAN Authentication"))))

    message = "".join("<p>{0}</p>".format(p) for p in paragraphs)
    if bank_answer:
        message += '<p class="text-muted small">{0}</p>'.format(
            _("Bank answer: {0}").format(escape_html(bank_answer))
        )

    frappe.throw(
        message,
        exc=TanAuthenticationRequired,
        title=_("TAN Required"),
        primary_action={
            "label": _("Use TAN and Try Again"),
            "client_action": "kefiya.interactive.use_tan_and_try_again",
            "args": kefiya_login.name,
        } if can_edit else None,
    )


class FinTSController:
    def __init__(self, kefiya_login_docname, interactive=False):
        self.kefiya_login = frappe.get_doc("Kefiya Login", kefiya_login_docname)
        self.name = self.kefiya_login.name
        self.interactive = FinTSInteractive(interactive)
        self.__init_fints_connection()
        with explain_login_rejection(self.name):
            self.__init_tan_processing()
            with self.fints_connection:
                self.__get_fints_accounts()

    def __init_fints_connection(self):
        """Private: Initialise new fints connection.

        :return: None
        """
        self.interactive.show_progress_realtime(
            _("Initialise connection"), 10, reload=False
        )
        if hasattr(self, "fints_connection"):
            return

        try:
            self.fints_connection = FinTS3PinTanClient(
                self.kefiya_login.blz,
                self.kefiya_login.fints_login,
                self.kefiya_login.get_password("fints_password"),
                self.kefiya_login.fints_url,
                product_id=self.kefiya_login.get_password("product_id"),
                mode=FinTSClientMode.INTERACTIVE,
            )
        except Exception as e:
            frappe.throw(
                _("Could not conntect to fints server with error<br>{0}").format(e)
            )

    def __init_tan_processing(self):
        """Show a progressbar on client side.

        :todo: Implement PSD2 requirements
        :return: None
        """
        self.interactive.show_progress_realtime(
            _("Initialise TAN settings"), 20, reload=False
        )
        self.fints_connection.fetch_tan_mechanisms()

        if self.fints_connection.init_tan_response:
            throw_tan_authentication_required(self.kefiya_login)

    def __get_fints_accounts(self):
        """Fetch FinTS Accounts.

        :return: None
        """
        self.interactive.show_progress_realtime(
            _("Loading accounts"), 30, reload=False
        )
        if not hasattr(self, 'fints_accounts'):
            try:
                self.fints_accounts = self.fints_connection.get_sepa_accounts()
            except FinTSSCARequiredError:
                throw_tan_authentication_required(self.kefiya_login)
            except Exception as e:
                # The bank has most likely ended the dialog. Don't let fints
                # send the dialog end (HKEND) on exit: it fails too, and its
                # non-validation error turns this message into a server error
                # with an empty "Message" dialog.
                if self.fints_connection._standing_dialog:
                    self.fints_connection._standing_dialog.open = False

                if isinstance(e, LOGIN_ERRORS):
                    raise

                # fints raises a plain Exception holding the bank response,
                # or a FinTSConnectionError for the HTTP status
                if (
                    isinstance(e, FinTSConnectionError)
                    and str(e) == SCA_REJECTION_HTTP_STATUS
                ) or any("code='{0}'".format(c) in str(e) for c in SCA_REJECTION_CODES):
                    frappe.log_error(title="Kefiya: bank rejected account list")
                    throw_tan_authentication_required(self.kefiya_login, get_bank_answer(e))
                frappe.throw(_(
                    "Could not load sepa accounts with error:<br>{0}"
                ).format(e))

    def __get_fints_account_by_key(self, key, value):
        try:
            account = None
            for acc in self.fints_accounts:
                if getattr(acc, key) == value:
                    account = acc
                    break
        except AttributeError:
            frappe.throw(_(
                "SEPA account object has no key '{0}'"
            ).format(key))
        # Account can be None
        return account

    @staticmethod
    def get_kefiya_import_file_content(kefiya_import):
        """Get Kefiya Import json file content as json.

        :param kefiya_import: kefiya_import doc
        :type kefiya_import: kefiya_import doc
        :return: Transaction from file as json object list
        """
        if kefiya_import.file_url:
            content = get_file(kefiya_import.file_url)[1]
            # Check content hash for file manipulations
            if kefiya_import.file_hash == get_content_hash(content):
                return json.loads(
                    content,
                    strict=False
                )
            else:
                raise ValueError('File hash does not match')
        else:
            return []

    def get_fints_connection(self):
        """Get the FinTS Connection object.

        :return: FinTS3PinTanClient
        """
        return self.fints_connection

    def get_fints_accounts(self):
        """Get FinTS Accounts.

        :return: List of SEPAAccount objects.
        """
        self.interactive.show_progress_realtime(
            _("Loading accounts completed"), 100, reload=False
        )
        return self.fints_accounts

    def get_fints_account_by_iban(self, iban):
        """Get FinTS account by iban number.

        :param iban: bank iban number
        :type iban: str
        :return: SEPAAccount
        """
        return self.__get_fints_account_by_key("iban", iban)

    def get_fints_account_by_nr(self, account_nr):
        """Get FinTS account by account number.

        :param account_nr: bank account number
        :type account_nr: str
        :return: SEPAAccount
        """
        return self.__get_fints_account_by_key(
            "accountnumber",
            account_nr
        )

    def get_fints_transactions(self, start_date=None, end_date=None):
        """Get FinTS transactions.

        Defaults to the login's allowed_sync_days_in_past (90 when unset) up
        to yesterday. Older history can be requested; whether the bank
        serves it is up to the bank (PSD2).

        :param start_date: Date to start the fetch
        :param end_date: Date to end the fetch
        :type start_date: date
        :type end_date: date
        :return: Transaction as json object list
        """
        allowed_days = cint(self.kefiya_login.allowed_sync_days_in_past) or 90

        if start_date is None:
            start_date = now_datetime().date() - relativedelta(days=allowed_days)

        if end_date is None:
            end_date = now_datetime().date() - relativedelta(days=1)

        account = self.get_fints_account_by_iban(
            self.kefiya_login.account_iban)
        return fetch_fints_transactions(
            self.fints_connection, account, start_date, end_date
        )

    def get_fints_holdings(self):
        """Fetch securities holdings (Depot / Wertpapiere) for the account.

        :return: list of plain dicts (isin, security_name, quantity, price,
            market_value, currency, valuation_date, securities_account)
        """
        with self.fints_connection:
            account = self.get_fints_account_by_iban(
                self.kefiya_login.account_iban)
            holdings = self.fints_connection.get_holdings(account)
            result = []
            for h in holdings or []:
                result.append({
                    "isin": getattr(h, "isin", None),
                    "security_name": getattr(h, "name", None),
                    "quantity": getattr(h, "pieces", None),
                    "price": getattr(h, "market_value", None),
                    "market_value": getattr(h, "total_value", None),
                    "currency": getattr(h, "value_symbol", None),
                    "valuation_date": getattr(h, "valuation_date", None),
                    "securities_account": self.kefiya_login.account_iban,
                })
            return result

    def import_fints_transactions(self, kefiya_import):
        """Create payment entries by FinTS transactions.

        :param kefiya_import: kefiya_import doc name
        :type kefiya_import: str
        :return: List of max 10 transactions and all new payment entries
        """
        try:
            self.interactive.show_progress_realtime(
                _("Start transaction import"), 40, reload=False
            )
            curr_doc = frappe.get_doc("Kefiya Import", kefiya_import)
            new_bank_transactions = None

            # Incremental fetch: when no start date was entered, begin at the
            # date of the most recently imported transaction for this account
            # (clamped to the FinTS 90-day window) and fetch up to today.
            if not curr_doc.from_date:
                curr_doc.from_date = resolve_incremental_from_date(
                    self.kefiya_login.bank_account,
                    self.kefiya_login.allowed_sync_days_in_past,
                )
                curr_doc.to_date = now_datetime().date()

            tansactions = self.get_fints_transactions(
                curr_doc.from_date,
                curr_doc.to_date
            )

            # python-fints 5.x returns a flat list of transactions; the older
            # code (and the partial CAMT refactor) assumed a list of per-account
            # buckets. Normalise to a flat list before the length check and any
            # indexing, so empty or nested results are handled correctly.
            if tansactions and isinstance(tansactions[0], list):
                flat = []
                for entry in tansactions:
                    if isinstance(entry, list):
                        flat.extend(entry)
                    else:
                        flat.append(entry)
                tansactions = flat

            if(len(tansactions) == 0):
                frappe.msgprint(_("No transaction found"))
            else:
                try:
                    save_file(
                        kefiya_import + ".json",
                        json.dumps(
                            tansactions, ensure_ascii=False
                        ).replace(",", ",\n").encode('utf8'),
                        'Kefiya Import',
                        kefiya_import,
                        folder='Home/Attachments/FinTS',
                        decode=False,
                        is_private=1,
                        df=None
                    )
                except Exception as e:
                    frappe.throw(_("Failed to attach file"), e)

                first_txn = tansactions[0]
                last_txn = tansactions[-1]

                curr_doc.start_date = first_txn.get("date") or first_txn.get("ValueDate.Date")
                curr_doc.end_date   = last_txn.get("date") or last_txn.get("ValueDate.Date")

                importer = ImportBankTransaction(self.kefiya_login, self.interactive)
                # FinTS delivers MT940; use the MT940-aware import path, not the CAMT one.
                importer.old_kefiya_import(tansactions)

                if len(importer.bank_transactions) == 0:
                    frappe.msgprint(_("No new payments found"))
                else:
                    # Save payment entries
                    frappe.db.commit()

                    frappe.msgprint(_(
                        "Found a total of '{0}' payments"
                    ).format(
                        len(importer.bank_transactions)
                    ))
                new_bank_transactions = importer.bank_transactions

            curr_doc.submit()
            self.interactive.show_progress_realtime(
                _("Bank Transaction import completed"), 100, reload=False
            )

            # Make the import durable before reconciliation runs, so a rollback
            # inside reconciliation can never revert the submitted import.
            frappe.db.commit()

            # Optional automatic reconciliation of the freshly imported window
            # (guarded so it can never break the import).
            try:
                run_after_import(
                    self.kefiya_login.name, curr_doc.from_date, curr_doc.to_date
                )
            except Exception:
                frappe.log_error(
                    title="Kefiya auto-reconcile entrypoint failed",
                    message=frappe.get_traceback(),
                )

            # auto_assignment = AssignmentController().auto_assign_payments()
            return {
                "transactions": tansactions,
                "payments": new_bank_transactions,
                # "assignment": auto_assignment
            }
        except FetchFailed:
            # Already a clear message for the user; don't wrap it
            raise
        except Exception as e:
            frappe.throw(_(
                "Error parsing transactions<br>{0}"
            ).format(str(e)), frappe.get_traceback())


class FinTSInteractive:
    def __init__(self, configuration):
        if not configuration:
            self.docname = None
            self.enabled = False
        else:
            self.docname = configuration["docname"]
            self.enabled = configuration["enabled"]
        self.progress = 0

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
