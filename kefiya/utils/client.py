# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt
from __future__ import unicode_literals

import frappe


def _use_tan_authentication() -> bool:
    """Helper: check Kefiya Settings for TAN toggle."""
    return bool(
        frappe.db.get_single_value("Kefiya Settings", "enable_tan_authentication")
    )


def _get_fints_controller():
    """Return the appropriate FinTSController class depending on TAN toggle."""
    if _use_tan_authentication():
        from kefiya.utils.fints_controller import FinTSController
    else:
        from kefiya.utils.fints_controller_legacy import FinTSController
    return FinTSController


@frappe.whitelist()
def import_fints_transactions(kefiya_import, kefiya_login, user_scope):
    """Create payment entries by FinTS transactions.

    :param kefiya_import: kefiya_import doc name
    :param kefiya_login: kefiya_login doc name
    :param user_scope: Current open doctype page
    :type kefiya_import: str
    :type kefiya_login: str
    :type user_scopet: str
    :return: List of max 10 transactions and all new payment entries
    """
    FinTSController = _get_fints_controller()
    interactive = {"docname": user_scope, "enabled": True}

    return FinTSController(kefiya_login, interactive) \
        .import_fints_transactions(kefiya_import)


@frappe.whitelist()
def import_fints_holdings(kefiya_login, user_scope):
    """Fetch securities holdings (Depot) for a login and store a snapshot.

    :param kefiya_login: kefiya_login doc name
    :param user_scope: current open doctype page (for progress/TAN UI)
    :return: dict with created/updated counts
    """
    from kefiya.utils.securities import refresh_holdings

    FinTSController = _get_fints_controller()
    interactive = {"docname": user_scope, "enabled": True}

    controller = FinTSController(kefiya_login, interactive)
    holdings = controller.get_fints_holdings()
    return refresh_holdings(kefiya_login, holdings)


@frappe.whitelist()
def submit_payment_request_via_fints(payment_request_name, user_scope, confirmed=0):
    """Prepare a SEPA credit transfer (pain.001) for an Outward Payment Request
    and submit it directly via FinTS -- no manual file upload.

    Money movement stays human-in-the-loop: the caller must pass
    ``confirmed=1`` (set only after an explicit user confirmation dialog), and
    the bank's TAN is supplied by the user (the UI prompts via the realtime TAN
    handler, then calls ``send_transfer_tan``). This never sends money on its
    own.

    :return: {"status": "submitted" | "tan_required" | "error", ...}
    """
    from frappe.utils import cint

    # Hard gate: never reach sepa_transfer without explicit confirmation.
    if not cint(confirmed):
        return {"status": "error", "message": _(
            "Transfer not confirmed. Money is only sent after explicit"
            " confirmation."
        )}

    from kefiya.events.hammer_script.payment_request_on_submit import (
        _build_sepa_xml,
    )

    pr = frappe.get_doc("Payment Request", payment_request_name)
    if not pr.company_bank_account:
        return {"status": "error",
                "message": _("Payment Request has no company bank account.")}

    # B2: guard against double submission (double click / retry).
    lock_key = "kefiya_transfer:" + payment_request_name
    if frappe.cache().get_value(lock_key):
        return {"status": "error", "message": _(
            "A transfer for this Payment Request is already in progress."
        )}

    # B7: the company bank account must map to exactly one Kefiya Login.
    logins = frappe.get_all(
        "Kefiya Login",
        filters={"bank_account": pr.company_bank_account},
        pluck="name",
    )
    if len(logins) != 1:
        return {"status": "error", "message": _(
            "Expected exactly one Kefiya Login for bank account {0}, found {1}."
        ).format(pr.company_bank_account, len(logins))}
    kefiya_login = logins[0]

    xml_content, error = _build_sepa_xml(payment_request_name)
    if error:
        return {"status": "error", "message": error}
    if not xml_content:
        return {"status": "error", "message": _("Failed to generate SEPA XML.")}

    # B8: audit every attempt to move money before contacting the bank.
    frappe.logger("kefiya").info(
        "SEPA transfer attempt: pr=%s login=%s user=%s",
        payment_request_name, kefiya_login, frappe.session.user,
    )
    frappe.cache().set_value(lock_key, frappe.session.user, expires_in_sec=600)

    # Transfers always require strong authentication (PSD2), so always use the
    # TAN-capable controller regardless of the import-mode setting.
    from kefiya.utils.fints_controller import FinTSController
    interactive = {"docname": user_scope, "enabled": True}
    try:
        controller = FinTSController(kefiya_login, interactive)
        return controller.submit_sepa_transfer(xml_content)
    except Exception:
        # release the lock on hard failure so the user can retry deliberately;
        # the audit log + bank statement remain the source of truth.
        frappe.cache().delete_value(lock_key)
        raise


