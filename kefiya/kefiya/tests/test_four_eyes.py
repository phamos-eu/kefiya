# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Nobody approves a transfer they entered themselves.

Submit rights were separate from create rights, and the transfer's docstring
concluded from that that the person entering a transfer is not the person
releasing it. Nothing checked it: whoever held both rights could type a
transfer and approve it alone, and the outbox approved whole selections that
way. These tests pin the rule down where it is decided and where it is
enforced.

Most of this runs the code rather than reading it. enforce() is called against
a stand-in for frappe, so the test proves that the names resolve and that the
refusal is raised -- not only that the lines are there.
"""

import os
import sys
import types
import unittest

from kefiya.utils import four_eyes


class _Refused(Exception):
    pass


def _fake_frappe(session_user, stored_modified_by, ceiling, user_roles=(),
                 exempt_roles=()):
    """Just enough of frappe for enforce(): settings, one stored value, a
    session user, its roles, the exempt roles, _() and throw()."""
    fake = types.ModuleType("frappe")
    fake.get_roles = lambda: list(user_roles)
    fake.get_all = lambda doctype, filters=None, pluck=None: list(exempt_roles)
    fake.session = types.SimpleNamespace(user=session_user)
    fake.db = types.SimpleNamespace(
        get_single_value=lambda doctype, field: ceiling,
        get_value=lambda doctype, name, field: stored_modified_by,
    )

    def throw(message, title=None):
        raise _Refused(message)

    fake.throw = throw
    fake._ = lambda text: text
    return fake


class _Transfer:
    doctype = "Kefiya Transfer"
    name = "KT-0001"

    def __init__(self, owner, total, new=False):
        self.owner = owner
        self.total_amount = total
        self._new = new

    def is_new(self):
        return self._new


class TestWhoMayApprove(unittest.TestCase):

    def test_the_creator_may_not(self):
        self.assertEqual(four_eyes.conflict("a@x", "a@x", "a@x"),
                         four_eyes.ENTERED)

    def test_whoever_changed_it_last_may_not(self):
        """Editing someone else's draft and then approving it is entering
        it: the amount on it is yours."""
        self.assertEqual(four_eyes.conflict("b@x", "a@x", "b@x"),
                         four_eyes.CHANGED)

    def test_a_second_person_may(self):
        self.assertIsNone(four_eyes.conflict("b@x", "a@x", "a@x"))

    def test_a_draft_never_saved_has_only_its_creator(self):
        self.assertIsNone(four_eyes.conflict("b@x", "a@x", None))


class TestWhenTheRuleBinds(unittest.TestCase):

    def test_by_default_always(self):
        """A field that did not exist before reads as 0 or None. Both have to
        mean "four eyes", or the rule would be off on every site until
        someone saved the settings."""
        for ceiling in (0, None, "", "0"):
            self.assertTrue(four_eyes.applies(10, ceiling), ceiling)

    def test_a_ceiling_allows_small_transfers_alone(self):
        self.assertFalse(four_eyes.applies(99.99, 100))
        self.assertFalse(four_eyes.applies(100, 100))
        self.assertTrue(four_eyes.applies(100.01, 100))

    def test_an_unreadable_ceiling_does_not_open_the_door(self):
        self.assertTrue(four_eyes.applies(10, "a lot"))


class TestWhoIsReleased(unittest.TestCase):

    def test_a_listed_role_releases(self):
        self.assertTrue(four_eyes.may_approve_alone(["A", "GF"], ["GF"]))

    def test_an_empty_list_releases_nobody(self):
        self.assertFalse(four_eyes.may_approve_alone(["GF"], []))
        self.assertFalse(four_eyes.may_approve_alone(["GF"], None))

    def test_an_empty_row_releases_nobody(self):
        self.assertFalse(four_eyes.may_approve_alone([""], [""]))


class TestEnforceRuns(unittest.TestCase):
    """enforce() executed against a stand-in frappe."""

    def _run(self, session_user, owner, stored_modified_by, total=500,
             ceiling=0, new=False, user_roles=(), exempt_roles=()):
        saved = sys.modules.get("frappe")
        sys.modules["frappe"] = _fake_frappe(
            session_user, stored_modified_by, ceiling, user_roles,
            exempt_roles)
        try:
            four_eyes.enforce(_Transfer(owner, total, new=new))
        finally:
            if saved is None:
                del sys.modules["frappe"]
            else:
                sys.modules["frappe"] = saved

    def test_self_approval_is_refused(self):
        with self.assertRaises(_Refused) as ctx:
            self._run("a@x", "a@x", "a@x")
        self.assertIn("you entered this transfer", str(ctx.exception))

    def test_approving_what_you_just_edited_is_refused(self):
        with self.assertRaises(_Refused) as ctx:
            self._run("b@x", "a@x", "b@x")
        self.assertIn("last to change", str(ctx.exception))

    def test_a_second_person_passes(self):
        self._run("b@x", "a@x", "a@x")

    def test_below_the_ceiling_one_person_passes(self):
        self._run("a@x", "a@x", "a@x", total=50, ceiling=100)

    def test_above_the_ceiling_one_person_is_refused(self):
        with self.assertRaises(_Refused):
            self._run("a@x", "a@x", "a@x", total=150, ceiling=100)

    def test_a_role_released_from_the_rule_approves_alone(self):
        """The managing director types and releases a payment alone."""
        self._run("gf@x", "gf@x", "gf@x", total=50000,
                  user_roles=["Employee", "Managing Director"],
                  exempt_roles=["Managing Director"])

    def test_nobody_is_released_by_default(self):
        with self.assertRaises(_Refused):
            self._run("gf@x", "gf@x", "gf@x",
                      user_roles=["Managing Director"], exempt_roles=[])

    def test_another_role_does_not_release(self):
        with self.assertRaises(_Refused):
            self._run("a@x", "a@x", "a@x", user_roles=["Accounts User"],
                      exempt_roles=["Managing Director"])


def _source(*parts):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, *parts), encoding="utf-8") as handle:
        return handle.read()


class TestEveryApprovalPathPassesTheCheck(unittest.TestCase):
    """The outbox batch, "Approve and send", submit_kefiya_transfer and the
    form all submit the document. The check sits in before_submit so none of
    them can be the one that forgot it."""

    def test_before_submit_checks_first(self):
        source = _source("doctype", "kefiya_transfer", "kefiya_transfer.py")
        start = source.index("def before_submit(self):")
        body = source[start:source.index("def on_update_after_submit", start)]
        self.assertIn("four_eyes.enforce(self)", body)
        self.assertLess(body.index("four_eyes.enforce(self)"),
                        body.index("self.status ="),
                        "Refuse before the status says Approved.")
        self.assertIn("from kefiya.utils import four_eyes", source)

    def test_the_stored_editor_is_compared_not_the_approver(self):
        """By the time before_submit runs, the submit has already written the
        approver into modified_by. Only the stored value says who edited."""
        source = _source("..", "utils", "four_eyes.py")
        self.assertIn(
            'frappe.db.get_value(doc.doctype, doc.name, "modified_by")',
            source)

    def test_the_setting_exists_and_defaults_to_never(self):
        import json
        meta = json.loads(_source("doctype", "kefiya_settings",
                                  "kefiya_settings.json"))
        fields = {f["fieldname"]: f for f in meta["fields"]}
        self.assertIn("self_approval_up_to", fields)
        self.assertEqual(fields["self_approval_up_to"]["default"], "0")
        self.assertIn("self_approval_up_to", meta["field_order"])
        self.assertEqual(fields["self_approval_roles"]["options"],
                         "Kefiya Self Approval Role")
        self.assertNotIn("default", fields["self_approval_roles"],
                         "Which roles may approve alone is the site's call.")


if __name__ == "__main__":
    unittest.main()
