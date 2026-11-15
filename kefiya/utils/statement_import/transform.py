# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Turn the raw rows of a statement file into transactions, by a validated mapping.

A transaction is a dict with the keys date, amount (signed), party_name, description and
transaction_id, plus currency, reference_number, party_iban, party_account_number and
transaction_type when the mapping has them.
"""

import hashlib
import re
from datetime import datetime

from frappe import _

# A dot only separates thousands (groups of three digits), the comma the decimals
GERMAN_AMOUNT = re.compile(r"[-+]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?")
ENGLISH_AMOUNT = re.compile(r"[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?")

# Currency signs, codes and spaces around an amount, such as "12,50 €" or "EUR -3,20"
AMOUNT_DECORATION = re.compile(r"^[^\d.,+-]+|[^\d.,+-]+$")

# Bank Transaction.transaction_id is a Data field
MAX_ID_LENGTH = 140


def parse_german_amount(amount: str) -> float:
    """Parse '-1.234,56', '1.500', '1234' or '12,5' to a float.

    Raises ValueError for anything else, such as '12.50': read as German it would be
    1250, but it is more likely a decimal point, so it is refused rather than guessed.
    """
    amount = amount.strip()
    if not GERMAN_AMOUNT.fullmatch(amount):
        raise ValueError(f"Not a German amount: {amount!r}")
    return float(amount.replace(".", "").replace(",", "."))


def parse_english_amount(amount: str) -> float:
    """Parse '-1,234.56', '1,500', '1234' or '12.5' to a float; '12,50' raises ValueError."""
    amount = amount.strip()
    if not ENGLISH_AMOUNT.fullmatch(amount):
        raise ValueError(f"Not an amount with a decimal point: {amount!r}")
    return float(amount.replace(",", ""))


def parse_amount(text: str, number_format: str) -> float:
    """Parse an amount cell; a currency sign or code around it is allowed, a trailing minus too."""
    text = (text or "").replace("\xa0", " ").strip()
    core = AMOUNT_DECORATION.sub("", text)
    if core.endswith("-") and not core.startswith(("-", "+")):
        core = "-" + core[:-1].strip()
    parse = parse_german_amount if number_format == "de" else parse_english_amount
    try:
        return parse(core)
    except ValueError:
        raise ValueError(_("Not an amount: {0}").format(text or _("(empty)")))


def describe(values: list[str], formatter: str) -> str:
    """Join the description columns. Two formatters keep the texts of the earlier DKB and PayPal imports."""
    if formatter == "dkb_visa" and values:
        # "MUSTER CAFE (Im Geschäft, -52,30 $)"
        details = [value.replace("\xa0", " ").strip() for value in values[1:]]
        return "{0} ({1})".format(values[0], ", ".join(d for d in details if d))
    if formatter == "paypal" and values:
        # "Rückzahlung, Bestellung AB10001, zu 1AB23456CD789012E": the last column is the related code
        related = values[-1]
        parts = values[:-1] + ["zu {0}".format(related) if related else ""]
        return ", ".join(p for p in parts if p)
    return ", ".join(v for v in values if v)


def short_id(value: str) -> str:
    """Keep an id that fits the field; hash a longer one, so it stays unique and stable."""
    return value if len(value) <= MAX_ID_LENGTH else hashlib.md5(value.encode()).hexdigest()


def id_columns(mapping: dict) -> list[str]:
    """Return the columns a generic transaction id is made of: every column the mapping reads, except filters."""
    columns = list(mapping["columns"].values()) + mapping["description"]["columns"]
    if mapping.get("fee"):
        columns.append(mapping["fee"]["column"])
    return list(dict.fromkeys(columns))


class Transform:
    """Apply one mapping to the rows of one file."""

    def __init__(self, mapping: dict, header: list[str]):
        self.mapping = mapping
        self.header = header
        # The first column of a name wins, if a file repeats one
        self.index = {}
        for position, name in enumerate(header):
            self.index.setdefault(name, position)
        self.occurrences = {}
        self.seen_ids = set()

    def cell(self, row: list[str], column: str | None, raw=False) -> str:
        if not column:
            return ""
        position = self.index.get(column)
        if position is None or position >= len(row):
            return ""
        return row[position] if raw else row[position].strip()

    def column(self, row, target) -> str:
        return self.cell(row, self.mapping["columns"].get(target))

    def is_skipped(self, row) -> bool:
        """Rows without a date, sum lines and rows a filter excludes are skipped silently.

        A sum line ("Total;;;1.234,56" or "Total;;;") has no date; one with a date but no
        amount is a broken booking and is reported, not skipped.
        """
        if not self.column(row, "date"):
            return True
        if not self.has_amount(row):
            try:
                datetime.strptime(self.column(row, "date"), self.mapping["date_format"])
            except ValueError:
                return True
        for row_filter in self.mapping["filters"]:
            equal = self.cell(row, row_filter["column"]) == row_filter["value"]
            if equal == (row_filter["op"] == "equals"):
                return True
        return False

    def has_amount(self, row) -> bool:
        return any(self.column(row, target) for target in ("amount", "deposit", "withdrawal"))

    def amount(self, row) -> float:
        number_format = self.mapping["number_format"]
        columns = self.mapping["columns"]
        if "amount" in columns:
            return parse_amount(self.column(row, "amount"), number_format)
        deposit, withdrawal = self.column(row, "deposit"), self.column(row, "withdrawal")
        if not deposit and not withdrawal:
            raise ValueError(_("No amount"))
        # Withdrawal columns hold "-12,50" in some files and "12,50" in others
        return (abs(parse_amount(deposit, number_format)) if deposit else 0) - (
            abs(parse_amount(withdrawal, number_format)) if withdrawal else 0
        )

    def transaction_id(self, row) -> str:
        if "transaction_id" in self.mapping["columns"]:
            value = self.column(row, "transaction_id")
            if not value:
                raise ValueError(_("{0} is missing").format(self.mapping["columns"]["transaction_id"]))
            return short_id(value)

        # Identical rows (same day, party and amount) get a running number, so they keep apart
        # and still get the same id in an overlapping export.
        # Without a recipe the mapped columns count: a column a later export adds or changes
        # (a balance, a status) then does not change the id.
        recipe = self.mapping.get("id") or {"seed": "Kefiya"}
        columns = recipe.get("columns") or id_columns(self.mapping)
        key = tuple(self.cell(row, column, raw=True) for column in columns)
        self.occurrences[key] = self.occurrences.get(key, 0) + 1
        return hashlib.md5(
            "{0},{1},{2}".format(recipe["seed"], ",".join(key), self.occurrences[key]).encode()
        ).hexdigest()

    def transactions(self, row) -> list[dict]:
        """Return the transactions of one row: the amount and, if the mapping has a fee column, the fee."""
        mapping, columns = self.mapping, self.mapping["columns"]
        date_text = self.column(row, "date")
        try:
            date = datetime.strptime(date_text, mapping["date_format"]).date()
        except ValueError:
            raise ValueError(_("Not a date in the format {0}: {1}").format(mapping["date_format"], date_text))
        amount = self.amount(row)
        fee = None
        if mapping.get("fee"):
            fee_text = self.cell(row, mapping["fee"]["column"])
            fee = parse_amount(fee_text, mapping["number_format"]) if fee_text else 0

        if "party_name" in columns:
            party_name = self.column(row, "party_name")
        else:
            # The counterparty is the payee of a withdrawal and the payer of a deposit
            party_name = self.column(row, "party_name_withdrawal" if amount < 0 else "party_name_deposit")

        base = {"date": date}
        if "currency" in columns:
            base["currency"] = self.column(row, "currency")

        transaction = dict(
            base,
            amount=amount,
            party_name=party_name,
            description=describe(
                [self.cell(row, column) for column in mapping["description"]["columns"]],
                mapping["description"]["formatter"],
            ),
        )
        for target, key in (
            ("party_iban", "party_iban"),
            ("reference_number", "reference_number"),
            ("party_account_number", "party_account_number"),
            ("transaction_type", "transaction_type"),
        ):
            if target in columns:
                transaction[key] = self.column(row, target)

        # Only now, so that a row that cannot be read does not use up a running number
        transaction["transaction_id"] = self.transaction_id(row)
        result = [transaction]

        if fee:
            code = transaction["transaction_id"]
            result.append(dict(
                base,
                amount=fee,
                party_name=mapping["fee"]["party_name"],
                description=mapping["fee"]["description"].replace("{id}", code),
                reference_number=mapping["fee"]["reference"].replace("{id}", code),
                transaction_id=short_id("{0}-fee".format(code)),
            ))

        for item in result:
            if item["transaction_id"] in self.seen_ids:
                raise ValueError(_("Transaction ID {0} is in the file twice").format(item["transaction_id"]))
        self.seen_ids.update(item["transaction_id"] for item in result)
        return result


def transform(table: dict, mapping: dict) -> dict:
    """Return {"transactions": [(file line, transaction)], "errors": [(file line, message)], "skipped": n}.

    `table` comes from reader.read, `mapping` from mapping.validate.
    """
    worker = Transform(mapping, table["header"])
    transactions, errors, skipped = [], [], 0
    for line, row in table["rows"]:
        if worker.is_skipped(row):
            skipped += 1
            continue
        try:
            transactions.extend((line, transaction) for transaction in worker.transactions(row))
        except (ValueError, TypeError, AttributeError) as e:
            errors.append((line, str(e)))
    return {"transactions": transactions, "errors": errors, "skipped": skipped}
