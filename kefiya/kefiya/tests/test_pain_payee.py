# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Reading the payee back out of the order that was sent.

A remembered VoP decision is filed against this, so a wrong reading files it
against somebody nobody looked at. Everything unreadable therefore has to come
back as "no payee", which leaves the reviewer being asked.
"""

import unittest

from kefiya.utils import pain_payee

NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"


def message(payees, namespace=NS):
    rows = "".join(
        "<CdtTrfTxInf><Cdtr><Nm>{0}</Nm></Cdtr>"
        "<CdtrAcct><Id><IBAN>{1}</IBAN></Id></CdtrAcct></CdtTrfTxInf>".format(
            name, iban)
        for name, iban in payees)
    return ('<Document xmlns="{0}"><CstmrCdtTrfInitn><PmtInf>{1}'
            '</PmtInf></CstmrCdtTrfInitn></Document>').format(namespace, rows)


class TestReadingThePayee(unittest.TestCase):

    def test_one_payment_one_payee(self):
        self.assertEqual(
            pain_payee.the_only_payee(message([("ACME GmbH", "DE0212")])),
            ("ACME GmbH", "DE0212"))

    def test_the_namespace_does_not_matter(self):
        """Banks accept several pain.001 versions and each has its own
        namespace; matching the full tag would read exactly one of them."""
        other = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"
        self.assertEqual(
            pain_payee.the_only_payee(
                message([("ACME GmbH", "DE0212")], namespace=other)),
            ("ACME GmbH", "DE0212"))

    def test_a_collective_order_has_no_single_payee(self):
        """One decision cannot stand for several payees, so the reviewer is
        asked -- the same answer as an unreadable message."""
        xml = message([("ACME GmbH", "DE0212"), ("Beta AG", "DE0213")])
        self.assertEqual(len(pain_payee.payees_in(xml)), 2)
        self.assertIsNone(pain_payee.the_only_payee(xml))

    def test_all_payees_come_back_in_order(self):
        xml = message([("ACME GmbH", "DE0212"), ("Beta AG", "DE0213")])
        self.assertEqual(pain_payee.payees_in(xml),
                         [("ACME GmbH", "DE0212"), ("Beta AG", "DE0213")])

    def test_half_a_payee_is_no_payee(self):
        self.assertIsNone(pain_payee.the_only_payee(message([("", "DE0212")])))
        self.assertIsNone(pain_payee.the_only_payee(message([("ACME", "")])))

    def test_nothing_readable_is_no_payee(self):
        for value in ("", None, "not xml at all", "<Document>"):
            self.assertEqual(pain_payee.payees_in(value), [], repr(value))
            self.assertIsNone(pain_payee.the_only_payee(value), repr(value))

    def test_bytes_read_too(self):
        """build_pain001_for decodes, but sepaxml hands back bytes and a
        caller that forwards them must not silently lose the payee."""
        xml = message([("ACME GmbH", "DE0212")]).encode("utf-8")
        self.assertEqual(pain_payee.the_only_payee(xml), ("ACME GmbH", "DE0212"))

    def test_whitespace_around_the_values_is_dropped(self):
        xml = ('<Document xmlns="{0}"><CdtTrfTxInf><Cdtr><Nm>\n  ACME GmbH\n'
               '  </Nm></Cdtr><CdtrAcct><Id><IBAN> DE0212 </IBAN></Id>'
               '</CdtrAcct></CdtTrfTxInf></Document>').format(NS)
        self.assertEqual(pain_payee.the_only_payee(xml), ("ACME GmbH", "DE0212"))
