# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Moving money between one's own accounts.

To the bank this is an ordinary SEPA credit transfer; nothing in the FinTS
layer needed changing. What was missing is duller than a new order type: a way
to say "that account over there" instead of typing an IBAN one already owns.
Typing it is where transposed digits come from, and a transposed digit pays a
stranger just as reliably on a transfer to oneself as on any other.
"""

import inspect
import unittest

from kefiya.utils import own_transfer


class TestWhichAccountsAreOffered(unittest.TestCase):

    def test_the_paying_account_is_left_out(self):
        """A transfer from an account to itself is not a transfer. The bank
        refuses it -- after the order went out and a TAN was spent on it."""
        source = inspect.getsource(own_transfer.own_accounts)
        self.assertIn('if row["name"] == paying_account:', source)
        self.assertIn("continue", source)

    def test_a_second_record_of_the_same_account_too(self):
        source = inspect.getsource(own_transfer.own_accounts)
        self.assertIn('_iban_of(row["name"]) == paying_iban', source)

    def test_an_account_without_an_iban_is_not_a_recipient(self):
        source = inspect.getsource(own_transfer.own_accounts)
        self.assertIn('if not row.get("iban"):', source)

    def test_only_the_company_s_own_accounts(self):
        source = inspect.getsource(own_transfer.own_accounts)
        self.assertIn('"is_company_account": 1', source)
        self.assertIn('"disabled": 0', source)

    def test_reading_the_login_needs_the_right_to_read_it(self):
        source = inspect.getsource(own_transfer.own_accounts)
        self.assertIn('frappe.has_permission("Kefiya Login", ptype="read"',
                      source)

    def test_the_account_list_goes_through_get_all(self):
        """get_all applies read permissions and User Permissions, so a user
        only ever sees accounts they may see anyway."""
        source = inspect.getsource(own_transfer.own_accounts)
        self.assertIn('frappe.get_all(\n        "Bank Account"', source)


class TestTheFormPicker(unittest.TestCase):

    def test_the_query_is_sanitised(self):
        """A link query takes free text straight from the browser."""
        source = inspect.getsource(own_transfer.own_account_query)
        self.assertIn("filters.get(\"kefiya_login\")", source)

    def test_it_searches_the_three_things_people_remember(self):
        source = inspect.getsource(own_transfer.own_account_query)
        for field in ("account_name", "bank", "iban"):
            self.assertIn(field, source)

    def test_it_reuses_the_same_list(self):
        """One place decides what may be paid, so the picker and the check
        can never drift apart."""
        source = inspect.getsource(own_transfer.own_account_query)
        self.assertIn("own_accounts(kefiya_login=", source)


class TestWhatIsCopiedIntoTheRow(unittest.TestCase):

    def test_the_account_s_iban_wins(self):
        """The account is the truth. A different IBAN in the row is a leftover
        from before the account was picked."""
        source = inspect.getsource(own_transfer.fill_from_own_account)
        self.assertIn('row.recipient_iban = iban', source)

    def test_a_name_the_user_typed_stays(self):
        source = inspect.getsource(own_transfer.fill_from_own_account)
        self.assertIn('if not getattr(row, "recipient_name", None):', source)

    def test_it_runs_on_the_server_too(self):
        """A row can arrive from an import or another script that never
        opened a form."""
        from kefiya.kefiya.doctype.kefiya_transfer import kefiya_transfer

        source = inspect.getsource(kefiya_transfer.KefiyaTransfer.validate)
        self.assertIn("own_transfer.fill_from_own_account(row)", source)


class TestPayingYourselfIsRefused(unittest.TestCase):

    def test_the_check_is_wired_into_validate(self):
        from kefiya.kefiya.doctype.kefiya_transfer import kefiya_transfer

        source = inspect.getsource(kefiya_transfer.KefiyaTransfer.validate)
        self.assertIn("own_transfer.refuse_paying_yourself(self)", source)

    def test_it_compares_normalised_ibans(self):
        """Spaces and lower case must not let it through."""
        source = inspect.getsource(own_transfer.refuse_paying_yourself)
        self.assertIn("normalize_iban(row.recipient_iban", source)

    def test_without_a_linked_account_nothing_is_claimed(self):
        source = inspect.getsource(own_transfer.refuse_paying_yourself)
        self.assertIn("if not paying_iban:", source)
        self.assertIn("return", source)

    def test_the_message_names_the_row(self):
        source = inspect.getsource(own_transfer.refuse_paying_yourself)
        self.assertIn("format(row.idx)", source)
