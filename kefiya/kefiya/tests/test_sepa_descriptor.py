# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Der Schemaname im Auftrag ist der der Bank.

python-fints schickt ohne Angabe die ISO-URN. Die Sparkasse fuehrt sie,
die Volksbank nicht -- ihre Liste heisst "sepade:xsd:...". Jeder Auftrag an
die Volksbank trug einen Namen, den die Bank nicht kennt; die
Empfaengerpruefung brach dort bei der zweiten Nachfrage mit "9000 interne
Probleme" ab, sechsmal.

Die Regel laeuft hier ohne Bank. Dass submit_sepa_transfer sie benutzt,
steht im Quelltext.
"""

import os
import unittest

from kefiya.utils.sepa_descriptor import (DEFAULT_PAIN_001, choose,
                                          formats_in)

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.dirname(os.path.dirname(HIER))

VOLKSBANK = [
    "sepade:xsd:pain.008.001.02.xsd", "sepade:xsd:pain.008.001.02_GBIC_2.xsd",
    "sepade:xsd:pain.008.001.08_GBIC_5.xsd",
    "sepade:xsd:pain.001.001.03.xsd", "sepade:xsd:pain.001.001.03_GBIC_2.xsd",
    "sepade:xsd:pain.001.001.03_GBIC_3.xsd",
    "sepade:xsd:pain.001.001.09.xsd", "sepade:xsd:pain.001.001.09_GBIC_4.xsd",
    "sepade:xsd:pain.001.001.09_GBIC_5.xsd",
]
SPARKASSE = [
    "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03",
    "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09",
    "urn:iso:std:iso:20022:tech:xsd:pain.008.001.02",
    "urn:iso:std:iso:20022:tech:xsd:pain.008.001.08",
]


class TestDerNameDerBank(unittest.TestCase):

    def test_die_volksbank_bekommt_ihre_juengste_auspraegung(self):
        self.assertEqual(choose(VOLKSBANK),
                         "sepade:xsd:pain.001.001.03_GBIC_3.xsd")

    def test_die_sparkasse_bekommt_die_urn(self):
        self.assertEqual(choose(SPARKASSE), DEFAULT_PAIN_001)

    def test_ein_anderes_schema_findet_seinen_namen(self):
        self.assertEqual(choose(VOLKSBANK, "pain.001.001.09"),
                         "sepade:xsd:pain.001.001.09_GBIC_5.xsd")

    def test_eine_bank_ohne_das_schema_bleibt_beim_vorgabewert(self):
        """Dann ist die Liste das Problem, und die Bank sagt es selbst."""
        self.assertEqual(choose(["sepade:xsd:pain.008.001.02.xsd"]),
                         DEFAULT_PAIN_001)
        self.assertEqual(choose([]), DEFAULT_PAIN_001)
        self.assertEqual(choose(None), DEFAULT_PAIN_001)

    def test_lastschriftschemata_zaehlen_nicht(self):
        self.assertNotIn("008", choose(VOLKSBANK))

    def test_der_vorgabewert_ist_der_der_bibliothek(self):
        self.assertEqual(DEFAULT_PAIN_001,
                         "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03")


class ValueList:
    """Was python-fints wirklich ablegt: iterierbar, aber keine ``list``.

    Genau daran ging die erste Fassung vorbei -- sie fragte
    ``isinstance(value, (list, tuple))``, und die Volksbank bekam ihren
    eigenen Schemanamen deshalb nur bei der Echtzeitueberweisung, deren
    Segment die Bibliothek nicht parst.
    """

    def __init__(self, items):
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)


class Parsed:
    """HISPAS, wie python-fints es kennt."""

    def __init__(self, formats):
        self.parameter = type(
            "P", (), {"supported_sepa_formats": ValueList(formats)})()


class Raw:
    """HIIPZS, das python-fints nicht kennt: Listen in Listen."""

    def __init__(self, data):
        self._additional_data = data


class TestDieFormateAusDemSegment(unittest.TestCase):

    def test_aus_dem_geparsten_segment(self):
        self.assertEqual(formats_in(Parsed(SPARKASSE)), SPARKASSE)

    def test_auch_wenn_die_liste_keine_liste_ist(self):
        """fints.types.ValueList ist iterierbar und keine list -- der Fehler,
        der die Volksbank bei der normalen Ueberweisung beim Namen der
        Bibliothek liess."""
        self.assertEqual(formats_in(Parsed(VOLKSBANK))[:1],
                         ["sepade:xsd:pain.008.001.02.xsd"])
        self.assertEqual(choose(formats_in(Parsed(VOLKSBANK))),
                         "sepade:xsd:pain.001.001.03_GBIC_3.xsd")

    def test_eine_zeichenkette_wird_nicht_buchstabenweise_gelesen(self):
        """Auch Zeichenketten sind iterierbar. Eine davon ist ein Name, kein
        Behaelter voller Buchstaben."""
        self.assertEqual(formats_in(Raw("sepade:xsd:pain.001.001.03.xsd")),
                         ["sepade:xsd:pain.001.001.03.xsd"])

    def test_aus_dem_rohen_segment(self):
        raw = Raw(["1", "1", "1", [None, "sepade:xsd:pain.001.001.03.xsd",
                                   "sepade:xsd:pain.001.001.09.xsd"]])
        self.assertEqual(formats_in(raw), ["sepade:xsd:pain.001.001.03.xsd",
                                           "sepade:xsd:pain.001.001.09.xsd"])

    def test_nichts_ist_nichts(self):
        self.assertEqual(formats_in(None), [])
        self.assertEqual(formats_in(object()), [])
        self.assertEqual(formats_in(Raw(["1", "1", "0"])), [])


class TestDerAuftragTraegtIhn(unittest.TestCase):

    def test_submit_sepa_transfer_reicht_ihn_durch(self):
        pfad = os.path.join(WURZEL, "utils", "fints_controller.py")
        with open(pfad, encoding="utf-8") as handle:
            quelle = handle.read()
        koerper = quelle[quelle.index("    def submit_sepa_transfer("):]
        koerper = koerper[:koerper.index("    def _pain_descriptor(")]
        self.assertIn('kwargs["pain_descriptor"] = self._pain_descriptor(instant_payment)',
                      koerper)
        self.assertLess(koerper.index('kwargs["pain_descriptor"]'),
                        koerper.index("self.fints_connection.sepa_transfer("))
        wahl = quelle[quelle.index("    def _pain_descriptor("):]
        wahl = wahl[:wahl.index("    def _refuse_unsigned(")]
        self.assertIn('find_segment_first("HIIPZS")', wahl)
        self.assertIn('find_segment_first("HISPAS")', wahl)
        self.assertIn("sepa_descriptor.choose(supported)", wahl)
