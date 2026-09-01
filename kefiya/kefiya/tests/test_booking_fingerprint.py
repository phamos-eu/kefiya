# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One booking, one fingerprint — whichever format the bank sent it in.

The cases below are not invented. They are taken off the live instance, from
the 57 pairs where kefiya imported the same booking twice, and from the shapes
that made them differ:

    "Alexander und Christina Fin keissen"   MT940, wrapped at a fixed width
    "Alexander und Christina Finkeissen"    CAMT

    "KD 40039 RNR 26/0719 Datum 28.02.20 26"   one date, wrapped mid-year
    "KD 40039 RNR 26/0719 Datum 28.02.2026"

    posting_text: MT940 'posting_text' against CAMT
                  'AdditionalEntryInformation' -- different fields entirely

The other half of the fix is the opposite mistake: the old hash carried no
account, so the same amount on the same day with the same purpose on a SECOND
account was treated as already imported and skipped. That one shows up not as
a duplicate but as a payment that is missing, which is worse and harder to
notice.
"""

import unittest
from datetime import date as Date

from kefiya.utils.booking_fingerprint import (FORMS, as_day, as_money,
                                              canonical, known_forms, legacy,
                                              tidy)


class TestTidying(unittest.TestCase):

    def test_fixed_width_wrapping_collapses(self):
        self.assertEqual(tidy("Datum 28.02.20  26"), "datum 28.02.20 26")

    def test_case_and_edges(self):
        self.assertEqual(tidy("  MIETE Nr. 7 "), "miete nr. 7")

    def test_nothing(self):
        self.assertEqual(tidy(None), "")
        self.assertEqual(tidy(""), "")

    def test_bytes(self):
        self.assertEqual(tidy(b"Miete"), "miete")


class TestTheDay(unittest.TestCase):

    def test_a_date_object(self):
        self.assertEqual(as_day(Date(2026, 3, 30)), "2026-03-30")

    def test_a_timestamp_string(self):
        self.assertEqual(as_day("2026-03-30 00:00:00"), "2026-03-30")

    def test_the_dotted_form_mt940_uses(self):
        self.assertEqual(as_day("2026.03.30"), "2026-03-30")

    def test_nothing(self):
        self.assertEqual(as_day(None), "")


class TestTheAmount(unittest.TestCase):

    def test_two_decimals_always(self):
        self.assertEqual(as_money(50), "50.00")
        self.assertEqual(as_money("50.0"), "50.00")
        self.assertEqual(as_money(50.0), "50.00")

    def test_the_sign_is_not_part_of_it(self):
        """Direction is carried by deposit/withdrawal, not by the hash."""
        self.assertEqual(as_money(-222.39), as_money(222.39))

    def test_nonsense(self):
        self.assertEqual(as_money("keine Zahl"), "")
        self.assertEqual(as_money(None), "")


class TestOneBookingOneFingerprint(unittest.TestCase):

    def _print(self, **overrides):
        row = dict(
            bank_account="Brilu KG Mietkonto Sparkasse - Sparkasse Heidelberg",
            date="2026-03-30", amount=222.39,
            iban="DE02120300000000202051",
            name="EHTW Service GmbH",
            purpose="KD 40039 RNR 26/0719 Datum 28.02.2026",
        )
        row.update(overrides)
        return canonical(**row)

    def test_the_two_routes_agree(self):
        """MT940's wrapping and CAMT's clean text are one booking."""
        self.assertEqual(
            self._print(),
            self._print(date=Date(2026, 3, 30), amount="222.390",
                        purpose="KD 40039 RNR 26/0719  Datum 28.02.2026"))

    def test_the_posting_text_is_not_in_it(self):
        """It is the field that differs most between the two formats, and it
        is not part of what makes a booking that booking."""
        self.assertEqual(
            len(set(canonical(
                "Konto", "2026-03-30", 10, "DE1", "Name", "Miete")
                for _ in range(2))), 1)

    def test_the_iban_decides_who_when_there_is_one(self):
        """A name is wrapped differently by each format; an IBAN is not."""
        self.assertEqual(
            self._print(name="EHTW Service  GmbH"),
            self._print(name="EHTW SERVICE GMBH"))

    def test_the_name_decides_when_there_is_no_iban(self):
        self.assertNotEqual(
            self._print(iban=None, name="Kemal Budic"),
            self._print(iban=None, name="Chiara Dangelo"))


class TestTheAccountIsPartOfIt(unittest.TestCase):
    """The half of the fix nobody would report as a bug: the old hash carried
    no account, so an identical booking on a second account was skipped as
    "already imported" and never appeared at all."""

    def test_two_accounts_two_bookings(self):
        one = canonical("Konto A", "2026-03-30", 1000, None, "Mieter", "Miete")
        two = canonical("Konto B", "2026-03-30", 1000, None, "Mieter", "Miete")
        self.assertNotEqual(one, two)

    def test_the_same_account_is_the_same_booking(self):
        one = canonical("Konto A", "2026-03-30", 1000, None, "Mieter", "Miete")
        two = canonical("konto a ", "2026-03-30", 1000, None, "Mieter", "Miete")
        self.assertEqual(one, two)


class TestTheOldHashesAreStillRecognised(unittest.TestCase):
    """Without this the fix is a catastrophe rather than a fix: a new hash for
    an old booking means the next fetch does not recognise it and imports the
    whole history again."""

    def test_the_old_form_is_offered(self):
        """Und es ist EINE, nicht zwei.

        Vorher standen hier zwei Altformen, legacy_camt und legacy_mt940 --
        zwei Namen fuer denselben Rumpf. Der Test prueft sie mit assertIn
        einzeln ab, was trivial bestand, weil es derselbe Wert war. Ueber
        die zweite Form sagte er nichts. Die ANZAHL ist die Zusicherung,
        auf der die ganze Zusage dieses Moduls ruht.
        """
        forms = known_forms(
            bank_account="Konto", date="2026-03-30", amount=222.39,
            iban="DE1", name="EHTW Service GmbH",
            posting_text="UEBERWEISUNG", purpose="RNR 26/0719")
        self.assertEqual(len(forms), FORMS)
        self.assertEqual(len(set(forms)), FORMS,
                         "zwei Formen, die denselben Hash liefern, sind eine")
        self.assertIn(legacy("2026-03-30", 222.39, "EHTW Service GmbH",
                             "UEBERWEISUNG", "RNR 26/0719"), forms)

    def test_the_new_one_is_written(self):
        """First in the list, and that is the one the caller stores."""
        forms = known_forms(
            bank_account="Konto", date="2026-03-30", amount=222.39,
            iban="DE1", name="N", posting_text="P", purpose="Z")
        self.assertEqual(forms[0], canonical("Konto", "2026-03-30", 222.39,
                                             "DE1", "N", "Z"))

    def test_the_old_form_is_reproduced_to_the_character(self):
        """It is what is in the database. Anything else does not match."""
        import hashlib
        expected = hashlib.md5(
            "2026-03-30,222.39,N,P,Z".encode()).hexdigest()
        self.assertEqual(legacy("2026-03-30", 222.39, "N", "P", "Z"),
                         expected)

    def test_the_new_form_and_the_old_one_differ(self):
        """Sonst waere die Suche unter beiden eine Suche unter einer."""
        forms = known_forms(
            bank_account="K", date="d", amount=1, iban="i", name="n",
            posting_text="p", purpose="z")
        self.assertEqual(len(forms), len(set(forms)))


class TestTheImportUsesIt(unittest.TestCase):

    def _source(self):
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "utils", "import_bank_transaction.py")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_both_importers_go_through_one_reader(self):
        """Two hand-rolled hashes were two things to keep right, and they
        were not kept right."""
        source = self._source()
        self.assertEqual(source.count("self._identify("), 2)
        self.assertNotIn('uniquestr = "{0},{1},{2},{3},{4}"', source)

    def test_the_lookup_covers_every_form(self):
        source = self._source()
        body = source.split("def _identify(")[1]
        self.assertIn('"reference_number": ["in", forms]', body)
        self.assertIn("booking_fingerprint.known_forms(", body)

    def test_the_account_is_handed_over(self):
        source = self._source()
        body = source.split("def _identify(")[1]
        self.assertIn("bank_account=self.kefiya_login.bank_account", body)


if __name__ == "__main__":
    unittest.main()
