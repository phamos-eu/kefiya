# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Outgoing SEPA credit transfer with free recipient entry.

One document carries one or more payments. A single row is sent as an
individual transfer (HKCCS, or HKIPZ when instant), several rows go out as one
collective order (HKCCM) that the bank authorises with a single TAN.

Recipients are typed in rather than taken from a Payment Request, so the
document itself has to carry the safeguards that the invoice workflow would
otherwise provide:

* the IBAN is checksum-verified, because a typo silently pays a stranger;
* money only leaves after submit, and submit rights are separate from create
  rights, so the person entering a transfer is not the person releasing it;
* sending is a distinct, explicitly confirmed step -- submit alone moves
  nothing.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, now_datetime

from kefiya.utils import own_transfer


def normalize_iban(value):
    """Strip formatting and upper-case an IBAN."""
    return (value or "").replace(" ", "").replace("-", "").upper()


def is_valid_iban(value):
    """Validate an IBAN's ISO 7064 mod-97 checksum.

    With free recipient entry there is no invoice to check the account against,
    so the checksum is the only automatic defence against a mistyped IBAN
    reaching the bank.
    """
    iban = normalize_iban(value)
    if len(iban) < 15 or len(iban) > 34:
        return False
    if not iban[:2].isalpha() or not iban[2:4].isdigit():
        return False
    if not iban.isalnum():
        return False

    rearranged = iban[4:] + iban[:4]
    digits = ""
    for char in rearranged:
        if char.isdigit():
            digits += char
        elif char.isalpha():
            digits += str(ord(char) - 55)
        else:
            return False
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


