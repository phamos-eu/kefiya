# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A lone separator is a thousands separator only before three digits.

statement_formats.parse_amount read a comma followed by anything but two
digits as a thousands separator, and a dot the same way. So "12,5" came out
as 125 and "-4,8" as -48 -- ten times the amount. DKB writes its amounts
without trailing zeros; the DKB profiles work around it with decimal_comma,
and every other format kept the bug. Found while porting the DKB imports from
the original (phamos-eu/kefiya#59, #60).

Thousands come in groups of exactly three digits, so every other count after
a lone separator is a decimal separator. Three digits stay ambiguous ("1,500")
and keep their reading as a thousands group.
"""

import unittest

from kefiya.utils import statement_formats as formats


class TestOneOrTwoDigitsAreDecimals(unittest.TestCase):

    def test_one_digit_after_a_comma(self):
        for text, amount in [("12,5", 12.5), ("-4,8", -4.8), ("1,5", 1.5),
                             ("-312,4", -312.4), ("0,5", 0.5)]:
            with self.subTest(text=text):
                self.assertEqual(formats.parse_amount(text), amount)

    def test_one_digit_after_a_dot(self):
        for text, amount in [("1.5", 1.5), ("-12.5", -12.5)]:
            with self.subTest(text=text):
                self.assertEqual(formats.parse_amount(text), amount)

    def test_two_digits_as_before(self):
        self.assertEqual(formats.parse_amount("12,50"), 12.5)
        self.assertEqual(formats.parse_amount("1.05"), 1.05)

    def test_more_than_three_digits_are_no_thousands_group(self):
        self.assertEqual(formats.parse_amount("1,2345"), 1.2345)


class TestThousandsStayThousands(unittest.TestCase):

    def test_three_digits_after_a_lone_separator(self):
        self.assertEqual(formats.parse_amount("1,500"), 1500.0)
        self.assertEqual(formats.parse_amount("1.500"), 1500.0)

    def test_several_groups(self):
        self.assertEqual(formats.parse_amount("1.234.567"), 1234567.0)
        self.assertEqual(formats.parse_amount("1,234,567"), 1234567.0)

    def test_both_separators_as_before(self):
        self.assertEqual(formats.parse_amount("2.380,5"), 2380.5)
        self.assertEqual(formats.parse_amount("1,234.56"), 1234.56)

    def test_a_malformed_grouping_is_not_an_amount(self):
        self.assertIsNone(formats.parse_amount("1.2.3"))


class TestTheGeneralReaderNowAgreesWithTheDkbProfile(unittest.TestCase):
    """The DKB amounts, read without decimal_comma, come out the same."""

    def test_dkb_amounts(self):
        for text in ("-450", "2.380,5", "-312,4", "12,5", "-4,8", "1.500",
                     "-1.234,56", "-2.000", "-0,96", "1.190,00"):
            with self.subTest(text=text):
                self.assertEqual(formats.parse_amount(text),
                                 formats.parse_amount(text, decimal_comma=True))


if __name__ == "__main__":
    unittest.main()
