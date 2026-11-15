# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Read bank statement files and turn their rows into transactions by a column mapping.

Pure Python without Frappe documents, so every step can be tested on the sample files:

- reader: bytes -> header and raw rows (encoding, delimiter, header row)
- mapping: what a mapping may contain, checked before it is used
- transform: raw rows + mapping -> transactions and per-row errors
- presets: the built-in mappings for DKB and PayPal exports
"""