class KefiyaTransfer(Document):
    def validate(self):
        if not self.items:
            frappe.throw(_("Add at least one payment."))

        total = 0
        for row in self.items:
            # A row that names one of our own accounts takes its IBAN from
            # there. Filled on the server too, not only in the form: a row can
            # arrive from an import or another script that never opened one.
            own_transfer.fill_from_own_account(row)

            row.recipient_iban = normalize_iban(row.recipient_iban)
            if not is_valid_iban(row.recipient_iban):
                frappe.throw(_(
                    "Row {0}: {1} is not a valid IBAN (checksum failed)."
                ).format(row.idx, row.recipient_iban or "-"))

            if row.recipient_bic:
                row.recipient_bic = (row.recipient_bic or "").replace(
                    " ", "").upper()

            if flt(row.amount) <= 0:
                frappe.throw(_(
                    "Row {0}: amount must be greater than zero."
                ).format(row.idx))

            # The SEPA purpose field is capped at 140 characters; truncate here
            # rather than letting the bank reject the whole order.
            if row.purpose:
                row.purpose = row.purpose[:140]

            total += flt(row.amount)

        self.total_amount = total
        self.payment_count = len(self.items)

        # Paying the account the money is drawn from. The bank refuses it as
        # well -- but only after the order went out and a TAN was spent on it.
        own_transfer.refuse_paying_yourself(self)

        if not self.execution_date:
            self.execution_date = now_datetime().date()

        if getdate(self.execution_date) > now_datetime().date():
            # An instant payment and a date are only a contradiction when the
            # BANK holds the date. Then the order goes out now, carrying a
            # future execution date, and no bank offers HKIPZ that way.
            #
            # Held here, they are not a contradiction at all: the order waits
            # in the outbox and is sent ON the day, and at that moment it is an
            # ordinary immediate transfer that may perfectly well go out as an
            # instant one. That is the combination this refusal used to catch
            # along with the impossible one, which is why a dated transfer
            # could never be an instant payment even where it plainly could.
            if cint(self.instant_payment) and not cint(self.manage_due_date):
                frappe.throw(_(
                    "An instant payment is executed within seconds, so the"
                    " bank cannot hold it until {0}. Either let the date be"
                    " kept here -- the order is then sent as an instant"
                    " payment on the day -- or hand the date to the bank as an"
                    " ordinary transfer."
                ).format(frappe.format(self.execution_date, "Date")))
        elif not cint(self.manage_due_date):
            # Nothing to hand over: a bank cannot file an order for today or
            # for a day gone by. Silently correcting this is better than an
            # error, because the outcome the user wants -- pay it -- is the
            # same either way.
            self.manage_due_date = 1

        self.company = self.company_of_the_paying_account()
        self.drop_instant_where_the_bank_refuses_it()
        self.check_the_payees()

    def check_the_payees(self):
        """Record what our own history says about every recipient.

        Written onto the order rather than shown and forgotten, because of who
        reads it and when. The person entering the order has the invoice in
        front of them; the person sending it does not. By the time the second
        one looks, the check has to be a recorded fact and not a question that
        gets asked again.

        The bank's own Verification of Payee still happens where it must -- at
        the bank, on submission. This is the other question, the one that can
        be asked at entry: have we paid this IBAN before, and did it belong to
        this name?

        Never blocks. A first payment to a new payee is an ordinary thing, and
        an order that cannot be saved until the software is satisfied is an
        order that gets entered somewhere else.
        """
        from kefiya.utils import payee_check

        for row in self.items:
            if not row.recipient_iban:
                continue
            try:
                answer = payee_check.check(row.recipient_name,
                                           row.recipient_iban)
            except Exception:
                # A check that fails must not stop an order. It leaves no
                # verdict, which reads as "not checked" rather than "fine".
                frappe.log_error(
                    title="Kefiya: payee check failed",
                    message=frappe.get_traceback())
                continue

            row.payee_check = answer["verdict"]
            row.payee_check_detail = _payee_detail(answer)

    def drop_instant_where_the_bank_refuses_it(self):
        """Turn the instant flag off where the account cannot do it at all.

        It is on by default now, which is what makes this necessary: an account
        whose bank does not offer HKIPZ would otherwise carry a flag that gets
        the order refused at send time, on every order, for as long as nobody
        notices what the default did.

        Only an EXPLICIT refusal counts. An account nobody has fetched knows
        nothing about itself, and treating that silence as a refusal would take
        instant payments away from every account before its first fetch.
        """
        if not cint(self.instant_payment) or not self.kefiya_login:
            return

        from kefiya.utils import account_capabilities as capabilities

        bank_account = frappe.db.get_value(
            "Kefiya Login", self.kefiya_login, "bank_account")
        wanted = capabilities.required_capability(
            payment_count=len(self.items) or 1, instant=True)
        if not capabilities.refuses(bank_account, wanted):
            return

        self.instant_payment = 0
        frappe.msgprint(
            capabilities.refusal_message(bank_account, wanted),
            title=_("Sent as an ordinary transfer"), indicator="orange")

    def company_of_the_paying_account(self):
        """Whose money this is. Never a choice -- a fact about the account.

        This used to be filled only when the field was still empty, so a
        company already sitting there survived. A Company link picks up the
        user's session default, so it is rarely empty: an order drawn on the
        Brilu-Stiftung's account went out carrying "axessio Hausverwaltung
        GmbH", and it was a colleague who noticed, not the software.

        That is not a label. build_pain001_for() takes the ordering party's
        NAME from this field and the ordering party's IBAN from the account --
        so a mismatch sends an order that names one company and debits
        another. Banks reject that, and the ones that do not are worse.

        The Bank Account is the authority: it is the thing that holds the
        money. The login's own company is the fallback for an account record
        that names none.
        """
        if not self.kefiya_login:
            return self.company

        login = frappe.db.get_value(
            "Kefiya Login", self.kefiya_login, ["company", "bank_account"],
            as_dict=True) or {}

        company = None
        if login.get("bank_account"):
            company = frappe.db.get_value(
                "Bank Account", login["bank_account"], "company")
        company = company or login.get("company")

        if not company:
            frappe.throw(_(
                "The paying account {0} does not name a company, so there is"
                " nothing to put on the order as the ordering party. Set the"
                " company on the Bank Account first."
            ).format(self.kefiya_login))

        return company

    def before_submit(self):
        # Submitting approves the transfer; it does not send it. Sending is a
        # separate, explicitly confirmed action so an approval can never move
        # money as a side effect.
        self.status = "On Hold" if self.on_hold else "Approved"
        for row in self.items:
            if not row.end_to_end_id:
                row.end_to_end_id = "{0}-{1}".format(self.name, row.idx)[:35]

    def on_update_after_submit(self):
        """The few things an approved order may still be told.

        execution_date is one of them, and it had to become one. An order
        approved for a day that has since passed could not be sent -- the bank
        cannot execute in the past, and requested_execution_date() refuses it
        with "change the date, or set it to be held here". Neither was
        possible: the field was locked by the submit. The order sat there
        unsendable and uneditable, and the only way out was to cancel and
        re-enter it.

        What stays locked is what the approval was about: the amounts, the
        recipients, the paying account. A date says WHEN the approved payment
        happens, not what it is.
        """
        # "Scheduled at Bank" belongs here as much as "Sent": the bank is
        # holding that order until its date, so the date is no longer ours to
        # move. Changing it here would only make this document disagree with
        # what the bank will actually do.
        if self.status in ("Sent", "Scheduled at Bank"):
            frappe.throw(_(
                "This transfer has gone to the bank. Its date cannot be"
                " changed here -- recall it with your bank if it must not be"
                " executed as it stands."))

        # A date that is not in the future is not something a bank can be
        # asked to hold -- it is ours to hold. The same silent correction
        # validate() makes on a draft, made here for the same reason: the
        # outcome the user wants is "pay it", and an error at this point would
        # leave the order exactly as stuck as it was.
        if self.execution_date and not cint(self.manage_due_date) \
                and getdate(self.execution_date) <= now_datetime().date():
            self.manage_due_date = 1
            self.db_set("manage_due_date", 1, update_modified=False)

    @frappe.whitelist()
    def set_hold(self, on_hold):
        """Hold an approved order back, or release it again.

        Holding changes when an order is sent, never what it says -- amounts
        and recipients stay locked by the submit. That is why this is allowed
        after approval while everything else on the document is not.
        """
        # A whitelisted document method is callable by anyone who may read the
        # document. Releasing a held-back order makes it eligible for the next
        # collective send, so it needs the same right as sending itself.
        frappe.has_permission(
            "Kefiya Transfer", ptype="submit", doc=self, throw=True)

        if self.docstatus != 1:
            frappe.throw(_("Only approved transfers can be held back."))
        if self.status == "Sent":
            frappe.throw(_("This transfer was already sent."))

        on_hold = 1 if cint(on_hold) else 0
        self.db_set("on_hold", on_hold)
        self.db_set("status", "On Hold" if on_hold else "Approved")
        return {"status": self.status, "on_hold": on_hold}

    def on_cancel(self):
        if self.status == "Sent":
            frappe.throw(_(
                "This transfer was already sent to the bank and cannot be"
                " cancelled here. Recall it with your bank if needed."
            ))
        self.status = "Draft"

    def build_pain001(self):
        """Render this document as a pain.001.001.03 credit-transfer message.

        Returns (xml, control_sum, count).
        """
        return build_pain001_for([self])


