import frappe
from frappe.tests.utils import FrappeTestCase

BANK = "_Test IBAN Bank"
IBAN = "DE89370400440532013000"


def make_bank_account(name, iban=None):
    return frappe.get_doc({
        "doctype": "Bank Account",
        "account_name": name,
        "bank": BANK,
        "iban": iban,
    }).insert()


class TestUniqueIban(FrappeTestCase):
    """kefiya.utils.bank_account_controller.validate_unique_iban"""

    def setUp(self):
        self.addCleanup(frappe.db.rollback)
        if not frappe.db.exists("Bank", BANK):
            frappe.get_doc({"doctype": "Bank", "bank_name": BANK}).insert()

    def test_accounts_without_iban_can_coexist(self):
        make_bank_account("_Test No IBAN 1")
        make_bank_account("_Test No IBAN 2")

    def test_same_iban_is_rejected(self):
        make_bank_account("_Test IBAN 1", IBAN)
        self.assertRaises(frappe.ValidationError, make_bank_account, "_Test IBAN 2", IBAN)
