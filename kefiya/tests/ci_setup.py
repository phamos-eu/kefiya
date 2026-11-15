# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt
"""Test data for a site that has nothing on it yet, as the CI builds one.

The tests expect two German companies in EUR, "_Test GmbH" and "My Company",
each with a bank ledger account, and one company Bank Account. A developer's
test site has them already. A fresh site gets them from here, once, before
the tests run:

    bench --site test_site execute kefiya.tests.ci_setup.execute

Running it again changes nothing.

The company Bank Account belongs to "My Company". ERPNext lets a ledger
account back one Bank Account only, and the tests make Bank Accounts of their
own on the ledger account of "_Test GmbH", so that one has to stay free.
"""

import frappe

COMPANY = "_Test GmbH"
OTHER_COMPANY = "My Company"
COMPANIES = {COMPANY: "TGH", OTHER_COMPANY: "MYC"}
CURRENCY = "EUR"
LEDGER_ACCOUNT = "Test Bank"
BANK = "_Test Kefiya Bank"
BANK_ACCOUNT = "_Test Kefiya Giro"


def execute():
    prepare_erpnext()
    for company, abbr in COMPANIES.items():
        make_company(company, abbr)
        make_bank_ledger_account(company)
    make_company_bank_account()
    set_default_currency()
    # No commit here: `bench execute` commits once this returns


def prepare_erpnext():
    """Have ERPNext make its presets and its own test data, the way its tests do.

    ERPNext 16 makes them when its test utilities are imported. The test
    runner imports those in any case, and they fail on a site the setup wizard
    has been through, so the wizard must not run there. ERPNext 15 runs the
    wizard from its before_tests hook instead.
    """
    frappe.flags.in_test = True
    if hasattr(frappe, "in_test"):
        frappe.in_test = True
    try:
        import erpnext.tests.utils as erpnext_tests

        if not hasattr(erpnext_tests, "BootStrapTestData"):
            from erpnext.setup.utils import before_tests

            before_tests()
    finally:
        frappe.flags.in_test = False
        if hasattr(frappe, "in_test"):
            frappe.in_test = False


def set_default_currency():
    """ERPNext's test data leaves another currency as the site's default.

    A Bank Transaction without a currency takes the default, and the bank
    accounts of the tests are in EUR.
    """
    defaults = frappe.get_single("Global Defaults")
    if defaults.default_currency != CURRENCY:
        defaults.default_currency = CURRENCY
        defaults.save()


def make_company(company: str, abbr: str):
    if frappe.db.exists("Company", company):
        return
    frappe.get_doc({
        "doctype": "Company",
        "company_name": company,
        "abbr": abbr,
        "country": "Germany",
        "default_currency": CURRENCY,
        "chart_of_accounts": "Standard",
    }).insert()


def bank_ledger_account(company: str) -> str | None:
    return frappe.db.get_value(
        "Account", {"company": company, "account_type": "Bank", "is_group": 0}
    )


def make_bank_ledger_account(company: str):
    """The standard chart only has the group "Bank Accounts", with nothing in it."""
    if bank_ledger_account(company):
        return
    group = frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Bank", "is_group": 1, "root_type": "Asset"},
    )
    frappe.get_doc({
        "doctype": "Account",
        "account_name": LEDGER_ACCOUNT,
        "parent_account": group,
        "company": company,
        "account_type": "Bank",
        "is_group": 0,
    }).insert()


def make_company_bank_account():
    if frappe.db.exists("Bank Account", {"is_company_account": 1}):
        return
    if not frappe.db.exists("Bank", BANK):
        frappe.get_doc({"doctype": "Bank", "bank_name": BANK}).insert()
    frappe.get_doc({
        "doctype": "Bank Account",
        "account_name": BANK_ACCOUNT,
        "bank": BANK,
        "company": OTHER_COMPANY,
        "is_company_account": 1,
        "account": bank_ledger_account(OTHER_COMPANY),
    }).insert()
