# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Every .py in this repository has to compile. Frappe Cloud says so.

The release check compiles the whole tree, and a file it cannot parse fails
the build with "Validation Failed: Invalid app release" -- no deploy, for
anybody, until it is fixed.

That is not hypothetical. A patch for the Finanzübersicht was committed as
docs/finanzuebersicht/1-server-script-fc_cockpit_data.py. A patch is made of
fragments from the middle of a function, so the file starts on an indented
line; as a module that is an IndentationError, and it blocked a release. The
fragments live in Markdown now.

So the test covers the WHOLE repository, not just the app package: the file
that broke it was under docs/, which no test would otherwise have looked at.

Parsing is not enough, and that cost a transfer. When the TAN path moved into
fints_tan_session.py, four names came along and their imports did not:
NeedTANResponse, NeedRetryResponse, InitFailedException,
TanInteractionRequired. The file parses -- Python resolves names when the line
runs, not when the module loads -- so every test here passed, the release
built, the deploy went out, and the first person to press "Senden" got
``NameError: name 'NeedTANResponse' is not defined`` after the bank had
already been asked for a TAN.

The second test below is the answer: a name used in a file has to be bound
somewhere in that file, or imported into it, or be a builtin. It is a flat
check -- one set of every binding anywhere in the module -- so it never
reports a name that exists, and it reports every name that exists nowhere.
"""

import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

#: Directories the release check has no business in either.
SKIP = {".git", "__pycache__", "node_modules", ".github", "env", "venv"}


def _python_files():
    for base, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(base, name)


class TestTheReleaseCanBeBuilt(unittest.TestCase):

    def test_every_python_file_parses(self):
        import ast

        broken = []
        for path in _python_files():
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            try:
                ast.parse(source, filename=path)
            except SyntaxError as exc:
                broken.append("{0}: {1}".format(
                    os.path.relpath(path, REPO), exc))

        self.assertEqual(
            broken, [],
            "Frappe Cloud compiles the whole repository at release time and "
            "refuses the build over any one of these. A file that is not "
            "meant to be a module -- a patch, a snippet, an example -- must "
            "not carry the .py extension.")

    def test_the_walk_actually_reaches_the_app(self):
        """A guard that walks nothing passes everything."""
        found = {os.path.relpath(p, REPO) for p in _python_files()}
        self.assertIn(os.path.join("kefiya", "hooks.py"), found)
        self.assertIn(os.path.join("kefiya", "utils", "client.py"), found)
        self.assertGreater(len(found), 50, found)


#: Names Python puts into a module without anybody importing them.
MODULE_GIVENS = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__path__", "__debug__", "__class__",
}


def _bindings(tree):
    """Every name this module binds, anywhere, by any means.

    Flat on purpose. A per-scope walk would find more -- a name bound in one
    function and read in another -- but it would also have to model closures,
    comprehensions and class bodies correctly to avoid crying wolf, and a
    guard that cries wolf gets deleted. This one reports only names that are
    bound in NO scope at all, which is exactly the mistake an extraction
    makes.
    """
    import ast

    bound = set(MODULE_GIVENS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, ast.arguments):
            for arg in (list(node.posonlyargs) + list(node.args)
                        + list(node.kwonlyargs)):
                bound.add(arg.arg)
            for arg in (node.vararg, node.kwarg):
                if arg:
                    bound.add(arg.arg)
    return bound


def _names_read(tree):
    import ast

    return {node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


class TestEveryNameHasASource(unittest.TestCase):
    """A name the file never binds and never imports is a NameError waiting.

    See the module docstring: this is the test that was missing when the TAN
    path moved house, and it is the one that would have held the deploy back
    instead of letting the bank ask for a TAN nobody could answer.
    """

    def test_no_file_reads_a_name_from_nowhere(self):
        import ast
        import builtins

        known = set(dir(builtins))
        homeless = []
        for path in _python_files():
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            missing = _names_read(tree) - _bindings(tree) - known
            if missing:
                homeless.append("{0}: {1}".format(
                    os.path.relpath(path, REPO), ", ".join(sorted(missing))))

        self.assertEqual(
            homeless, [],
            "These names are used but bound nowhere in their file and are not "
            "builtins. The file still parses and still deploys; it raises "
            "NameError the first time the line runs, which on this app means "
            "in front of a bank.")

    def test_the_check_would_have_caught_the_transfer_failure(self):
        """The exact shape of the bug, run through the exact check."""
        import ast
        import builtins

        wie_es_war = ("def _settle_tan(self, response):\n"
                      "    if not isinstance(response, NeedTANResponse):\n"
                      "        return response, None\n")
        tree = ast.parse(wie_es_war)
        missing = _names_read(tree) - _bindings(tree) - set(dir(builtins))
        self.assertEqual(missing, {"NeedTANResponse"})

        wie_es_ist = "from fints.client import NeedTANResponse\n" + wie_es_war
        tree = ast.parse(wie_es_ist)
        missing = _names_read(tree) - _bindings(tree) - set(dir(builtins))
        self.assertEqual(missing, set())
