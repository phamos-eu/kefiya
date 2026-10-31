# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Every user-facing string of the document service has a German translation.

A translation file rots quietly: a label added next month is simply English on
a German screen, and nobody notices until a user does. So the coverage is
asserted rather than assumed.

The second half matters more than the first. An app translation is applied
SITE-WIDE, so a generic source string overrides that word everywhere: shipping
"Accounts" -> "Konten" renames the whole accounting module, and "Note" ->
"Hinweis" renames the Note DocType. Those source strings were made specific in
this app instead, and this test keeps them that way.
"""

import csv
import json
import os
import re
import unittest


def _app_path(*parts):
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), *parts)


def _rows():
    path = _app_path("translations", "de.csv")
    with open(path, encoding="utf-8") as handle:
        return [r for r in csv.reader(handle) if r]


def _po_msgids():
    """The sources translated in locale/de.po.

    The app carries two catalogues. locale/de.po is the one Frappe v15 builds
    from the DocType JSON -- labels, Select options, descriptions -- and
    translations/de.csv holds what the code says at runtime. Both are read for
    the same site, so a source string in both is a source string translated
    twice, and which of the two wins is not something this app decides.
    """
    path = _app_path("locale", "de.po")
    if not os.path.exists(path):
        return set()
    found = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("msgid \""):
                text = line[len("msgid \""):].rstrip()[:-1]
                if text:
                    found.add(re.sub(
                        r"\\(.)",
                        lambda m: _ESCAPES.get(m.group(1), m.group(1)), text))
    return found


#: Left to the framework on purpose: either its translation is already right
#: ("Bank Account" -> "Bankkonto") or the string is a product name that must
#: not be translated at all.
LEFT_TO_THE_FRAMEWORK = {
    "Bank Account", "Date", "Name", "GetMyInvoices",
    "Enabled", "Provider", "Base URL", "API Key", "Match Value",
    # The transfer screens. Every one of these is a word the framework already
    # translates, and translates the same way this app would -- "Amount" is
    # "Betrag", "Delete" is "Löschen". Shipping our own would override the
    # framework's for the entire site to arrive at the same word.
    "Amount", "Draft", "Delete", "Send", "Refresh", "Total", "Company",
    "IBAN", "Recipient", "Transfer", "Loading ...", "public",
    "Order", "Payments", "Yes", "No", "Save", "When",
}

#: Words the framework already uses for something else. Translating them from
#: this app would rename that something else across the whole site.
#:
#: "State" is here for the opposite reason, and it is the more instructive
#: one. It was left to the framework on the assumption that a word we do not
#: translate cannot go wrong. The framework knows "State" as the address
#: field, so the order's state came out on screen as "Bundesland". Not
#: translating a generic word is as much a decision as translating it -- both
#: get it wrong. Only a source string specific to what it means gets it right,
#: which is why this app says "Order state".
CLAIMED_ELSEWHERE = {"Accounts", "Note", "Failed", "Credentials", "Status",
                     "State",
                     # "Account" is ERPNext's ledger account and "Type" is on
                     # half the forms in the product. Both were nearly used as
                     # labels in the send confirmation -- "Paying account" and
                     # "Transfer type" say the same thing and belong to us.
                     "Account", "Type"}


#: The screens whose JavaScript is covered here as well. The document service
#: was the first; the transfer screens joined it when the outgoing-payments
#: list moved out of a stored Custom HTML Block into this app -- it was written
#: in German inside that block, so moving it here without the translations
#: would have turned the whole page English.
#:
#: Not every controller: bank_refresh.js and the FinTS dialogs are still
#: English-sourced and untranslated, and pretending otherwise by listing them
#: would only make this test fail for work nobody has started.
COVERED_CONTROLLERS = (
    "transfer_details.js", "transfer_form.js", "payment_outbox.js",
)

#: `__("a" + "b")` -- one message written across two lines. Matching only the
#: first literal would look for a string that is never shown.
_JS_CALL = re.compile(
    r'__\(\s*((?:"(?:[^"\\]|\\.)*")(?:\s*\+\s*"(?:[^"\\]|\\.)*")*)')


#: Written out rather than using codecs' "unicode_escape": that one decodes the
#: bytes as latin-1, so every em dash and ellipsis in these files would come
#: back as mojibake and read as an untranslated string.
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}


def _js_strings(text):
    out = set()
    for match in _JS_CALL.finditer(text):
        joined = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1)))
        out.add(re.sub(r"\\(.)",
                       lambda m: _ESCAPES.get(m.group(1), m.group(1)), joined))
    return out


def _sources():
    """Every translatable string of the document service and the transfers."""
    found = set()

    for name in COVERED_CONTROLLERS:
        with open(_app_path("public", "js", "controllers", name),
                  encoding="utf-8") as handle:
            found |= _js_strings(handle.read())

    for doctype in ("kefiya_document_service", "kefiya_document_account"):
        with open(_app_path("kefiya", "doctype", doctype, doctype + ".json"),
                  encoding="utf-8") as handle:
            meta = json.load(handle)
        found.add(meta["name"])
        for field in meta["fields"]:
            for key in ("label", "description"):
                if field.get(key):
                    found.add(field[key])
            if field.get("fieldtype") == "Select" and field.get("options"):
                found |= {o.strip() for o in field["options"].split("\n")
                          if o.strip()}

    with open(_app_path("kefiya", "doctype", "kefiya_document_service",
                        "kefiya_document_service.js"), encoding="utf-8") as h:
        js = h.read()
    found |= {m.group(1)
              for m in re.finditer(r'__\(\s*"((?:[^"\\]|\\.)*)"', js)}

    python = ""
    for path in (("utils", "document_service.py"),
                 # The outgoing-payments page. Its reasons an order is not
                 # going out used to be German literals inside a Server
                 # Script stored on one site -- typed without umlauts, and
                 # nothing in a stored script notices. They are source
                 # strings now, so this test reads them.
                 ("utils", "outbox.py"),
                 # The bank connection. Its messages reach the user through
                 # the fetch panel and the TAN prompt -- "the bank requires a
                 # TAN before transactions can be read" stood there in English
                 # on a German site until somebody reported it.
                 ("utils", "fints_controller.py"),
                 ("kefiya", "doctype", "kefiya_document_service",
                  "kefiya_document_service.py")):
        with open(_app_path(*path), encoding="utf-8") as handle:
            python += handle.read()
    # Unescaped like the JavaScript above, and for the same reason: what
    # frappe looks up at runtime is the STRING, not the source text. A message
    # carrying \n or an escaped quote was compared against its own backslashes
    # here and read as untranslated for as long as it existed.
    for m in re.finditer(r'_\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\)', python):
        joined = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))
        found.add(re.sub(r"\\(.)",
                         lambda m: _ESCAPES.get(m.group(1), m.group(1)),
                         joined))

    return {s for s in found if s.strip()}


class TestEveryStringIsTranslated(unittest.TestCase):

    def test_nothing_is_left_in_english_by_accident(self):
        translated = {r[0] for r in _rows()} | _po_msgids()
        missing = sorted(_sources() - translated - LEFT_TO_THE_FRAMEWORK)
        self.assertEqual(
            missing, [],
            "Untranslated strings. Add them to translations/de.csv, or to "
            "LEFT_TO_THE_FRAMEWORK if the framework already translates them "
            "correctly.")

    def test_the_file_has_two_columns_and_no_empty_translation(self):
        for row in _rows():
            self.assertEqual(len(row), 2, row)
            self.assertTrue(row[0].strip(), row)
            self.assertTrue(row[1].strip(), row)

    def test_no_source_is_translated_twice(self):
        sources = [r[0] for r in _rows()]
        duplicates = sorted({s for s in sources if sources.count(s) > 1})
        self.assertEqual(duplicates, [],
                         "One source, two translations -- whichever wins is "
                         "a coin toss.")

    def test_the_two_catalogues_do_not_overlap(self):
        """The same coin toss, one file further out.

        The account kinds are the case that made this a test. They are Select
        options, so locale/de.po already had them -- and a second set in
        de.csv disagreed with it ("Sparkonto" against "Tagesgeld / Sparen").
        Four of the seven are ERPNext's own words as well, so the duplicate
        would have renamed "Loan" and "Credit Card" across the entire site.
        """
        both = sorted({r[0] for r in _rows()} & _po_msgids())
        self.assertEqual(
            both, [],
            "Translated in locale/de.po already. Remove the row from "
            "translations/de.csv -- .po is where a DocType label belongs.")


class TestNoGenericWordIsHijacked(unittest.TestCase):
    """An app translation applies to the WHOLE site. Shipping a translation
    for a word the framework uses for something else renames that something
    else -- "Accounts" is the accounting module, "Note" is a DocType."""

    def test_the_file_claims_no_word_that_belongs_to_the_framework(self):
        sources = {r[0] for r in _rows()}
        stolen = sorted(sources & CLAIMED_ELSEWHERE)
        self.assertEqual(
            stolen, [],
            "These would be renamed across the entire site. Make the source "
            "string in this app specific instead (\"Account mapping\", "
            "\"Remark\", \"Failures\").")

    def test_the_app_uses_the_specific_wording(self):
        sources = _sources()
        for generic in CLAIMED_ELSEWHERE:
            self.assertNotIn(
                generic, sources,
                "{0} is claimed by the framework; this app must not use it as "
                "a label.".format(generic))

    def test_the_replacements_are_actually_in_use(self):
        sources = _sources()
        for specific in ("Account mapping", "Assigned accounts",
                         "Access credentials", "Failures", "Remark"):
            self.assertIn(specific, sources,
                          "{0} was translated but is used nowhere.".format(
                              specific))