@frappe.whitelist()
def send_transfer_tan(kefiya_login, tan, user_scope):
    """Continue a pending SEPA transfer by sending the user's TAN.

    Reuses the controller's stored-TAN resume mechanism (the pending transfer
    dialog was persisted when the TAN was requested). The bank response is
    reported truthfully: the transfer is only "submitted" when the bank accepted
    the TAN without asking for a further challenge, so a money movement is never
    reported as done on a guess.
    """
    from kefiya.utils.fints_controller import (
        FinTSController,
        TanInteractionRequired,
    )
    interactive = {"docname": user_scope, "enabled": True}
    try:
        # Re-instantiating with the TAN resumes the stored dialog and sends it.
        FinTSController(kefiya_login, interactive, tan=tan)
    except TanInteractionRequired:
        # The bank requested a further/renewed challenge; the UI re-prompts.
        return {"status": "tan_required", "docname": kefiya_login}
    except Exception as e:
        frappe.log_error(
            title="Kefiya SEPA transfer TAN submission failed",
            message=frappe.get_traceback(),
        )
        return {"status": "error", "message": str(e)}
    return {"status": "submitted"}


@frappe.whitelist()
def get_accounts(kefiya_login, user_scope):
    """Return FinTS accounts for a given login.

    For TAN-enabled mode we may end up triggering a TAN flow.
    For legacy mode we just use the old controller.
    """
    FinTSController = _get_fints_controller()

    interactive = {"docname": user_scope, "enabled": True}

    # New controller may raise TanInteractionRequired – we just ignore and let
    # the realtime handler + UI deal with it. Legacy controller won’t raise it.
    try:
        return {
            "accounts": FinTSController(
                kefiya_login,
                interactive
            ).get_fints_accounts()
        }
    except Exception:
        # In TAN mode this can be TanInteractionRequired – handled via socket.
        # For legacy mode we re-raise to not hide real errors.
        if not _use_tan_authentication():
            raise


@frappe.whitelist()
def is_tan_enabled():
    """Small helper for JS if needed."""
    return _use_tan_authentication()


@frappe.whitelist()
def new_bank_account(payment_doc, bankData):
    """Create new bank account.

    Create new bank account and if missing a bank entry.
    :param payment_doc: json formated payment_doc
    :param bankData: json formated bank information
    :type payment_doc: str
    :type bankData: str
    :return: Dict with status and bank details
    """
    from kefiya.utils.bank_account_controller import \
        BankAccountController
    return BankAccountController().new_bank_account(payment_doc, bankData)


@frappe.whitelist()
def get_missing_bank_accounts():
    """Get possibly missing bank accounts.

    Query payment entries for missing bank accounts.
    :return: List of payment entry data
    """
    from kefiya.utils.bank_account_controller import \
        BankAccountController
    return BankAccountController().get_missing_bank_accounts()


@frappe.whitelist()
def has_page_permission(page_name):
    """Check if user has permission for a page doctype.

    Based on frappe/desk/desk_page.py
    :param page_doc: page doctype object
    :type page_doc: page doctyp
    :return: Boolean
    """
    from kefiya.utils.bank_account_controller import \
        has_page_permission
    return has_page_permission(page_name)


@frappe.whitelist()
def add_payment_reference(payment_entry, sales_invoice):
    """Add payment reference to payment entry for sales invoice.

    Create new bank account and if missing a bank entry.
    :param payment_entry: json formated payment_doc
    :param sales_invoice: json formated bank information
    :type payment_entry: str
    :type sales_invoice: str
    :return: Payment reference name
    """
    from kefiya.utils.assign_payment_controller import \
        AssignmentController

    return AssignmentController().add_payment_reference(
        payment_entry,
        sales_invoice
    )


