# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Read a CSV statement file into a header and raw rows."""

import csv
import io
from collections import Counter

import chardet

DELIMITERS = (",", ";", "\t", "|")

# Lines looked at to guess the delimiter and the header row
SNIFF_RECORDS = 30


def decode(rawdata: bytes) -> tuple[str, str]:
    """Return (text, encoding). UTF-8 (with or without BOM) first, else what chardet guesses."""
    try:
        return rawdata.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    guess = chardet.detect(rawdata).get("encoding")
    if guess:
        try:
            return rawdata.decode(guess), guess
        except (LookupError, UnicodeDecodeError):
            pass
    return rawdata.decode("cp1252", errors="replace"), "cp1252"


def records(text: str, delimiter: str) -> list[tuple[int, list[str]]]:
    """Return (file line, cells) per CSV record; the line is the record's last line, counted from 1.

    newline='' keeps line breaks inside quoted cells, so such a record spans several lines.
    """
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    return [(reader.line_num, row) for row in reader]


def sniff_delimiter(text: str) -> str:
    """Return the delimiter that splits the most lines into the same number (above one) of cells."""
    head = "\n".join(text.splitlines()[:SNIFF_RECORDS])
    best, best_score = DELIMITERS[0], 0
    for delimiter in DELIMITERS:
        counts = Counter(len(row) for _line, row in records(head, delimiter) if len(row) > 1)
        if not counts:
            continue
        width, lines = counts.most_common(1)[0]
        score = width * lines
        if score > best_score:
            best, best_score = delimiter, score
    return best


def sniff_header_row(rows: list[tuple[int, list[str]]]) -> int:
    """Return the index of the first record as wide as most records: the header, after any preamble."""
    widths = Counter(len(row) for _line, row in rows[:SNIFF_RECORDS] if len(row) > 1)
    if not widths:
        return 0
    width = widths.most_common(1)[0][0]
    for index, (_line, row) in enumerate(rows):
        if len(row) == width and all(cell.strip() for cell in row):
            return index
    return next(index for index, (_line, row) in enumerate(rows) if len(row) == width)


def find_header_row(rows: list[tuple[int, list[str]]], columns) -> int | None:
    """Return the index of the first record that has all these column names, or None."""
    wanted = set(columns)
    for index, (_line, row) in enumerate(rows[:SNIFF_RECORDS]):
        if wanted <= {cell.strip() for cell in row}:
            return index
    return None


def read(rawdata: bytes, delimiter: str | None = None, header_row: int | None = None) -> dict:
    """Return the file as {encoding, delimiter, header_row, header, rows}.

    `rows` holds (file line, cells) for every record below the header; empty lines are left out.
    Delimiter and header row are guessed when not given.
    """
    text, encoding = decode(rawdata)
    delimiter = delimiter or sniff_delimiter(text)
    all_rows = records(text, delimiter)
    if header_row is None:
        header_row = sniff_header_row(all_rows)
    header = [cell.strip() for cell in all_rows[header_row][1]] if header_row < len(all_rows) else []
    return {
        "encoding": encoding,
        "delimiter": delimiter,
        "header_row": header_row,
        "header": header,
        "rows": [(line, row) for line, row in all_rows[header_row + 1:] if row],
    }
