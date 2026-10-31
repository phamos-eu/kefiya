# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt
"""Demo data for the Kefiya page.

Create:  bench --site <site> execute kefiya.utils.demo_data.create --kwargs '{"company": "My Company"}'
Remove:  bench --site <site> execute kefiya.utils.demo_data.delete
All demo records are tagged with DEMO_TAG so they can be removed again.
"""

import random

import frappe
from frappe.utils import add_days, add_to_date, now_datetime, nowdate

DEMO_TAG = "kefiya-demo"

BANKS = [
    # login name, bank, account name, iban, blz, failed connections, linked
    ("DKB Business", "DKB", "Geschäftskonto", "DE89999999990001111111", "99999999", 0, True),
    ("Sparkasse KölnBonn", "Sparkasse KölnBonn", "Girokonto", "DE19888888880004444444", "88888888", 3, True),
    ("GLS Tagesgeld", "GLS Bank", "Tagesgeld", "DE34777777770001234567", "77777777", 0, False),
]

# Invented names only: a counterparty is a third party, and its name has no
# place in the source (see CLAUDE.md).
PARTIES = [
    ("Beispiel Bürobedarf GmbH", -1), ("Stadtwerke Musterstadt", -1), ("Muster Telefon AG", -1),
    ("Beispiel Hosting GmbH", -1), ("Feinmechanik Muster KG", 1), ("Gärtnerei Beispiel", 1),
    ("Musterstoffe GmbH", 1), ("Beispiel Handels AG", 1), ("Finanzamt Musterstadt", -1),
]


def create(company="My Company"):
    delete()
    bank_accounts = create_bank_accounts(company)
    create_logins(company, bank_accounts)
    create_schedule()
    create_imports()
    create_transactions(company, bank_accounts)
    create_statement_import(company, bank_accounts)
    frappe.db.commit()
    print("Kefiya demo data created for", company)


def create_bank_accounts(company):
    gl_accounts = frappe.get_all(
        "Account",
        filters={"company": company, "account_type": "Bank", "is_group": 0},
        pluck="name", order_by="name",
    )
    accounts = {}
    for i, (login, bank, account_name, iban, *_rest) in enumerate(BANKS):
        if not frappe.db.exists("Bank", bank):
            frappe.get_doc({"doctype": "Bank", "bank_name": bank}).insert()
        doc = frappe.get_doc({
            "doctype": "Bank Account",
            "account_name": account_name,
            "bank": bank,
            "company": company,
            "is_company_account": 1,
            "account": gl_accounts[i % len(gl_accounts)] if gl_accounts else None,
            "iban": iban,
            "branch_code": DEMO_TAG,
        }).insert()
        accounts[login] = doc.name
    return accounts


def create_logins(company, bank_accounts):
    for login, bank, account_name, iban, blz, failed, linked in BANKS:
        frappe.get_doc({
            "doctype": "Kefiya Login",
            "login_name": login,
            "fints_url": "https://fints.example.com/" + DEMO_TAG,
            "blz": blz,
            "fints_login": "demo",
            "fints_password": "demo",
            "account_iban": iban,
            "iban_list": iban,
            "company": company,
            "bank_account": bank_accounts[login] if linked else None,
            "failed_connection": failed,
            "tan_state_updated": add_days(now_datetime(), -1 if not failed else -12),
        }).insert(ignore_mandatory=True)


def create_schedule():
    schedule = frappe.get_single("Kefiya Schedule")
    schedule.schedule_items = []
    schedule.append("schedule_items", {"kefiya_login": "DKB Business", "import_frequency": "Daily", "hour": "06", "active": 1})
    schedule.append("schedule_items", {"kefiya_login": "Sparkasse KölnBonn", "import_frequency": "Weekly", "hour": "07", "active": 0})
    schedule.flags.ignore_validate = True
    schedule.save()


def create_imports():
    today = nowdate()
    imports = [
        ("DKB Business", -14, -8, 1), ("DKB Business", -7, -1, 1), ("DKB Business", -1, 0, 1),
        ("Sparkasse KölnBonn", -40, -20, 1), ("Sparkasse KölnBonn", -19, -12, 1),
    ]
    for login, start, end, docstatus in imports:
        doc = frappe.get_doc({
            "doctype": "Kefiya Import",
            "kefiya_login": login,
            "from_date": add_days(today, start),
            "to_date": add_days(today, end),
            "start_date": add_days(today, start),
            "end_date": add_days(today, end),
        }).insert()
        if docstatus:
            doc.submit()
        creation = add_to_date(add_days(today, end), hours=6)
        frappe.db.set_value("Kefiya Import", doc.name, "creation", creation, update_modified=False)


def create_transactions(company, bank_accounts):
    rng = random.Random(42)
    currency = frappe.get_cached_value("Company", company, "default_currency")
    plan = [("DKB Business", 26, 21), ("Sparkasse KölnBonn", 9, 30)]
    for login, count, days_back in plan:
        for i in range(count):
            party, sign = rng.choice(PARTIES)
            amount = round(rng.uniform(19, 4800), 2)
            doc = frappe.get_doc({
                "doctype": "Bank Transaction",
                "date": add_days(nowdate(), -rng.randint(0, days_back)),
                "bank_account": bank_accounts[login],
                "company": company,
                "currency": currency,
                "deposit": amount if sign > 0 else 0,
                "withdrawal": amount if sign < 0 else 0,
                "description": f"{party} RE-{rng.randint(10000, 99999)}",
                "bank_party_name": party,
                "reference_number": DEMO_TAG,
                "transaction_id": f"{DEMO_TAG}-{login[:3]}-{i}",
            }).insert()
            doc.submit()
            # roughly a third is already reconciled
            if rng.random() < 0.35:
                frappe.db.set_value("Bank Transaction", doc.name, {
                    "allocated_amount": amount, "unallocated_amount": 0, "status": "Reconciled",
                })


def create_statement_import(company, bank_accounts):
    doc = frappe.get_doc({
        "doctype": "Kefiya Bank Statement Import",
        "company": company,
        "bank_account": bank_accounts["Sparkasse KölnBonn"],
        "bank": "Sparkasse KölnBonn",
        "status": "Partial Success",
        "payload_count": 42,
        "imported_records": 39,
    })
    doc.flags.ignore_mandatory = True
    doc.insert()


def delete():
    logins = [b[0] for b in BANKS]
    for name in frappe.get_all("Bank Transaction", filters={"reference_number": DEMO_TAG}, pluck="name"):
        frappe.db.set_value("Bank Transaction", name, "docstatus", 2)
        frappe.delete_doc("Bank Transaction", name, force=True)
    for name in frappe.get_all("Kefiya Import", filters={"kefiya_login": ["in", logins]}, pluck="name"):
        frappe.db.set_value("Kefiya Import", name, "docstatus", 2)
        frappe.delete_doc("Kefiya Import", name, force=True)
    for name in frappe.get_all("Kefiya Bank Statement Import", filters={"bank": ["in", [b[1] for b in BANKS]]}, pluck="name"):
        frappe.delete_doc("Kefiya Bank Statement Import", name, force=True)
    frappe.db.delete("Kefiya Schedule Item", {"parent": "Kefiya Schedule", "kefiya_login": ["in", logins]})
    for login in logins:
        if frappe.db.exists("Kefiya Login", login):
            frappe.delete_doc("Kefiya Login", login, force=True)
    for name in frappe.get_all("Bank Account", filters={"branch_code": DEMO_TAG}, pluck="name"):
        frappe.delete_doc("Bank Account", name, force=True)
    frappe.db.commit()