@frappe.whitelist()
def auto_assign_payments():
    """Query assignable payments and create payment references.

    Try to assign payments in 3 steps:
    1. payment to sale assingment
    2. multiple payments to sale assingment
    3. payment to sale assingment

    :return: List of assigned payments
    """
    from kefiya.utils.assign_payment_controller import \
        AssignmentController

    return AssignmentController().auto_assign_payments()


def create_mastercard_journal_entry_from_purchase_invoice(invoice_doc, bank_transaction_name, mastercard_account):
    """Create a journal entry/payment entry for refund/normal a purchase invoice."""
    bt = frappe.get_doc("Bank Transaction", bank_transaction_name)
    paid_amount = abs(invoice_doc.outstanding_amount)
    payment_document, payment_entry = '',''
    allocated_amount = 0

    if invoice_doc.status == "Return":
        je = frappe.new_doc("Journal Entry")
        je.posting_date = bt.date
        je.company = invoice_doc.company
        je.voucher_type = "Journal Entry"
        je.user_remark = f"Refund for Return Invoice {invoice_doc.name}"
        payment_document = "Journal Entry"

        
        je.append("accounts", {
            "account": mastercard_account,
            "debit_in_account_currency": paid_amount,
            "credit_in_account_currency": 0,
        })

        je.append("accounts", {
            "account": invoice_doc.credit_to,
            "party_type": "Supplier",
            "party": invoice_doc.supplier,
            "credit_in_account_currency": paid_amount,
            "debit_in_account_currency": 0,
            "reference_type": "Purchase Invoice",
            "reference_name": invoice_doc.name
        })

        je.insert()
        je.submit()
        payment_entry = je.name
        if bt.deposit > 0:
            allocated_amount = paid_amount
        elif bt.withdrawal > 0:
            allocated_amount= -paid_amount # NEGATIVE to increase unallocated
    else: # create payment entry for normal purchase invoice
        payment_entry_doc = frappe.call("erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry", "Purchase Invoice", invoice_doc.name)
        payment_entry_doc.payment_type = "Pay"
        payment_entry_doc.paid_amount = paid_amount
        payment_entry_doc.posting_date = bt.date
        payment_entry_doc.reference_date = bt.date
        payment_entry_doc.reference_no = 'BTN Wizard ' + bt.date.strftime("%Y-%m-%d") if hasattr(bt.date, "strftime") else 'BTN Wizard ' + str(bt.date)
        payment_entry_doc.bank_account = bt.bank_account
        payment_entry_doc.paid_from = mastercard_account

        for reference in payment_entry_doc.references:
            reference.allocated_amount = paid_amount

        payment_entry_doc.insert()
        payment_entry_doc.submit()
        payment_document = "Payment Entry"
        payment_entry = payment_entry_doc.name
        if bt.deposit > 0:
            allocated_amount = -paid_amount # NEGATIVE to increase unallocated
        elif bt.withdrawal > 0:
            allocated_amount = paid_amount

    bt.append("payment_entries", {
        "payment_document": payment_document,
        "payment_entry": payment_entry,
        "allocated_amount": allocated_amount
    })
    bt.save()

    return bt.unallocated_amount, frappe.format(bt.unallocated_amount, "Currency")


