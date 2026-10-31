# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Whether a FinTS dialog can be sent on, and what to do when it cannot.

Its own module because these are two plain predicates about an object's state
and nothing else -- no bank, no library, no session. That is what makes them
testable without either, which for the rule that decides whether a payment run
starts at all is the difference between an assertion and a hope.

What they are for:

    Could not load sepa accounts with error:
    Cannot send on dialog that is not open

reported for one login after another of the same bank, and read by everybody as
a bank problem. It was ours. python-fints' resume_dialog() has no try/finally::

    self._standing_dialog = FinTSDialog.create_resume(self, dialog_data)
    with self._standing_dialog:          # __exit__ ends the dialog
        yield self
    self._standing_dialog = None         # skipped when the block raises

The block raises on the ordinary path -- a parked TAN that comes back
unanswered raises -- so the client keeps a reference to an ENDED dialog.
Nothing clears it, and inside a fetch session that client is shared by every
login of the bank access, so each of them in turn joins the wreck.
"""

# No import of frappe at module level, deliberately. The docstring above says
# these are two predicates about an object's state and nothing else, and that
# claim is only worth anything if the module can be imported without a site --
# otherwise the tests that check the rule cannot run, which is exactly what
# happened: eight of them errored out on the import for as long as it stood
# here. The one place frappe is wanted is a log line, and a log line asks for
# it where it writes.


def dialog_is_usable(conn):
    """May a command be sent on this connection's standing dialog?

    A standing dialog is not the same thing as a usable one, and the two ways
    it can be unusable want opposite treatment:

        open is False   it has been ended. Dead; drop it.
        paused is True  a TAN request parked it and somebody is answering in
                        their banking app. Very much alive; leave it.
    """
    dialog = getattr(conn, "_standing_dialog", None)
    if dialog is None:
        return False
    if getattr(dialog, "paused", False):
        return False
    return bool(getattr(dialog, "open", False))


def discard_unusable_dialog(conn):
    """Forget an ENDED dialog that is still registered, so a fresh one opens.

    Only the ended case. A paused dialog is somebody's half-finished
    authentication: ending it means the release, when it comes, unlocks
    nothing, and the next attempt starts another challenge -- a loop the user
    cannot get out of by trying harder.

    Reaching into _standing_dialog is not something to be proud of. The library
    sets it and, on one path, forgets to clear it, and offers no public way to
    ask. The alternative is a bank access that stays broken for the rest of the
    run.

    :return: True when a dead dialog was dropped
    """
    dialog = getattr(conn, "_standing_dialog", None)
    if dialog is None or getattr(dialog, "paused", False):
        return False
    if getattr(dialog, "open", False):
        return False

    conn._standing_dialog = None
    try:
        import frappe

        frappe.logger("kefiya").info(
            "Kefiya: dropped an ended FinTS dialog that was still registered")
    except Exception:
        pass
    return True