def _payee_detail(answer):
    """The verdict in the words the reader needs, with the evidence in it."""
    from kefiya.utils import payee_check

    if answer["verdict"] == payee_check.VERDICT_KNOWN:
        return _("Paid before under this name.")
    if answer["verdict"] == payee_check.VERDICT_NAME_DIFFERS:
        return _("This IBAN was paid before, but under: {0}").format(
            ", ".join(answer["known_as"]) or _("another name"))
    if answer["verdict"] == payee_check.VERDICT_OTHER_IBAN:
        return _(
            "We have paid this payee before, always to a different IBAN: {0}."
            " Check the invoice against an earlier one before releasing this."
        ).format(", ".join(answer["other_ibans"]))
    return _("First payment to this IBAN, and this payee is not in our"
             " history. Worth a second pair of eyes.")


def requested_execution_date(doc):
    """The day the BANK is asked to execute -- which is not doc.execution_date.

    Two orders carry a date and they mean opposite things:

        manage_due_date = 0   the bank holds the order until that day. The
                              date belongs in the message; it IS the order.
        manage_due_date = 1   WE hold it. It sits in the outbox and is offered
                              for sending on the day, and the moment it is
                              sent it is an ordinary immediate transfer. The
                              date is our bookkeeping and has no business in
                              the message.

    The old line sent doc.execution_date either way, falling back to today
    only when there was none. So an order entered for the 16th and sent on the
    25th went to the bank with ReqdExctnDt = 16.08.2026 -- nine days in the
    past. That is not a transfer the bank can execute, and it did not: no TAN
    was asked for, nothing was debited, and the app wrote "Sent".

    A past date is refused rather than quietly moved forward, but only where
    it is the order itself. Silently turning "execute on the 16th" into
    "execute today" is a different payment than the one somebody approved.
    """
    from frappe.utils import cint

    today = now_datetime().date()
    if cint(getattr(doc, "manage_due_date", 0)) or not doc.execution_date:
        return today

    wanted = getdate(doc.execution_date)
    if wanted < today:
        frappe.throw(_(
            "{0} is dated {1}, which has passed, and the bank is meant to"
            " hold it until then. A bank cannot execute an order in the past."
            " Change the date, or set it to be held here and sent on the day."
        ).format(doc.name, frappe.utils.formatdate(doc.execution_date)),
            title=_("Execution date has passed"))
    return wanted