# Create Payment Entry record when reconcile button is clicked
@frappe.whitelist()
def create_payment_entry(bank_transaction_name, invoice_name, match_against):
    """Create payment entry document from sales or purchase invoice doctype.
    """
    if match_against == "Mastercard":
        invoice_doc = frappe.get_doc("Purchase Invoice", invoice_name)
        bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
        bank_account = ''
        if bank_transaction.bank_account:
            bank_account = frappe.db.get_values(
                "Bank Account", bank_transaction.bank_account, ["account", "company"], as_dict=True
            )[0]
        if bank_account:
            if bank_account.account:
                return create_mastercard_journal_entry_from_purchase_invoice(invoice_doc, bank_transaction_name, bank_account.account)
            else:
                frappe.throw("Bank Account {} has no account set.".format(bank_transaction.bank_account))
    else:
        bank_transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
        invoice_doc = frappe.get_doc(match_against, invoice_name)

        unallocated_amount = bank_transaction.unallocated_amount
        outstanding_amount = invoice_doc.outstanding_amount
        diff = frappe.format(abs(unallocated_amount - outstanding_amount), "Currency")
        paid_amount = outstanding_amount

        if unallocated_amount <= outstanding_amount:
            paid_amount = unallocated_amount

        payment_entry = frappe.call("erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry", match_against, invoice_name)

        payment_entry.paid_amount = paid_amount
        payment_entry.posting_date = bank_transaction.date
        payment_entry.reference_date = bank_transaction.date
        payment_entry.reference_no = 'BTN Wizard ' + str(bank_transaction.date)
        payment_entry.payment_type = "Receive" if bank_transaction.deposit > 0.0 else "Pay"
        account_from_to = "paid_to" if bank_transaction.deposit > 0.0 else "paid_from"

        bank_account = ''
        if bank_transaction.bank_account:
            bank_account = frappe.db.get_values(
                "Bank Account", bank_transaction.bank_account, ["account", "company"], as_dict=True
            )[0]

        if bank_account and bank_account.account:
            (gl_account, company) = (bank_account.account, bank_account.company)

            payment_entry.bank_account = bank_transaction.bank_account

            if account_from_to == "paid_to":
                payment_entry.paid_to = gl_account
            else:
                payment_entry.paid_from = gl_account

        for reference in payment_entry.references:
            reference.allocated_amount = paid_amount

        payment_entry.insert()
        payment_entry.submit()

        return paid_amount, payment_entry.name, unallocated_amount, outstanding_amount, diff


def _get_priority_parties(assign_against):
	"""Return set of party names (customer/supplier) that have at least one
	unreconciled Bank Transaction, so we can show those invoices first in the wizard.
	"""
	if assign_against == "Sales Invoice":
		bt_filters = [
			["docstatus", "=", 1],
			["party_type", "=", "Customer"],
			["deposit", ">", 0],
			["unallocated_amount", "!=", 0],
		]
	elif assign_against == "Purchase Invoice":
		bt_filters = [
			["docstatus", "=", 1],
			["party_type", "=", "Supplier"],
			["withdrawal", ">", 0],
			["unallocated_amount", "!=", 0],
		]
	elif assign_against == "Mastercard":
		bt_filters = [
			["docstatus", "=", 1],
			["party_type", "=", "Supplier"],
			["unallocated_amount", "!=", 0],
		]
	else:
		return set()
	parties = frappe.get_all(
		"Bank Transaction",
		filters=bt_filters,
		pluck="party",
		distinct=True,
		ignore_permissions=True,
	)
	return set(p for p in parties if p)


# Params that must not be passed to reportview execute (request-only / reserved)
_REPORTVIEW_DISALLOWED = frozenset(
	("cmd", "data", "ignore_permissions", "view", "user", "csrf_token", "join", "assign_against")
)


def _reportview_kwargs(kwargs):
	"""Return kwargs suitable for reportview.execute (strip request-only params)."""
	return {k: v for k, v in kwargs.items() if k not in _REPORTVIEW_DISALLOWED}


