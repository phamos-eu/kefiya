# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""The bank's refusals live in a module both controllers can import.

They came from the original inside fints_controller.py, and the legacy
controller imported them from there. A module that both controllers need
must not import either of them -- CLAUDE.md, "Zirkulaere Importe".
"""

import ast
import os
import unittest

UTILS = os.path.join(os.path.dirname(__file__), "..", "..", "utils")


def _imports(name):
    with open(os.path.join(UTILS, name), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.add(("." * node.level) + (node.module or ""))
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


class TestTheSharedModuleStandsAlone(unittest.TestCase):

    def test_it_imports_no_controller(self):
        for module in _imports("fints_bank_errors.py"):
            self.assertNotIn("fints_controller", module)

    def test_the_legacy_controller_takes_it_from_there(self):
        imported = _imports("fints_controller_legacy.py")
        self.assertIn(".fints_bank_errors", imported)
        self.assertNotIn(".fints_controller", imported)

    def test_the_controller_passes_the_names_on(self):
        """Existing callers import them from fints_controller."""
        with open(os.path.join(UTILS, "fints_controller.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("from kefiya.utils.fints_bank_errors import (", source)
        for name in ("LOGIN_ERRORS", "FetchFailed", "explain_login_rejection",
                     "log_bank_traffic_on_error", "fetch_fints_transactions"):
            self.assertIn("    {0},\n".format(name), source)


if __name__ == "__main__":
    unittest.main()
