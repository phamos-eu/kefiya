# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What a column mapping may contain, and the check that runs before one is used.

A mapping is plain data, so built-in formats and saved templates look the same:

    {
        "delimiter": ";",                  # guessed when missing
        "header_row": 4,                   # record index of the header, guessed when missing
        "number_format": "de",             # "de" 1.234,56 or "en" 1,234.56
        "date_format": "%d.%m.%y",
        "columns": {"date": "Buchungsdatum", "amount": "Betrag (€)", ...},
        "description": {"columns": ["Verwendungszweck"], "formatter": "joined"},
        "fee": {"column": "Gebühr", "party_name": "PayPal", "description": "Fee {id}", "reference": "{id}"},
        "filters": [{"column": "Status", "op": "not_equals", "value": "Gebucht"}],   # rows to skip
        "id": {"seed": "DKB VISA", "columns": [...]},   # when no transaction id column is mapped
        "optional_columns": ["Betreff"],   # may be missing from a file
    }

A mapping comes from the browser too, so everything is checked against fixed lists here.
"""

import json

from frappe import _

NUMBER_FORMATS = {"de": "1.234,56", "en": "1,234.56"}

DATE_FORMATS = (
    "%d.%m.%Y",
    "%d.%m.%y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y%m%d",
)

# Target -> label; one column each. Party name for deposits / withdrawals: the payer is in one
# column and the payee in another, the counterparty is the one that is not us.
COLUMN_TARGETS = {
    "date": "Date",
    "amount": "Amount (signed)",
    "deposit": "Deposit",
    "withdrawal": "Withdrawal",
    "currency": "Currency",
    "reference_number": "Reference Number",
    "transaction_id": "Transaction ID",
    "party_name": "Party Name",
    "party_name_deposit": "Party Name for deposits (payer)",
    "party_name_withdrawal": "Party Name for withdrawals (payee)",
    "party_iban": "Party IBAN",
    "party_account_number": "Party Account No.",
    "transaction_type": "Transaction Type",
}

DESCRIPTION_FORMATTERS = ("joined", "dkb_visa", "paypal")
FILTER_OPS = ("equals", "not_equals")

MAX_JSON_LENGTH = 20000
MAX_TEXT_LENGTH = 140
MAX_COLUMNS = 20
MAX_FILTERS = 10
MAX_HEADER_ROW = 50


class MappingError(ValueError):
    pass


def load(mapping) -> dict:
    """Return the mapping as a dict; it may come as JSON text from the browser."""
    if isinstance(mapping, str):
        if len(mapping) > MAX_JSON_LENGTH:
            raise MappingError(_("The column mapping is too long."))
        try:
            mapping = json.loads(mapping)
        except ValueError:
            raise MappingError(_("The column mapping is not valid JSON."))
    if not isinstance(mapping, dict):
        raise MappingError(_("The column mapping must be an object."))
    if len(json.dumps(mapping)) > MAX_JSON_LENGTH:
        raise MappingError(_("The column mapping is too long."))
    return mapping


def _text(value, what, allow_empty=False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or len(value) > MAX_TEXT_LENGTH:
        raise MappingError(_("{0} must be a text of at most {1} characters.").format(what, MAX_TEXT_LENGTH))
    return value


def _columns(value, what) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise MappingError(_("{0} must be a list of columns.").format(what))
    return [_text(column, what) for column in value]


def validate(mapping, header: list[str] | None = None) -> dict:
    """Return a clean copy of the mapping, or raise MappingError.

    With the file's header, every column the mapping uses must be in it, unless it is optional.
    """
    mapping = load(mapping)
    clean = {}

    if mapping.get("delimiter") is not None:
        if mapping["delimiter"] not in (",", ";", "\t", "|"):
            raise MappingError(_("Unknown delimiter."))
        clean["delimiter"] = mapping["delimiter"]
    if mapping.get("header_row") is not None:
        header_row = mapping["header_row"]
        if not isinstance(header_row, int) or isinstance(header_row, bool) or not 0 <= header_row <= MAX_HEADER_ROW:
            raise MappingError(_("The header row must be a number from 0 to {0}.").format(MAX_HEADER_ROW))
        clean["header_row"] = header_row

    clean["number_format"] = mapping.get("number_format") or "de"
    if clean["number_format"] not in NUMBER_FORMATS:
        raise MappingError(_("Unknown number format."))
    clean["date_format"] = mapping.get("date_format") or DATE_FORMATS[0]
    if clean["date_format"] not in DATE_FORMATS:
        raise MappingError(_("Unknown date format."))

    columns = mapping.get("columns") or {}
    if not isinstance(columns, dict) or len(columns) > MAX_COLUMNS:
        raise MappingError(_("The column mapping must be an object."))
    clean["columns"] = {}
    for target, column in columns.items():
        if target not in COLUMN_TARGETS:
            raise MappingError(_("Unknown field: {0}").format(target))
        if column:
            clean["columns"][target] = _text(column, _(COLUMN_TARGETS[target]))

    mapped = clean["columns"]
    if "date" not in mapped:
        raise MappingError(_("Choose the column with the date."))
    if "amount" in mapped and ({"deposit", "withdrawal"} & set(mapped)):
        raise MappingError(_("Choose either one signed amount column or deposit and withdrawal columns."))
    if not ({"amount", "deposit", "withdrawal"} & set(mapped)):
        raise MappingError(_("Choose the column with the amount, or the deposit and withdrawal columns."))
    if "party_name" in mapped and ({"party_name_deposit", "party_name_withdrawal"} & set(mapped)):
        raise MappingError(_("Choose either one party name column or the payer and payee columns."))

    description = mapping.get("description") or {}
    if not isinstance(description, dict):
        raise MappingError(_("The description must be an object."))
    formatter = description.get("formatter") or "joined"
    if formatter not in DESCRIPTION_FORMATTERS:
        raise MappingError(_("Unknown description format."))
    clean["description"] = {
        "columns": _columns(description.get("columns") or [], _("Description")),
        "formatter": formatter,
    }

    fee = mapping.get("fee")
    if fee:
        if not isinstance(fee, dict):
            raise MappingError(_("The fee must be an object."))
        clean["fee"] = {
            "column": _text(fee.get("column"), _("Fee")),
            "party_name": _text(fee.get("party_name") or "", _("Fee party name"), allow_empty=True),
            "description": _text(fee.get("description") or "Fee {id}", _("Fee description")),
            "reference": _text(fee.get("reference") or "{id}", _("Fee reference")),
        }

    filters = mapping.get("filters") or []
    if not isinstance(filters, list) or len(filters) > MAX_FILTERS:
        raise MappingError(_("At most {0} row filters are allowed.").format(MAX_FILTERS))
    clean["filters"] = []
    for row_filter in filters:
        if not isinstance(row_filter, dict) or row_filter.get("op") not in FILTER_OPS:
            raise MappingError(_("Unknown row filter."))
        clean["filters"].append({
            "column": _text(row_filter.get("column"), _("Filter column")),
            "op": row_filter["op"],
            "value": _text(row_filter.get("value") or "", _("Filter value"), allow_empty=True),
        })

    id_recipe = mapping.get("id")
    if id_recipe:
        if not isinstance(id_recipe, dict):
            raise MappingError(_("The transaction id recipe must be an object."))
        clean["id"] = {"seed": _text(id_recipe.get("seed"), _("Id seed"))}
        if id_recipe.get("columns") is not None:
            clean["id"]["columns"] = _columns(id_recipe["columns"], _("Id columns"))

    clean["optional_columns"] = _columns(mapping.get("optional_columns") or [], _("Optional columns"))

    if header is not None:
        missing = [column for column in required_columns(clean) if column not in header]
        if missing:
            raise MappingError(_("These columns are not in the file: {0}").format(", ".join(missing)))
    return clean


def used_columns(mapping: dict) -> list[str]:
    """Return every column the mapping reads, in a stable order without repeats."""
    columns = list(mapping["columns"].values()) + mapping["description"]["columns"]
    if mapping.get("fee"):
        columns.append(mapping["fee"]["column"])
    columns += [row_filter["column"] for row_filter in mapping["filters"]]
    columns += (mapping.get("id") or {}).get("columns") or []
    return list(dict.fromkeys(columns))


def required_columns(mapping: dict) -> list[str]:
    """Return the columns a file must have for this mapping."""
    optional = set(mapping.get("optional_columns") or [])
    return [column for column in used_columns(mapping) if column not in optional]
