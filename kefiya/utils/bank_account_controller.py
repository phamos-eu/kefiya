# -*- coding: utf-8 -*-
# Copyright (c) 2019, jHetzer and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import os
import json
import frappe
from frappe import _
from frappe.utils.csvutils import getlink


class BankAccountController:
    def __init__(self):
        self.cur_dir = os.path.dirname(__file__)

    @staticmethod
    def new_bank_account(payment_doc, bankData):
        """Create new bank account.

        Create new bank account and if missing a bank entry.
        :param payment_doc: json formated payment_doc
        :param bankData: json formated bank information
        :type payment_doc: str
        :type bankData: str
        :return: Dict with status and bank details
        """
        
        try:
            ignore_permissions = False
            payment_doc = json.loads(payment_doc)

            if has_page_permission('bank_account_wizard'):
                ignore_permissions = True
            else:
                raise PermissionError(_(
                    'User {0} does not have access to this method'
                ).format(frappe.bold(frappe.session.user)))

            bankData = json.loads(bankData)
            bankName = bankData.get('name')
            bankCode = bankData.get('bankCode')
            swiftNumber = bankData.get('bic')

            # Check if Bank document exists based on bank name
            bank_name_exists = frappe.db.exists('Bank', {'bank_name': bankName})

            # Check if Bank document exists based on swift number
            swift_number_exists = frappe.db.exists('Bank', {'swift_number': swiftNumber})

            # print("check both", bank_name_exists, swift_number_exists)

            if not bank_name_exists and not swift_number_exists:
                # print('both not exist', bank_name_exists, swift_number_exists)

                # Bank document does not exist, insert a new one
                new_bank_doc = frappe.get_doc({
                    'doctype': 'Bank',
                    'bank_name': bankName,
                    'swift_number': swiftNumber
                })
                new_bank_doc.insert(ignore_permissions=ignore_permissions)
                # frappe.msgprint(_("new_bank_doc: {0}").format(new_bank_doc))
            else:
                # print('one of them exist', bank_name_exists, swift_number_exists) 
                existing_bank_doc = ''
                if bank_name_exists:
                    existing_bank_doc = frappe.get_doc('Bank', {'bank_name': bankName})
                else:
                    existing_bank_doc = frappe.get_doc('Bank', {'swift_number': swiftNumber})
                
                bankName = existing_bank_doc.name 
                swiftNumber = existing_bank_doc.swift_number

            # print('----------')
            # print('bank name', bankName)
            # print('bank code', bankCode)
            # print('swift number', swiftNumber)
            # gl_account = None
            # if payment_doc.get('payment_type') == "Pay":
            #     gl_account = payment_doc.get('paid_from')
            # else:
            #     gl_account = payment_doc.get('paid_to')

            bankAccount = ({
                'doctype': 'Bank Account',
                'account_name': payment_doc.get('bank_party_name'),
                # 'account': gl_account,
                'bank': bankName,
                'party_type': payment_doc.get('party_type'),
                'party': payment_doc.get('party'),
                'iban': payment_doc.get('bank_party_iban'),
                'bank_account_no': bankCode,
                'branch_code': swiftNumber,
                'is_company_account': False,
                'is_default': False,
            })
            bankAccountName = ''.join([
                payment_doc.get('bank_party_name'), " - ",
                bankName
            ])
            if not frappe.db.exists('Bank Account', bankAccountName):
                newBankAccount = frappe.get_doc(
                    bankAccount,
                    ignore_permissions=ignore_permissions
                )
                newBankAccount.insert(ignore_permissions)
                
                # get all bank transactions which have the same IBAN
                IBANValue = payment_doc.get('bank_party_iban')
                bank_transactions = frappe.get_all('Bank Transaction', filters={'bank_party_iban': IBANValue, 'party': ''})

                
                for bank_transaction in bank_transactions:
                    
                    # insert party and party_type on bank_transaction document
                    bank_transaction_doc = frappe.get_doc("Bank Transaction", bank_transaction.name)
                    bank_transaction_doc.party = payment_doc.get('party')
                    bank_transaction_doc.party_type = payment_doc.get('party_type')
                    bank_transaction_doc.save()

                bank_account_name = newBankAccount.name
                bank_account_link = f'<a href="bank-account/{bank_account_name}">{bank_account_name}</a>'

                frappe.msgprint(_("Successfully created Bank Account: {0}").format(bank_account_link))
                return {"bankAccount": newBankAccount, "status": True}
            else:
                frappe.msgprint(_("Bank account already exists"))
                return {
                    "bankAccount": frappe.get_doc(
                        'Bank Account',
                        bankAccountName),
                    "status": True
                }
            
        except Exception as e:
            frappe.throw(_(
                "Could not create bank account with error: {0}"
            ).format(e))

    def get_missing_bank_accounts(self):
        """Get possibly missing bank accounts.

        Query payment entries for missing bank accounts.
        :return: List of payment entry data
        """
        file_path = os.path.join(self.cur_dir, './sql/bank_wizard.sql')
        filehandle = open(file_path)
        sqlQuery = filehandle.read()
        filehandle.close()
        return frappe.db.sql(sqlQuery, as_dict=True)


def has_page_permission(page_name):
    """Check if user has permission for a page doctype.

    Based on frappe/desk/desk_page.py
    :param page_doc: page doctype object
    :type page_doc: page doctyp
    :return: Boolean
    """
    if (
        frappe.session.user == 'Administrator'
        or 'System Manager' in frappe.get_roles()
    ):
        return True

    page_doc = frappe.get_doc('Page', page_name)
    page_roles = [d.role for d in page_doc.get('roles')]
    if page_roles:
        if frappe.session.user == 'Guest' and 'Guest' not in page_roles:
            return False
        elif set(page_roles).intersection(set(frappe.get_roles())):
            # check if roles match
            return True
    else:
        return False


def validate_unique_iban(doc, method):
    """Bank Account IBAN should be unique.

    An account without an IBAN is not a duplicate of every other account
    without one. The filter used to key on the document's own IBAN with no
    guard, so an empty one matched `iban IS NULL` -- and on a book where
    most accounts have no IBAN at all (supplier accounts, tenant accounts,
    the hand-kept loan records) that made every one of them unsavable,
    naming an unrelated account as the culprit.

    IBANs are compared without spaces and in upper case. Banks and people
    write the same IBAN both ways, and two spellings of one IBAN are one
    IBAN, not two.
    """
    iban = (doc.iban or "").replace(" ", "").upper()
    if not iban:
        return

    doctype = "Bank Account"
    for name, other in frappe.get_all(
            doctype,
            filters={"name": ["!=", doc.name], "iban": ["is", "set"]},
            fields=["name", "iban"],
            as_list=True):
        if (other or "").replace(" ", "").upper() == iban:
            frappe.throw(_("IBAN already exists in bank account: {0}").format(
                getlink(doctype, name)
            ))
