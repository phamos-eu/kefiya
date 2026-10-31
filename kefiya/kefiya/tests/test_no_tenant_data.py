# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Keine Daten des Betreibers im Quelltext. Niemals.

WARUM ES DIESEN TEST GIBT. Am 09.10.2026 stand in diesem offentlich
einsehbaren Repository: echte IBANs von Girokonten, die Kennung eines
Online-Banking-Zugangs neben seiner Bankleitzahl, Namen von Personen und
Gesellschaften, Buchungsvolumen je Konto, Auftragsnummern und Betraege. In
33 Dateien.

Niemand hat das boeswillig hineingeschrieben. Es ist der Hausstil: die
Messung wandert in den Docstring, damit spaeter nachlesbar ist, woran eine
Regel gemessen wurde. "Gemessen am 30.09.2026, <Gesellschaft> Volksbank"
ist intern ausgezeichnet und nach aussen eine Offenlegung. Genau deshalb
reicht ein Vorsatz nicht: der Vorsatz war nie, Daten zu veroeffentlichen,
und sie standen trotzdem drin.

Die Messung soll bleiben. Nur die Identitaet geht. Aus

    Gemessen am 30.09.2026, <Gesellschaft> Volksbank

wird

    Gemessen am 30.09.2026 an einem Volksbank-Konto

-- das Datum und die Zahlen sind der Wert dieser Kommentare, der Name ist
es nicht.

WIE DIE SPERRLISTE AUSSIEHT, OHNE SELBST EINE OFFENLEGUNG ZU SEIN. Ein Test,
der nach "<Gesellschaft>" sucht, muesste den Namen enthalten -- und haette
ihn damit wieder im Repository. Deshalb stehen unten nur SHA-256-Praefixe
der gesuchten Woerter, mit ihrer Laenge. Aus dem Hash laesst sich das Wort
nicht zurueckgewinnen; die Pruefung funktioniert trotzdem, weil sie jedes
Wort im Repository hasht und vergleicht. Dasselbe Verfahren, mit dem
Passwort-Sperrlisten arbeiten.