def build_pain001_for(docs):
    """Render one or more Kefiya Transfers as a single pain.001 message.

    Several documents collapse into one collective order (HKCCM) that the bank
    authorises with a single TAN -- the point of the outbox. They must all be
    paid from the same account, since one message carries exactly one debtor.

    Returns (xml, control_sum, count). The control sum is what the bank checks
    a collective order against, so it is summed from the very rows that go into
    the message rather than from stored totals that could have drifted.
    """
    from sepaxml import SepaTransfer

    if not docs:
        frappe.throw(_("No transfers to send."))

    logins = {doc.kefiya_login for doc in docs}
    if len(logins) > 1:
        frappe.throw(_(
            "All selected transfers must be paid from the same account."
            " Found: {0}"
        ).format(", ".join(sorted(logins))))

    rows = []
    for doc in docs:
        for row in doc.items:
            rows.append((doc, row))
    if not rows:
        frappe.throw(_("The selected transfers contain no payments."))

    first = docs[0]
    login = frappe.get_doc("Kefiya Login", first.kefiya_login)
    bank_account = login.bank_account
    if not bank_account:
        frappe.throw(_("Kefiya Login {0} has no bank account.").format(
            first.kefiya_login))

    company_bank = frappe.get_doc("Bank Account", bank_account)
    debtor_iban = normalize_iban(company_bank.iban)
    if not debtor_iban:
        frappe.throw(_("Bank account {0} has no IBAN.").format(bank_account))

    company = first.company or login.company
    company_name = frappe.get_cached_value(
        "Company", company, "company_name") or company or ""

    config = {
        "name": (company_name or "")[:70],
        "currency": "EUR",
        "IBAN": debtor_iban,
        # A collective order is a batch; a single payment is not.
        "batch": len(rows) > 1,
    }
    if company_bank.branch_code:
        config["BIC"] = (company_bank.branch_code or "").replace(
            " ", "").upper()

    # clean=False keeps the text exactly as entered, so the XSD validation
    # below is what catches out-of-spec characters instead of silently
    # rewriting a recipient name.
    sepa = SepaTransfer(config, schema="pain.001.001.03", clean=False)

    control_sum = 0
    for doc, row in rows:
        amount_cents = int(round(flt(row.amount) * 100))
        if amount_cents <= 0:
            frappe.throw(_(
                "{0} row {1}: amount must be greater than zero."
            ).format(doc.name, row.idx))
        payment = {
            "name": (row.recipient_name or "")[:70],
            "IBAN": normalize_iban(row.recipient_iban),
            "amount": amount_cents,
            "description": (row.purpose or doc.name)[:140],
            "execution_date": requested_execution_date(doc),
            "endtoend_id": (row.end_to_end_id
                            or "{0}-{1}".format(doc.name, row.idx))[:35],
        }
        if row.recipient_bic:
            payment["BIC"] = row.recipient_bic
        sepa.add_payment(payment)
        control_sum += amount_cents

    try:
        xml = sepa.export(validate=True)
    except Exception as exc:
        frappe.throw(_(
            "Generated SEPA XML failed schema validation: {0}"
        ).format(exc))

    if isinstance(xml, bytes):
        xml = xml.decode("utf-8")
    return xml, control_sum / 100.0, len(rows)
