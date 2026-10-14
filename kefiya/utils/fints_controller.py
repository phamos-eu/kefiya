# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import contextlib
import frappe
import json
import mt940

from dateutil.relativedelta import relativedelta
from fints.client import FinTS3PinTanClient, FinTSClientMode, NeedTANResponse, NeedRetryResponse

from frappe import _
from frappe.utils import now_datetime, cint
from frappe.utils.file_manager import (
    save_file,
    get_file,
    get_content_hash,
)
from .import_bank_transaction import (
    ImportBankTransaction,
    resolve_incremental_from_date,
)
from .auto_reconcile import run_after_import
from .assign_payment_controller import AssignmentController

class InitFailedException(Exception):
    pass

class TanInteractionRequired(InitFailedException):
    pass

class FinTSController:
    def __init__(self, kefiya_login_docname:str, interactive:bool=False, tan_mode:str=None, tan_medium:str=None, tan:str=...):
        self.kefiya_login = frappe.get_doc("Kefiya Login", kefiya_login_docname)

        # If this is load during a user interaction, verify its permissions (ignore for scheduled background tasks)
        if interactive and interactive["enabled"]:
            frappe.has_permission(ptype="write", doc=self.kefiya_login, throw=True)

        self.name = self.kefiya_login.name
        self.interactive = FinTSInteractive(interactive)

        self.__init_fints_connection()

        # If state is new and did not setup tan requirements, init tan mode (and probably ask user for mode and medium).
        if self.__init_tan_mode(tan_mode, tan_medium, tan) is False:
            raise TanInteractionRequired()

        # If there is an open TAN request, try to fulfill it
        if self.kefiya_login.stored_tan_blob:
            with self.fints_connection.resume_dialog(self.kefiya_login.stored_dialog_blob):
                tan_request = NeedRetryResponse.from_data(self.kefiya_login.stored_tan_blob)

                # this decoupled setting is missing everywhere, so decoupled requests (like pushTAN 2.0) cannot be handled without this
                tan_request.decoupled = self.kefiya_login.stored_tan_state_decoupled
                self.fints_connection.init_tan_response = self.fints_connection.send_tan(tan_request, tan)

                # on failure update status and skip
                if self.is_tan_required_and_requested(self.fints_connection.init_tan_response):
                    raise TanInteractionRequired()

                # on success clear stored tan state
                self.__persist_fints_state()

        # After successful login/tan verification fetch available accounts if not already present
        if self.__fetch_fints_accounts() is False:
            raise InitFailedException()

        # Afterwards the controller/connection is ready to go for further operations
        if self.kefiya_login.failed_connection:
            self.kefiya_login.failed_connection = 0
            self.kefiya_login.save()

        self.interactive.show_progress_realtime(
           _("Connection established"), 100, reload=False
        )

    def __init_fints_connection(self):
        """Private: Initialise new fints connection.

        :return: None
        """
        if hasattr(self, "fints_connection"):
            return

        self.interactive.show_progress_realtime(
            _("Initialise connection"), 10, reload=False
        )

        try:
            self.fints_connection = FinTS3PinTanClient(
                self.kefiya_login.blz,
                self.kefiya_login.fints_login,
                self.kefiya_login.get_password("fints_password"),
                self.kefiya_login.fints_url,
                product_id=self.kefiya_login.get_password("product_id"),
                mode=FinTSClientMode.INTERACTIVE,
                from_data=self.kefiya_login.stored_client_blob,
            )
        except Exception as e:
            frappe.throw(
                _("Could not conntect to fints server with error<br>{0}").format(e)
            )

    @contextlib.contextmanager
    def trusted_client_context(self):
        """
        Opens the fints client context (will most likely generate a new fints dialog) and checks if a TAN is requested.
        If so, it will be requested from the user and the context body will be skipped.
        """
        with self.fints_connection:
            if self.is_tan_required_and_requested(self.fints_connection.init_tan_response):
                raise TanInteractionRequired()

            yield self.fints_connection

    def is_tan_required_and_requested(self, response) -> bool:
        """Checks the given response, if a TAN is required. If so, it is requested from the user.
        :param response: The response object from the FinTS server
        :return: True if a TAN is required (and requested), False otherwise
        """
        if isinstance(response, NeedTANResponse):
            self.ask_for_tan(response)
            return True

        return False

    def __persist_fints_state(self, tan_state=None, clear:bool=False):
        """Persist the current client/dialog state to the database.
        :param tan_state: An additional TAN state to persist if given.
        :param clear: Reset all the stored states
        :return: None
        """
        self.kefiya_login.stored_client_blob = self.fints_connection.deconstruct(including_private=True)

        if tan_state and isinstance(tan_state, NeedTANResponse):
            self.kefiya_login.stored_tan_blob = tan_state.get_data()
            self.kefiya_login.stored_tan_state_decoupled = tan_state.decoupled
            self.kefiya_login.stored_dialog_blob = self.fints_connection.pause_dialog()
        else:
            self.kefiya_login.stored_tan_blob = None
            self.kefiya_login.stored_tan_state_decoupled = None
            self.kefiya_login.stored_dialog_blob = None

        self.kefiya_login.save()

    def __init_tan_mode(self, tan_mode:str=None, tan_medium:str=None, tan:str=...) -> bool:
        """
        Initializes the TAN mode to use. If there are multiple TAN mechanisms available, the user is asked to choose one (if no choise is permitted).

        :param tan_mode: The name of the TAN mechanism to use (choice was already made, if permitted)
        :param tan_medium: The name of the TAN medium (on several devices, ...) to use (choice was already made, if permitted)
        :return bool: Indicates if initialization was complete or should be stopped (because user interaction is required)
        """
        if not self.fints_connection.get_current_tan_mechanism():
            self.interactive.show_progress_realtime(
                _("Initialise TAN settings"), 20, reload=False
            )

            self.fints_connection.fetch_tan_mechanisms()

            mechanisms = self.fints_connection.get_tan_mechanisms().items()

            if len(mechanisms) == 0:
                frappe.throw(_("No TAN mechanisms available"))

            mechanism_names = ["{p.security_function}: {p.name}".format(p=m[1]) for m in mechanisms]

            # If there is only one tan mechanism available, use it without asking
            if len(mechanisms) == 1:
                tan_mode = mechanism_names[0]

            if tan_mode and tan_mode in mechanism_names:
                # set tan mechanism
                selected_mode = list(mechanisms)[mechanism_names.index(tan_mode)]
                self.fints_connection.set_tan_mechanism(selected_mode[0])
            else:
                # request tan mechanism from user
                self.interactive.request_tan_mechanism(mechanism_names)
                return False

        # discover tan medium handling
        if self.fints_connection.selected_tan_medium is None and self.fints_connection.is_tan_media_required():
            m = self.fints_connection.get_tan_media() # this request can already trigger another TAN request, which is not cool, but for other banks this makes more sense hopefully
            medium_names = None

            if len(m[1]) == 1:
                self.fints_connection.set_tan_medium(m[1][0])
            elif len(m[1]) == 0:
                # This is a workaround for when the dialog already contains return code 3955.
                # This occurs with e.g. Sparkasse Heidelberg, which apparently does not require us to choose a
                # medium for pushTAN but is totally fine with keeping "" as a TAN medium.
                self.fints_connection.selected_tan_medium = ""
            elif tan_medium and tan_medium in [mm.tan_medium_name for mm in m[1]]:
                self.fints_connection.set_tan_medium(next(mm for mm in m[1] if mm.tan_medium_name == tan_medium))
            else:
                # Multiple tan media available. Prompt user
                medium_names = []
                for mm in m[1]:
                    medium_names.append("Medium {p.tan_medium_name}: Phone no. {p.mobile_number_masked}, Last used {p.last_use}".format(p=mm))

                self.interactive.request_tan_mechanism([tan_mode], medium_names)
                return False

            # if the request already claimed a tan, persist state and ask for tan
            if self.fints_connection.init_tan_response and self.fints_connection._standing_dialog:
                # (Only!) If the tan request opened a dialog, which can be paused for later (continue after user-interaction)
                # If not, it needs to be skipped (leads to multiple requests on the phone, but there is now option to persist the state, and it works)
                self.ask_for_tan(self.fints_connection.init_tan_response, possible_tan_modes=[tan_mode], possible_tan_mediums=medium_names)
                return False

        return True

    def ask_for_tan(self, response, *, decoupled=None, possible_tan_modes=None, possible_tan_mediums=None):
        if response:
            self.__persist_fints_state(response)

        if response.decoupled if decoupled is None else decoupled:
            self.interactive.request_mfa_confirmation(possible_tan_modes=possible_tan_modes, possible_tan_mediums=possible_tan_mediums)
        else:
            self.interactive.request_tan(possible_tan_modes=possible_tan_modes, possible_tan_mediums=possible_tan_mediums)

    def __fetch_fints_accounts(self) -> bool:
        """Fetch FinTS Accounts.

        :return: True on success. False or an exception otherwise.
        """
        if hasattr(self, 'fints_accounts'):
            return True

        if self.kefiya_login.iban_list:
            cached_accounts = json.loads(self.kefiya_login.iban_list)
            if cached_accounts and isinstance(cached_accounts[0], dict):
                self.fints_accounts = [frappe._dict(acc) for acc in cached_accounts]
                return True

        try:
            with self.trusted_client_context() as client:
                self.interactive.show_progress_realtime(
                    _("Loading accounts"), 30, reload=False
                )

                accounts_response = client.get_sepa_accounts()
                if self.is_tan_required_and_requested(accounts_response):
                    return False

                self.fints_accounts = accounts_response
                self.kefiya_login.iban_list = json.dumps(
                    [a.iban for a in self.fints_accounts if getattr(a, "iban", None)]
                )

                # Reading the accounts is part of the first initialization. So after this was successful, the
                # client state can be persisted for future use.
                self.__persist_fints_state()

                self.interactive.show_progress_realtime(
                    _("Loading accounts completed"), 100, reload=True
                )
                return True

        except TanInteractionRequired:
            raise
        except Exception as e:
            frappe.throw(_(
                "Could not load sepa accounts with error:<br>{0}"
            ).format(e))

        return False

    def __get_fints_account_by_key(self, key, value):
        if not value:
            return None

        for acc in self.fints_accounts:
            if isinstance(acc, dict):
                if acc.get(key) == value:
                    return acc
            else:
                try:
                    if getattr(acc, key) == value:
                        return acc
                except AttributeError:
                    frappe.throw(_(
                        "SEPA account object has no key '{0}'"
                    ).format(key))

        # Account can be None
        return None


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

        The fetch window is limited to the login's allowed_sync_days_in_past
        (default 90). Also only transaction from atleast one day ago can be
        fetched

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

        if (now_datetime().date() - start_date).days > allowed_days:
            raise NotImplementedError(
                _("Start date is more than the allowed {0} days in the past").format(
                    allowed_days
                )
            )

        with self.fints_connection:
            account = self.get_fints_account_by_iban(
                self.kefiya_login.account_iban)
            return json.loads(
                json.dumps(
                    self.fints_connection.get_transactions(
                        account,
                        start_date,
                        end_date
                    ),
                    cls=mt940.JSONEncoder
                )
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

    def submit_sepa_transfer(self, pain_xml):
        """Submit a SEPA credit transfer (pain.001 XML) via FinTS.

        Requires a TAN: if the bank asks for one, the request is persisted and
        the UI is prompted (the user later calls send_transfer_tan). Never sends
        money without the user's TAN.

        :param pain_xml: pain.001 credit-transfer message
        :return: {"status": "submitted" | "tan_required", ...}
        """
        with self.fints_connection:
            account = self.get_fints_account_by_iban(
                self.kefiya_login.account_iban)
            response = self.fints_connection.sepa_transfer(account, pain_xml)
            if self.is_tan_required_and_requested(response):
                return {
                    "status": "tan_required",
                    "docname": self.kefiya_login.name,
                }
            # The bank accepted the order without asking for a TAN. For a credit
            # transfer this is unexpected under PSD2 -- record it loudly so a
            # transfer that moved money without strong auth is never silent.
            frappe.log_error(
                title="Kefiya SEPA transfer completed without TAN challenge",
                message="login={0}: the bank did not request a TAN".format(
                    self.kefiya_login.name
                ),
            )
            return {"status": "submitted"}

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

            # normalise: some banks (e.g. Atruvia/VR) return a list of
            # statements (list of lists); others (Finanz Informatik) return a
            # flat list of transaction dicts. Flatten one nesting level so the
            # downstream code (start/end date, old_kefiya_import) always sees a
            # flat list of transactions.
            flat_transactions = []
            for statement in tansactions:
                if isinstance(statement, list):
                    flat_transactions.extend(statement)
                else:
                    flat_transactions.append(statement)
            tansactions = flat_transactions
            
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

                curr_doc.start_date = tansactions[0]["date"]
                curr_doc.end_date = tansactions[-1]["date"]

                importer = ImportBankTransaction(self.kefiya_login, self.interactive)
                # Banks differ in transaction format: Atruvia/VR deliver CAMT
                # (ISO 20022, recognisable by the CreditDebitIndicator field),
                # Finanz Informatik (Sparkasse, LBBW) deliver MT940. Route each
                # set to the matching parser.
                if tansactions and isinstance(tansactions[0], dict) and "CreditDebitIndicator" in tansactions[0]:
                    importer.kefiya_import(tansactions)
                else:
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

    def request_tan_mechanism(self, possible_tan_modes=None, possible_tan_mediums=None):
        """Request tan mechanism from user.

        :param possible_tan_modes: List of tan mechanisms
        :type mechanisms: list
        :return: None
        """
        self.request_tan_prompt(possible_tan_modes, possible_tan_mediums)

    def request_tan(self, possible_tan_modes=None, possible_tan_mediums=None):
        """Request a TAN from user

        :return: None
        """
        self.request_tan_prompt(possible_tan_modes, possible_tan_mediums, request_tan=True)

    def request_mfa_confirmation(self, possible_tan_modes=None, possible_tan_mediums=None):
        """Request a solved MFA challenge from the user

        :return: None
        """
        self.request_tan_prompt(possible_tan_modes, possible_tan_mediums, request_mfa_confirmation=True)

    def request_tan_prompt(self, possible_tan_modes, possible_tan_mediums=None, *, request_tan=False, request_mfa_confirmation=False):
        """Request tan mechanism from user.

        :param possible_tan_modes: List of tan mechanisms
        :type mechanisms: list
        :return: None
        """
        if self.enabled:
            params = {
                        "docname": self.docname,
                        "possible_tan_modes": possible_tan_modes,
                        "possible_tan_mediums": possible_tan_mediums,
                    }

            if request_tan:
                params["tan_required"] = True

            elif request_mfa_confirmation:
                params["mfa_required"] = True

            frappe.publish_realtime("fints_tan_interaction_required", params, user=frappe.session.user)
