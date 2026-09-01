# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What a statement file says -- read, not guessed.

This module knows about CSV, encodings, column names, amounts and dates. It
knows nothing about Frappe, Bank Accounts or bookings, and it must stay that
way: a file format is not a database concern, and keeping the two apart is
what lets every rule in here be tested without a site.

Its counterpart is statement_import.py, which takes what this produces and
books it.

An account statement is not a trusted document. Every value is parsed
defensively and a row that cannot be read is reported as unreadable rather
than guessed at.
"""

import csv
import hashlib
import io
import re
from datetime import datetime

# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

#: A format is described, not coded. Each entry declares:
#:
#:   charges_are_positive  the sign convention -- True where a positive amount
#:                         means money LEFT the account (every credit card
#:                         statement), False where it means money arrived
#:                         (every giro account). Read a card with the giro
#:                         convention and a year of spending books as income.
#:   columns               canonical field -> accepted header names, matched
#:                         case-insensitively and ignoring punctuation. A
#:                         header only has to CONTAIN the alias, because
#:                         exports append things like "(EUR)".
#:   positions             for a file with NO header row: canonical field ->
#:                         column index, plus skip_rows. Declaring it as data
#:                         is what replaced a hand-written second reader.
#:   required              fields without which the format is not recognised.
PROFILES = {
    "amex": {
        "label": "American Express",
        "charges_are_positive": True,
        "columns": {
            "date": ("datum", "date", "transaction date"),
            "amount": ("betrag", "amount"),
            "description": ("beschreibung", "description",
                            "erscheint auf ihrer abrechnung als"),
            "counterparty": ("karteninhaber", "card member", "cardmember"),
            "reference": ("referenz", "reference"),
        },
        # A card statement names no IBAN -- a card has none.
        "required": ("date", "amount"),
    },
    # A StarMoney export names 75 columns and carries SEVERAL ACCOUNTS in one
    # file. Its "IBAN" column is therefore the account the booking belongs TO,
    # not the counterparty: read as a counterparty IBAN (which is what a
    # generic giro profile would do) every booking would name its own account
    # as the other side.
    "starmoney": {
        "label": "StarMoney CSV",
        "charges_are_positive": False,
        "columns": {
            "own_iban": ("iban",),
            "date": ("buchungstag",),
            "value_date": ("wertstellungstag",),
            "amount": ("betrag",),
            "counterparty": ("beguenstigter absender name",),
            "description": ("verwendungszweckzeile 1",),
            "posting_text": ("buchungstext",),
            # The bank's own running number. Stable across a re-export of the
            # same period, which is what a reference is for.
            "reference": ("laufende nummer",),
        },
        "required": ("date", "amount", "own_iban"),
    },
    "sepa_csv": {
        "label": "SEPA / Giro CSV",
        "charges_are_positive": False,
        "columns": {
            "date": ("buchungstag", "datum", "date", "valutadatum"),
            "amount": ("betrag", "amount", "umsatz"),
            "description": ("verwendungszweck", "beschreibung", "purpose",
                            "description"),
            "counterparty": ("beguenstigter", "zahlungspflichtiger", "name",
                             "auftraggeber", "empfaenger"),
            "iban": ("iban", "kontonummer", "account"),
            "reference": ("referenz", "reference", "mandatsreferenz"),
        },
        "required": ("date", "amount"),
    },
    # The layout the very first importer could read, and the only one it could
    # read: seven rows of preamble, then columns by position. It used to be a
    # second reader with its own amount parser and its own encoding branch --
    # around 170 lines that existed because this one file has no header. As
    # five lines of data it costs nothing and shares every rule above,
    # including the amount parser whose predecessor divided by a hundred.
    "legacy_positional": {
        "label": "Positional CSV (no header)",
        "charges_are_positive": False,
        "skip_rows": 7,
        "positions": {"date": 0, "counterparty": 3, "description": 4,
                      "iban": 5, "amount": 7},
        "required": ("date", "amount"),
    },
}


def _named_profiles():
    return {n: p for n, p in PROFILES.items() if p.get("columns")}


def _positional_profiles():
    return {n: p for n, p in PROFILES.items() if p.get("positions")}


def _normalise_header(value):
    """Compare header cells without caring about case, spacing or umlauts."""
    text = str(value or "").strip().lower()
    text = (text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                .replace("ß", "ss"))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _match_columns(header, profile):
    """Position of each canonical field in this header row, or {}.

    Two passes, in order of confidence: a cell that IS the alias wins outright;
    only if none does, the longest alias merely CONTAINED in a cell wins. That
    ordering is the whole rule -- it is what keeps "datum" from claiming the
    "valutadatum" column while a plain "Datum" column is present.
    """
    cells = [_normalise_header(cell) for cell in header]
    found = {}

    for field, aliases in profile["columns"].items():
        exact = next((i for i, cell in enumerate(cells)
                      if cell and cell in aliases), None)
        if exact is not None:
            found[field] = exact
            continue

        best_index, best_length = None, 0
        for index, cell in enumerate(cells):
            for alias in aliases:
                if cell and alias in cell and len(alias) > best_length:
                    best_index, best_length = index, len(alias)
        if best_index is not None:
            found[field] = best_index

    return found


def detect_profile(header):
    """Which named format this header row belongs to, or (None, {}).

    The profile that maps the most columns wins; one whose required fields are
    missing does not compete at all.
    """
    best_name, best_columns, best_score = None, {}, 0
    for name, profile in _named_profiles().items():
        columns = _match_columns(header, profile)
        if not all(field in columns for field in profile["required"]):
            continue
        if len(columns) > best_score:
            best_name, best_columns, best_score = name, columns, len(columns)
    return best_name, best_columns


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------

_AMOUNT_NOISE = re.compile(r"[^\d,.\-+]")


def parse_amount(value):
    """Read an amount without guessing which separator means what.

    The reader this replaces treated a plain "1234" as 12.34 -- it split off
    the last two digits whenever it found no separator, so an amount written
    without decimals was silently divided by a hundred. Here a number without
    a separator is that number.

    Handles: "1.234,56", "1,234.56", "1234.56", "1234", "-12,50", "12,50-",
    "EUR 1.234,56", non-breaking spaces.

    :return: float, or None when the text is not an amount
    """
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").strip().strip('"').strip()
    if not text:
        return None

    trailing_minus = text.endswith("-") and not text.startswith("-")
    text = _AMOUNT_NOISE.sub("", text)
    if trailing_minus:
        text = "-" + text.rstrip("-")
    text = text.replace("+", "")
    if not text or text in ("-", ".", ","):
        return None

    negative = text.startswith("-")
    digits = text.lstrip("-")

    last_dot, last_comma = digits.rfind("."), digits.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        # Both present: the one further right is the decimal separator.
        if last_comma > last_dot:
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif last_comma >= 0:
        # "1,50" is fifty cents, "1,500" is a thousand and a half.
        digits = (digits.replace(",", ".") if len(digits) - last_comma - 1 == 2
                  else digits.replace(",", ""))
    elif last_dot >= 0:
        digits = (digits if len(digits) - last_dot - 1 == 2
                  else digits.replace(".", ""))

    try:
        amount = float(digits)
    except ValueError:
        return None
    return -amount if negative else amount


#: Written out rather than left to a guessing parser: an ambiguous "01/02/2026"
#: must not silently become the first of February in one file and the second of
#: January in the next. German exports lead with the day.
_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
                 "%d-%m-%Y", "%Y/%m/%d")


def parse_date(value):
    """Read a booking date, or None.

    Never today's date as a fallback: a booking filed under the wrong day is
    worse than one reported as unreadable -- it lands in the wrong month and
    reconciles against nothing.
    """
    text = str(value or "").strip().strip('"')
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Reading the file
# --------------------------------------------------------------------------

_ENCODINGS = ("utf-8", "cp1252", "iso-8859-1")


def decode(raw):
    """Text out of bytes, trying the encodings these exports actually use.

    The byte order mark is stripped BEFORE any decoding. A real StarMoney
    export carried a UTF-8 BOM in front of a cp1252 body -- the mark says
    "this is UTF-8" and the umlauts say otherwise. With the mark left in
    place, the first header cell begins with an invisible character and
    matches no column name at all.

    And it is not one encoding but two: that file held 887 UTF-8 lines and
    1.695 cp1252 lines, accesses exported at different times and concatenated.
    Decoded wholesale either way one half comes out wrong -- as cp1252 every
    "Ü" becomes "Ãœ", as UTF-8 the file does not decode at all. Line by line,
    both halves come out right.

    Splitting on the newline byte is safe for every encoding here, because
    none of them uses 0x0A inside a multi-byte sequence -- and the lines are
    rejoined exactly as found, so a newline inside a quoted CSV field survives
    untouched. The ordinary single-encoding file still costs one decode.
    """
    if isinstance(raw, str):
        return raw.lstrip("﻿")
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    out = []
    for line in raw.split(b"\n"):
        for encoding in _ENCODINGS:
            try:
                out.append(line.decode(encoding))
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            out.append(line.decode("utf-8", errors="replace"))
    return "\n".join(out)


def _delimiter(sample):
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except Exception:
        # The sniffer gives up on short or irregular files; the German exports
        # are semicolon-separated far more often than not.
        return ";" if sample.count(";") >= sample.count(",") else ","


def read_rows(text, max_header_scan=25):
    """Find the format and the data rows.

    The header is not assumed to be the first line: exports put a title, an
    account summary and a blank line above it. A file with no header at all is
    matched against the positional profiles instead, by their declared
    skip_rows -- which is the last thing tried, because a guess by position is
    always weaker than a match by name.

    :return: (profile_name, columns, rows, header_index), profile None when
        nothing matched. `columns` maps canonical field -> column index either
        way, so everything downstream is indifferent to which route was taken.
    """
    reader = csv.reader(io.StringIO(text), delimiter=_delimiter(text[:4096]))
    rows = list(reader)

    for index, row in enumerate(rows[:max_header_scan]):
        if not any(str(cell).strip() for cell in row):
            continue
        name, columns = detect_profile(row)
        if name:
            return name, columns, rows[index + 1:], index

    for name, profile in _positional_profiles().items():
        skip = profile.get("skip_rows", 0)
        body = rows[skip:]
        if not body:
            continue
        # Only accepted when the declared columns actually parse. Otherwise a
        # positional profile would swallow every unrecognised file and book
        # whatever happened to sit in those columns.
        sample = next((r for r in body if any(str(c).strip() for c in r)), None)
        if sample is None:
            continue
        if to_entry(sample, profile["positions"], profile) is not None:
            return name, profile["positions"], body, skip - 1

    return None, {}, rows, None


# --------------------------------------------------------------------------
# Rows to entries
# --------------------------------------------------------------------------

def to_entry(row, columns, profile):
    """One canonical entry out of one row, or None if unreadable."""
    def cell(field):
        index = columns.get(field)
        if index is None or index >= len(row):
            return ""
        return str(row[index] or "").strip().strip('"').strip()

    date = parse_date(cell("date"))
    amount = parse_amount(cell("amount"))
    if date is None or amount is None:
        return None

    # The sign convention of the format, applied once and here, so everything
    # downstream can think in "money in / money out" alone.
    if profile["charges_are_positive"]:
        amount = -amount

    # The posting text ("LASTSCHRIFT", "RECHNUNG") is what the bank called the
    # movement; the purpose line is what the payer wrote. Both are worth
    # keeping, and neither is worth losing to the other.
    description = " ".join(
        part for part in (cell("posting_text"), cell("description")) if part)

    return {
        "date": date,
        "amount": amount,
        "description": description,
        "counterparty": cell("counterparty"),
        "iban": cell("iban").replace(" ", "").upper() or None,
        # The account the booking belongs TO, where the format names it. A
        # file covering twenty accesses is split by this rather than by hand.
        "own_iban": cell("own_iban").replace(" ", "").upper() or None,
        "reference": cell("reference") or None,
    }


#: Key names the services that push entries at us actually use.
_PUSHED = {
    "date": ("date", "bookingDate", "booking_date", "datum", "valueDate",
             "transactionDate"),
    "amount": ("amount", "betrag", "value"),
    "description": ("description", "purpose", "verwendungszweck",
                    "beschreibung", "reference_text"),
    "counterparty": ("counterparty", "counterpartyName", "merchant", "name",
                     "karteninhaber"),
    "iban": ("iban", "counterpartyIban", "bank_party_iban"),
    "reference": ("reference", "referenz", "transactionId", "transaction_id",
                  "id"),
}


def normalise_pushed(entry, profile_name=None, charges_are_positive=None):
    """One canonical entry out of one record handed over by an outside service.

    Same rules as to_entry(), without the file: amounts and dates parsed
    defensively and, above all, the SIGN settled. That is why this takes a
    profile rather than assuming one -- a card feed states a charge as a
    positive number, a giro feed states money arriving as a positive number,
    and guessing wrong books a year of spending as income.

    :raises ValueError: when neither a known profile nor an explicit
        convention was given. Refused rather than assumed.
    :return: canonical entry, or None when the record cannot be read
    """
    if charges_are_positive is None:
        if profile_name not in PROFILES:
            raise ValueError(profile_name)
        charges_are_positive = PROFILES[profile_name]["charges_are_positive"]

    def pick(field):
        for key in _PUSHED[field]:
            if key in entry and entry[key] not in (None, ""):
                return entry[key]
        return None

    date = parse_date(pick("date"))
    amount = parse_amount(pick("amount"))
    if date is None or amount is None:
        return None
    if charges_are_positive:
        amount = -amount

    iban = pick("iban")
    return {
        "date": date,
        "amount": amount,
        "description": str(pick("description") or ""),
        "counterparty": str(pick("counterparty") or ""),
        "iban": str(iban).replace(" ", "").upper() if iban else None,
        "own_iban": None,
        # The service's own id is the best reference there is: it survives a
        # changed description and a re-download of the same period.
        "reference": pick("reference"),
    }


def reference_number(bank_account, entry):
    """The identity of a booking, so a second import recognises it.

    Deliberately NOT built from the row number: a file downloaded a week later
    has the same bookings at different positions. Where the export carries the
    bank's own reference, that is used instead -- it is the only identifier
    that survives a changed description.
    """
    if entry.get("reference"):
        raw = "st|{0}|{1}".format(bank_account, entry["reference"])
    else:
        raw = "st|{0}|{1}|{2}|{3}|{4}".format(
            bank_account, entry["date"], entry["amount"],
            entry.get("counterparty") or "",
            (entry.get("description") or "")[:120])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# MT940
# --------------------------------------------------------------------------
#
# Not a profile: MT940 has no header row and no columns, so it cannot be
# matched the way the CSV layouts are. It gets its own branch -- and it earns
# it, because this is the format the banks themselves speak. Every Sparkasse
# and Volksbank statement this app fetches over FinTS arrives as MT940 and is
# already parsed by the mt940 library, wrapped by python-fints, patched by our
# own mt940_compat. Only the FILE path never asked that parser anything.
#
# What makes it worth the branch is the second line of the file:
#
#     :25:67250020/9281703
#
# The statement names the account it belongs to. That is precisely the fact
# the old importer did not read -- it booked every row of a portfolio-wide
# export onto the one account picked on the form, which is how 21.252 payments
# came to stand on accounts that never made them.

#: A statement begins with its transaction reference and its account.
_MT940_HEAD = re.compile(r":20:.*?:25:", re.S)

#: ":25:BLZ/Kontonummer" -- German banks; other countries put an IBAN here,
#: which is handled by the second branch of mt940_own_iban().
_MT940_ACCOUNT = re.compile(r":25:\s*(\d{8})\s*/\s*(\w+)")
_MT940_IBAN = re.compile(r":25:\s*([A-Z]{2}\d{2}[A-Z0-9]{10,30})")

#: How long an IBAN is in each country. The split below needs it, because an
#: IBAN and the name behind it are not separated by anything: without a length
#: the pattern eats the first letters of the name, which is how "Deutsche
#: Postbank AG" lost its D.
IBAN_LENGTH = {
    "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22,
    "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27,
    "HR": 21, "HU": 28, "IE": 22, "IT": 27, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25,
    "RO": 24, "SE": 24, "SI": 19, "SK": 24,
}

_IBAN_SHAPE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]+$")


def iban_checksum_ok(value):
    """Does this IBAN satisfy its own check digits?

    The mod-97 rule, which is what makes an IBAN self-verifying: move the first
    four characters to the end, read letters as numbers (A=10 … Z=35), and the
    whole thing modulo 97 must be 1.
    """
    text = str(value or "").replace(" ", "").upper()
    if not _IBAN_SHAPE.match(text) or not 15 <= len(text) <= 34:
        return False
    rotated = text[4:] + text[:4]
    digits = "".join(
        str(ord(ch) - 55) if ch.isalpha() else ch for ch in rotated)
    return int(digits) % 97 == 1


def looks_like_mt940(text):
    """Is this an MT940 statement rather than a table?"""
    return bool(_MT940_HEAD.search(str(text or "")[:2000]))


def german_iban(blz, account):
    """The IBAN of a German account, from its bank code and account number.

    Germany's IBAN is not a lookup, it is a rule: DE, two check digits, the
    eight-digit bank code, the ten-digit account number padded with zeros. So
    ":25:67250020/9281703" is DE67672500200009281703 and nothing else -- which
    is what lets an MT940 file address its own Bank Account without anybody
    mapping account numbers by hand.

    :return: the IBAN, or None when the parts are not usable
    """
    digits = "".join(ch for ch in str(blz or "") if ch.isdigit())
    number = "".join(ch for ch in str(account or "") if ch.isdigit())
    if len(digits) != 8 or not number or len(number) > 10:
        return None

    bban = digits + number.rjust(10, "0")
    # The check digits: BBAN + country + "00", letters as numbers, mod 97.
    rearranged = bban + "131400"          # D = 13, E = 14
    check = 98 - (int(rearranged) % 97)
    return "DE{0:02d}{1}".format(check, bban)


def mt940_own_iban(text):
    """The account an MT940 statement belongs to, as an IBAN."""
    head = str(text or "")[:2000]
    named = _MT940_IBAN.search(head)
    if named:
        return named.group(1).upper()
    parts = _MT940_ACCOUNT.search(head)
    if not parts:
        return None
    return german_iban(parts.group(1), parts.group(2))


def _split_iban_and_name(value):
    """"DE68…468Deutsche Postbank AG" -> ("DE68…468", "Deutsche Postbank AG").

    The mt940 library concatenates the counterparty's IBAN and name into one
    field, with nothing between them. Left alone the IBAN ends up inside the
    name on every booking; split by a pattern alone it eats the beginning of
    the name, because a capital letter is a legal IBAN character.

    So the length decides, and the check digits confirm it. The country's own
    IBAN length is tried first; where that country is unknown, every length is
    tried and only a prefix that verifies against its own checksum is accepted.
    Guessing is not among the options: a wrong split puts a letter of the name
    into the IBAN, and that IBAN then matches no party at all.
    """
    text = str(value or "").strip()
    if len(text) < 15 or not text[:2].isalpha():
        return None, text

    country = text[:2].upper()
    lengths = ([IBAN_LENGTH[country]] if country in IBAN_LENGTH
               else range(15, 35))
    for length in lengths:
        head = text[:length]
        if len(head) == length and iban_checksum_ok(head):
            return head.upper(), text[length:].strip()
    return None, text


def mt940_entries(text):
    """Canonical entries out of an MT940 statement.

    The same shape to_entry() produces, so everything downstream -- the
    account routing, the duplicate check, the one constructor -- is indifferent
    to whether a booking came from a table or from the bank's own format.

    :return: list of entries; empty when nothing parses
    """
    # Imported here, not at module level: this module is the pure format layer
    # and its tests run without a bench. The parser is a hard dependency of the
    # app either way -- it is how every FinTS fetch reads its statements.
    from fints.utils import mt940_to_array

    own = mt940_own_iban(text)
    entries = []
    for row in mt940_to_array(str(text or "")):
        data = getattr(row, "data", None) or row

        amount = data.get("amount")
        amount = getattr(amount, "amount", amount)
        if amount is None:
            continue
        date = data.get("entry_date") or data.get("date")
        if date is None:
            continue

        iban, name = _split_iban_and_name(data.get("applicant_name"))
        description = " ".join(str(part) for part in (
            data.get("posting_text"), data.get("purpose"),
            data.get("additional_purpose")) if part)

        # NONREF means "no reference given" and is on thousands of bookings;
        # taking it as an identity would make them all the same booking. The
        # prima nota is worse still -- it is a batch number, shared by every
        # booking of a day. Neither may become the reference: without one, the
        # identity is built from what the booking actually says.
        reference = str(data.get("customer_reference") or "").strip()
        if reference.upper() in ("", "NONREF"):
            reference = str(data.get("bank_reference") or "").strip() or None

        entries.append({
            "date": date,
            "amount": float(amount),
            "description": description,
            "counterparty": name,
            "iban": iban,
            "own_iban": own,
            "reference": reference,
        })
    return entries