@frappe.whitelist()
def get_bank_transaction_wizard_list(doctype, fields, filters, order_by, start, page_length, assign_against=None, **kwargs):
	"""Return list data for Bank Transaction Wizard with invoices that have
	matching bank transactions prioritised (first in the list), so the first
	page shows reconcilable items even when pagination is 20 or 100.
	Returns the same compressed format as frappe.desk.reportview.get.
	"""
	from frappe.desk.reportview import compress, execute

	if isinstance(filters, str):
		filters = frappe.parse_json(filters) or []
	if isinstance(fields, str):
		fields = frappe.parse_json(fields) if fields != "*" else ["*"]

	start = int(start or 0)
	page_length = int(page_length or 20)
	assign_against = assign_against or "Sales Invoice"
	rv_kw = _reportview_kwargs(kwargs)

	# Journal Entry view lists Bank Transactions directly; no prioritisation needed
	if assign_against == "Journal Entry":
		result = execute(
			doctype=doctype,
			fields=fields,
			filters=filters,
			order_by=order_by,
			start=start,
			page_length=page_length,
			**rv_kw,
		)
		return compress(result, {"doctype": doctype, **rv_kw})

	# For Sales Invoice / Purchase Invoice / Mastercard, put invoices with matching BT first
	party_field = "customer" if assign_against == "Sales Invoice" else "supplier"
	priority_parties = _get_priority_parties(assign_against)

	if not priority_parties:
		result = execute(
			doctype=doctype,
			fields=fields,
			filters=filters,
			order_by=order_by,
			start=start,
			page_length=page_length,
			**rv_kw,
		)
		return compress(result, {"doctype": doctype, **rv_kw})

	# Build priority and non-priority filters (same base filters + party in/not in)
	priority_filters = list(filters or []) + [[doctype, party_field, "in", list(priority_parties)]]
	non_priority_filters = list(filters or []) + [[doctype, party_field, "not in", list(priority_parties)]]

	# Count priority rows: pass only allowed args so "fields" is always a list of strings
	_count_args = {
		"doctype": doctype,
		"fields": [f"`tab{doctype}`.name"],
		"filters": priority_filters,
		"order_by": None,
		"start": 0,
		"page_length": 0,
		"run": 0,
	}
	partial_query = execute(**_count_args)
	total_priority = int(
		frappe.db.sql(f"""select count(*) from ( {partial_query} ) _p""")[0][0]
	)

	if start >= total_priority:
		# Page is entirely in non-priority range
		result = execute(
			doctype=doctype,
			fields=fields,
			filters=non_priority_filters,
			order_by=order_by,
			start=start - total_priority,
			page_length=page_length,
			**rv_kw,
		)
	elif start + page_length <= total_priority:
		# Page is entirely in priority range
		result = execute(
			doctype=doctype,
			fields=fields,
			filters=priority_filters,
			order_by=order_by,
			start=start,
			page_length=page_length,
			**rv_kw,
		)
	else:
		# Page spans priority and non-priority
		priority_len = total_priority - start
		priority_result = execute(
			doctype=doctype,
			fields=fields,
			filters=priority_filters,
			order_by=order_by,
			start=start,
			page_length=priority_len,
			**rv_kw,
		)
		non_priority_result = execute(
			doctype=doctype,
			fields=fields,
			filters=non_priority_filters,
			order_by=order_by,
			start=0,
			page_length=page_length - priority_len,
			**rv_kw,
		)
		result = list(priority_result) + list(non_priority_result)

	return compress(result, {"doctype": doctype, **rv_kw})


@frappe.whitelist()
def change_match_against(selected_match):
    kefiya_setting = frappe.get_single("Kefiya Settings")
    kefiya_setting.assign_against = selected_match
    kefiya_setting.save()


@frappe.whitelist()
def resolve_tan_interaction(fints_login: str, values: str | dict):
    """
    When a user was requested to perform a 2FA, this method is called as a callback
    to resolve the interaction. If TAN is disabled, this is effectively a no-op.
    """
    if not _use_tan_authentication():
        # Old banks / legacy mode: nothing to resolve
        return

    from kefiya.utils.fints_controller import FinTSController, TanInteractionRequired

    if isinstance(values, str):
        values = frappe.parse_json(values)

    tan_mode = None

    if values.get("possible_tan_modes") and values.get("tan_mode") and isinstance(values["possible_tan_modes"], list) and isinstance(values["tan_mode"], str):
        tan_mode = values["tan_mode"]

    tan_medium = values.get("tan_medium") if tan_mode else None

    try:
        if values.get("mfa_confirmation"):
            # for tan generators, the TAN is permitted here (may also be empty for Mobile TAN 2.0)
            FinTSController(fints_login, {"docname": fints_login, "enabled": True}, tan_mode=tan_mode, tan_medium=tan_medium, tan=values.get("tan"))
        else:
            # get index of tan_mode in possible_tan_modes
            FinTSController(fints_login, {"docname": fints_login, "enabled": True}, tan_mode=tan_mode, tan_medium=tan_medium)
    except TanInteractionRequired:
        # will have triggered user interaction via socket
        pass
