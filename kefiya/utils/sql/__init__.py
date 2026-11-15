# Copyright (c) 2026, phamos GmbH and contributors
# For license information, please see license.txt
"""The SQL queries Kefiya keeps as files in this folder."""

import os

FOLDER = os.path.dirname(__file__)
QUERIES = frozenset(name for name in os.listdir(FOLDER) if name.endswith(".sql"))


def read_query(name: str) -> str:
    """Return the query in the file `name` of this folder.

    Only the .sql files shipped in this folder can be read, so no other path
    reaches open().
    """
    if name not in QUERIES:
        raise ValueError(f"Unknown query: {name}")
    with open(os.path.join(FOLDER, name)) as f:  # nosemgrep
        return f.read()