UND DER BEITRAG NACH OBEN. Dieser Fork soll an das Original beitragen
koennen. Dieser Test laeuft in der normalen Suite, also kann kein Zweig, der
hier gruen ist, Betreiberdaten nach oben tragen. Wer ihn aufweicht, nimmt
diese Zusage zurueck.
"""

import hashlib
import os
import re
import unicodedata
import unittest

HIER = os.path.abspath(__file__)
WURZEL = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(HIER))))

#: Ordner, die nicht zum Quelltext gehoeren.
UEBERGEHEN = {".git", "__pycache__", "node_modules", ".eggs", "dist",
              "build", ".pytest_cache", "public/dist"}

#: Nur Text. Alles andere ist kein Quelltext, den jemand liest.
TEXT = {".py", ".js", ".json", ".md", ".csv", ".txt", ".html", ".css",
        ".yml", ".yaml", ".cfg", ".toml", ".ini", ".sh", ".xml", ".po",
        ".pot", ".rst", ".ts", ".vue", ".sql"}

#: SHA-256-Praefixe (12 Hex) der gesperrten Woerter, mit ihrer Laenge in
#: normalisierter Form. Normalisiert heisst: klein, ohne Akzente, "ss" fuer
#: das scharfe s, nur Buchstaben und Ziffern.
#:
#: Es sind Namen von Personen, Gesellschaften, Bankzugaengen und Mandanten.
#: Was es genau ist, steht hier absichtlich nicht.
GESPERRT = (
    (10, "8a4152b353bf"),
    (11, "56c7cecc0b93"),
    (11, "20864cda2693"),
    (7, "8cf7c1f3da80"),
    (5, "40edecf8bc1b"),
    (9, "1714588e05f3"),
    (13, "82edd654f187"),
    (8, "8795a7ea4de1"),
    (6, "b7ed3d1a3f93"),    (7, "b4a05e2078c8"),
    (5, "e8d5cc5cc34e"),
)

#: IBANs, die in Beispielen stehen duerfen, weil sie veroeffentlichte
#: Testnummern sind oder erfundene mit gueltiger Pruefsumme (die
#: Bankleitzahlen 99999999 und 88888888 sind in Deutschland nicht vergeben).
ERLAUBTE_IBANS = frozenset((
    # Veroeffentlichte Beispiel- und Testnummern.
    "DE89370400440532013000",
    "DE98370400440532013000",   # absichtlich falsche Pruefsumme, fuer Tests
    "DE89370400440532013001",   # dito
    "DE02120300000000202051",
    "DE02120300000000202052",   # dito
    "DE02100500000054540402",
    # Erfunden, Pruefsumme gueltig.
    "DE89999999990001111111",
    "DE58999999990002222222",
    "DE27999999990003333333",
    "DE19888888880004444444",
    "DE85888888880005555555",
    "DE54888888880006666666",
    "DE23888888880007777777",
    "DE89888888880008888888",
    "DE58888888880009999999",
    "DE34777777770001234567",
    "DE50777777770007654321",
    "DE27999999990003333334",   # absichtlich falsche Pruefsumme, fuer Tests
))

#: Maildomains, die in Beispielen und Kopfzeilen stehen duerfen.
ERLAUBTE_DOMAINS = ("example.com", "example.org", "example.net",
                    "anthropic.com", "phamos.eu", "phamos.de",
                    "erpnext.com", "frappe.io", "localhost")

_IBAN = re.compile(r"\bDE[0-9]{20}\b")
_MAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_WORT = re.compile(r"[0-9A-Za-zÀ-ɏ]+")


def normalisiert(wort):
    """Klein, ohne Akzente, "ss" fuer das scharfe s, nur Buchstaben."""
    wort = unicodedata.normalize("NFKD", wort.lower())
    wort = wort.replace("ß", "ss")
    return "".join(c for c in wort if c.isalnum() and not
                   unicodedata.combining(c))


def _dateien():
    """Jede Textdatei des Quelltextbaums."""
    for ordner, unterordner, namen in os.walk(WURZEL):
        unterordner[:] = [u for u in unterordner if u not in UEBERGEHEN]
        for name in namen:
            if os.path.splitext(name)[1].lower() not in TEXT:
                continue
            pfad = os.path.join(ordner, name)
            try:
                with open(pfad, encoding="utf-8") as griff:
                    yield os.path.relpath(pfad, WURZEL), griff.read()
            except (OSError, UnicodeDecodeError):
                continue


def _wortschatz():
    """Jedes Wort des Baums, einmal, mit der Datei seines ersten Auftretens.

    Einmal, weil dieselbe Pruefung sonst hunderttausendfach laeuft: der
    Wortschatz ist klein, die Zahl der Woerter nicht.
    """
    gesehen = {}
    for datei, inhalt in _dateien():
        for treffer in _WORT.finditer(inhalt):
            wort = normalisiert(treffer.group(0))
            if len(wort) >= 4 and wort not in gesehen:
                gesehen[wort] = datei
    return gesehen


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def gesperrte_woerter(text):
    """Die gesperrten Woerter in ``text``, in Fundreihenfolge.

    Oeffentlich, damit andere Tests dieselbe Frage stellen koennen, ohne
    eine Liste von Namen bei sich zu fuehren -- genau die Liste war einmal
    die Offenlegung. test_document_service fragt so, ob die App den Ort
    fremder Zugangsdaten beim Namen nennt.
    """
    laengen = sorted({laenge for laenge, _ in GESPERRT})
    verboten = {(laenge, summe) for laenge, summe in GESPERRT}
    funde = []
    for treffer in _WORT.finditer(str(text or "")):
        wort = normalisiert(treffer.group(0))
        for laenge in laengen:
            if len(wort) >= laenge and (laenge, _hash(wort[:laenge])) in verboten:
                funde.append(treffer.group(0))
                break
    return funde


class TestKeinNameDesBetreibers(unittest.TestCase):

    def test_kein_gesperrtes_wort(self):
        laengen = sorted({laenge for laenge, _ in GESPERRT})
        verboten = {(laenge, summe) for laenge, summe in GESPERRT}
        funde = []
        for wort, datei in _wortschatz().items():
            for laenge in laengen:
                if len(wort) < laenge:
                    continue
                if (laenge, _hash(wort[:laenge])) in verboten:
                    funde.append((datei, wort))
                    break
        self.assertEqual(
            funde, [],
            "Daten des Betreibers im Quelltext. Das darf nicht sein -- siehe"
            " den Kopf dieser Datei und CLAUDE.md: die Messung bleibt, die"
            " Identitaet geht.\n" + "\n".join(
                "  {0}: {1}".format(d, w) for d, w in sorted(funde)))


class TestKeineEchteIban(unittest.TestCase):
    """Eine IBAN ist kein Geheimnis wie eine PIN -- sie steht auf jeder
    Rechnung. Zusammen mit Kontobezeichnung, Bankleitzahl und Saldogroessen
    ist sie trotzdem eine Offenlegung, die gezielte Lastschriften und
    Social Engineering gegenueber der Bank erleichtert."""

    def test_nur_erlaubte(self):
        funde = []
        for datei, inhalt in _dateien():
            if datei == os.path.relpath(HIER, WURZEL):
                continue
            for treffer in set(_IBAN.findall(inhalt)):
                if treffer not in ERLAUBTE_IBANS:
                    funde.append((datei, treffer))
        self.assertEqual(
            funde, [],
            "IBAN im Quelltext, die nicht als Beispielnummer gelistet ist."
            " Eine erfundene mit gueltiger Pruefsumme gehoert in"
            " ERLAUBTE_IBANS, eine echte gehoert hier nicht hin.\n"
            + "\n".join("  {0}: {1}".format(d, i) for d, i in sorted(funde)))


class TestKeineEchteMailadresse(unittest.TestCase):

    def test_nur_beispieldomains(self):
        funde = []
        for datei, inhalt in _dateien():
            if datei == os.path.relpath(HIER, WURZEL):
                continue
            for treffer in _MAIL.finditer(inhalt):
                # "\n@frappe.whitelist" sieht wie eine Adresse aus: das "n"
                # der Escape-Folge wird zum lokalen Teil. Wer unmittelbar
                # hinter einem Backslash steht, ist keine Mailadresse.
                if treffer.start() and inhalt[treffer.start() - 1] == "\\":
                    continue
                domain = treffer.group(1)
                if not domain.lower().endswith(ERLAUBTE_DOMAINS):
                    funde.append((datei, domain))
        self.assertEqual(
            funde, [],
            "Mailadresse im Quelltext, deren Domain nicht als Beispiel"
            " gelistet ist.\n"
            + "\n".join("  {0}: {1}".format(d, m) for d, m in sorted(funde)))


class TestDerWaechterSelbstVerraetNichts(unittest.TestCase):
    """Ein Test, der nach den Namen sucht, darf sie nicht enthalten."""

    def test_nur_hashes_in_der_sperrliste(self):
        for _, summe in GESPERRT:
            self.assertRegex(summe, r"^[0-9a-f]{12}$")

    def test_und_kein_wort_im_klartext(self):
        """Gegen sich selbst geprueft: diese Datei muss den eigenen Test
        bestehen, sonst waere die Sperrliste die Offenlegung."""
        with open(HIER, encoding="utf-8") as griff:
            eigen = griff.read()
        laengen = sorted({laenge for laenge, _ in GESPERRT})
        verboten = {(laenge, summe) for laenge, summe in GESPERRT}
        for treffer in _WORT.finditer(eigen):
            wort = normalisiert(treffer.group(0))
            for laenge in laengen:
                if len(wort) >= laenge:
                    self.assertNotIn((laenge, _hash(wort[:laenge])), verboten,
                                     treffer.group(0))


if __name__ == "__main__":
    unittest.main()
