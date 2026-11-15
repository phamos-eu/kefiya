# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A first mapping for a file no template fits, from common header names and the values below them.

Only a starting point for the mapping step: the user checks and changes it there.
"""

from datetime import datetime

from kefiya.utils.statement_import.mapping import DATE_FORMATS
from kefiya.utils.statement_import.transform import parse_amount

# Target -> header names, lower case, most specific first
HEADER_NAMES = {
    "date": ["buchungsdatum", "buchungstag", "belegdatum", "datum", "booking date", "transaction date", "date"],
    "amount": ["betrag (€)", "betrag (eur)", "betrag", "umsatz", "amount", "brutto"],
    "deposit": ["haben", "gutschrift", "eingang", "credit", "deposit", "paid in"],
    "withdrawal": ["soll", "lastschrift", "ausgang", "debit", "withdrawal", "paid out"],
    "currency": ["währung", "waehrung", "currency"],
    "party_name": [
        "name zahlungsbeteiligter", "beguenstigter/zahlungspflichtiger", "begünstigter/zahlungspflichtiger",
        "auftraggeber/empfänger", "empfänger/auftraggeber", "name", "counterparty", "payee", "party",
    ],
    "party_iban": ["iban zahlungsbeteiligter", "kontonummer/iban", "iban", "counterparty iban"],
    "reference_number": ["kundenreferenz", "end-to-end-referenz", "referenz", "reference", "ref"],
    "transaction_type": ["buchungstext", "umsatzart", "umsatztyp", "transaction type", "type"],
}
# Payer and payee in two columns; used when a file has both
PAYER_NAMES = ["zahlungspflichtige*r", "zahlungspflichtiger", "auftraggeber", "payer", "from"]
PAYEE_NAMES = ["zahlungsempfänger*in", "zahlungsempfänger", "empfänger", "payee", "to"]
DESCRIPTION_NAMES =["verwendungszweck", "buchungstext", "beschreibung", "description", "purpose", "details", "memo"]

SAMPLE_ROWS = 30


def find_column(header: list[str], names: list[str], taken: set) -> str | None:
    lowered = {column.lower(): column for column in header if column not in taken}
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


def column_values(table: dict, column: str) -> list[str]:
    position = table["header"].index(column)
    values = (row[position].strip() for _line, row in table["rows"][:SAMPLE_ROWS] if position < len(row))
    return [value for value in values if value]


def guess_date_format(values: list[str]) -> str:
    """Return the date format most values have (a sum line is no date); day-first wins a tie."""
    def readable(date_format):
        count = 0
        for value in values:
            try:
                datetime.strptime(value, date_format)
                count += 1
            except ValueError:
                pass
        return count

    counts = [readable(date_format) for date_format in DATE_FORMATS]
    return DATE_FORMATS[counts.index(max(counts))] if values and max(counts) else DATE_FORMATS[0]


def guess_number_format(values: list[str]) -> str:
    """Return "de" or "en", whichever reads more of the values."""
    def readable(number_format):
        count = 0
        for value in values:
            try:
                parse_amount(value, number_format)
                count += 1
            except ValueError:
                pass
        return count

    return "en" if readable("en") > readable("de") else "de"


def guess_mapping(table: dict) -> dict:
    """Return a mapping for the table's header; it may lack the date or amount if no name fits."""
    header, taken, columns = table["header"], set(), {}
    payer, payee = find_column(header, PAYER_NAMES, taken), find_column(header, PAYEE_NAMES, taken)
    if payer and payee and payer != payee:
        columns.update(party_name_deposit=payer, party_name_withdrawal=payee)
        taken.update((payer, payee))
    for target, names in HEADER_NAMES.items():
        if target == "party_name" and "party_name_deposit" in columns:
            continue
        if target in ("deposit", "withdrawal") and "amount" in columns:
            continue
        column = find_column(header, names, taken)
        if column:
            columns[target] = column
            taken.add(column)
    if ("deposit" in columns) != ("withdrawal" in columns):
        # One of the two is more likely a column of another meaning ("Eingang" as a date)
        columns.pop("deposit", None)
        columns.pop("withdrawal", None)

    description = find_column(header, DESCRIPTION_NAMES, taken)
    amount_columns = [columns[target] for target in ("amount", "deposit", "withdrawal") if target in columns]
    amounts = [value for column in amount_columns for value in column_values(table, column)]
    return {
        "delimiter": table["delimiter"],
        "header_row": table["header_row"],
        "number_format": guess_number_format(amounts) if amounts else "de",
        "date_format": guess_date_format(column_values(table, columns["date"])) if "date" in columns else DATE_FORMATS[0],
        "columns": columns,
        "description": {"columns": [description] if description else [], "formatter": "joined"},
        "filters": [],
    }
