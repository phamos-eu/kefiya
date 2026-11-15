# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Find the mapping for a file: the built-in formats and the saved templates."""

import frappe

from kefiya.utils.statement_import import mapping as statement_mapping
from kefiya.utils.statement_import import reader
from kefiya.utils.statement_import.presets import PRESET_HEADERS, PRESETS

BUILTIN_PREFIX = "builtin:"


def get_candidates() -> list[dict]:
    """Return every known mapping as {key, label, builtin, header, mapping}, saved templates first."""
    candidates = []
    for template in frappe.get_all(
        "Kefiya Statement Import Template", fields=["name", "header_columns", "mapping"], order_by="modified desc"
    ):
        try:
            clean = statement_mapping.validate(template.mapping)
        except statement_mapping.MappingError:
            continue
        candidates.append({
            "key": template.name,
            "label": template.name,
            "builtin": False,
            "header": [line for line in (template.header_columns or "").splitlines() if line],
            "mapping": clean,
        })
    for name, preset in PRESETS.items():
        candidates.append({
            "key": BUILTIN_PREFIX + name,
            "label": name,
            "builtin": True,
            "header": PRESET_HEADERS[name],
            "mapping": statement_mapping.validate(preset),
        })
    return candidates


def get_candidate(key: str) -> dict | None:
    return next((candidate for candidate in get_candidates() if candidate["key"] == key), None)


def suggest(rawdata: bytes, candidates: list[dict] | None = None) -> dict | None:
    """Return the best mapping for this file, with "header_row" set, or None.

    A mapping fits when one of the first lines has every column it needs. A header equal to
    the one the template was made from wins, then saved templates over the built-in ones.
    """
    text, _encoding = reader.decode(rawdata)
    best, best_rank = None, None
    for position, candidate in enumerate(candidates if candidates is not None else get_candidates()):
        clean = candidate["mapping"]
        delimiter = clean.get("delimiter") or reader.sniff_delimiter(text)
        rows = reader.records("\n".join(text.splitlines()[: reader.SNIFF_RECORDS]), delimiter)
        header_row = reader.find_header_row(rows, statement_mapping.required_columns(clean))
        if header_row is None:
            continue
        header = [cell.strip() for cell in rows[header_row][1]]
        rank = (header != candidate["header"], candidate["builtin"], position)
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = dict(candidate, mapping=dict(clean, delimiter=delimiter, header_row=header_row))
    return best
