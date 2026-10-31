# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A stored connection state that cannot build a dialog must not be kept.

The trap it made is a closed loop. A send whose dialog initialisation fails
persists the state it ended up with; that state has no bank parameters; the
next attempt restores it, python-fints answers "could not fetch BPD", and the
same empty state is written again. The access is then unusable for good and
the only way out is emptying the field by hand.

Both ends are closed here: an empty BPD is never persisted over a good state,
and a state that already went bad is discarded when it surfaces -- for the
sibling logins too, because they share it.

Read as source text: fints_controller imports frappe at the top and never
loads outside a bench, and a test that cannot run is worse than none.
"""

import os
import re
import unittest


def _read(*parts):
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), *parts)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _block(source, header):
    """One def, by indentation."""
    # [ \t]* and not \s*: \s matches newlines, so the search happily started
    # on a blank line above and measured the indent of nothing.
    hit = re.search(r"^([ \t]*)def %s\(" % re.escape(header), source, re.M)
    assert hit, header
    indent = len(hit.group(1))
    lines = source[hit.start():].split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
    return "\n".join(out)


class TestAnEmptyBpdIsNeverPersisted(unittest.TestCase):

    def setUp(self):
        self.py = _read("utils", "fints_controller.py")

    def test_the_guard_stands_before_the_write(self):
        body = _block(self.py, "_persist_fints_state")
        self.assertIn("_has_bank_parameters(self.fints_connection)", body)
        self.assertLess(
            body.index("_has_bank_parameters"),
            body.index("deconstruct"),
            "Asking after writing would defeat the purpose.")

    def test_an_unreadable_library_shape_behaves_as_before(self):
        """A version this cannot inspect must persist, as it always did.
        Guessing "empty" there would stop storing state at all."""
        body = _block(self.py, "_has_bank_parameters")
        self.assertEqual(body.count("return True"), 3, body)
        self.assertIn("except Exception:", body)


class TestAPoisonedStateDiscardsItself(unittest.TestCase):

    def setUp(self):
        self.py = _read("utils", "fints_controller.py")

    def test_only_this_one_failure_is_caught(self):
        """FinTSClientError covers a dozen unrelated things; everything else
        has to keep propagating."""
        body = _block(self.py, "_is_missing_bank_parameters")
        self.assertIn("could not fetch BPD", body)
        send = _block(self.py, "submit_sepa_transfer")
        self.assertIn("if not _is_missing_bank_parameters(exc):", send)
        self.assertIn("raise", send)

    def test_it_is_discarded_and_not_retried(self):
        """The segments were already on the wire. A second send is the one
        mistake that costs real money."""
        send = _block(self.py, "submit_sepa_transfer")
        self.assertIn("self._forget_client_state()", send)
        self.assertIn("Before sending again, look in your online banking",
                      send)

    def test_the_siblings_lose_it_too(self):
        """They share the state, so clearing one hands it straight back."""
        body = _block(self.py, "_forget_client_state")
        self.assertIn("self.kefiya_login.discard_connection_state()", body)
        doc = _read("kefiya", "doctype", "kefiya_login", "kefiya_login.py")
        self.assertIn("self.sibling_logins()",
                      _block(doc, "discard_connection_state"))

    def test_one_list_for_both_ends(self):
        """They had drifted apart, and the raw one wrote None into a Check
        column -- MariaDB refuses that, and the discard broke off with
        error 1048 while an order was already on the wire. One list now, in
        fints_state_fields, checked against the doctype without a bench."""
        doc = _read("kefiya", "doctype", "kefiya_login", "kefiya_login.py")
        verwerfen = _block(doc, "discard_connection_state")
        self.assertIn("fints_state_fields.CLEARED", verwerfen)
        self.assertIn("fints_state_fields.cleared()", verwerfen)
        self.assertNotIn('"stored_tan_state_decoupled": None', verwerfen)


    def test_the_discard_is_committed(self):
        """The throw that follows rolls the transaction back; without its own
        commit the discarded state comes back with it."""
        body = _block(self.py, "_forget_client_state")
        self.assertIn("frappe.db.commit()", body)


class TestTheButtonDoesTheSameThing(unittest.TestCase):
    """"Reset Connection" cleared one row of fifteen. The state belongs to
    the bank access, so the next fetch borrowed the discarded one back from
    a sibling (_seed_client_state_from_sibling) and the button looked like
    it had done nothing. Both ends now take the same route."""

    def setUp(self):
        self.doc = _read("kefiya", "doctype", "kefiya_login",
                         "kefiya_login.py")

    def test_the_button_reaches_the_siblings(self):
        self.assertIn("siblings=True", _block(self.doc, "reset_connection"))

    def test_and_the_selection_rule_is_shared(self):
        """One rule for what a sibling is -- in login_siblings, frappe-free,
        so the document and the controller ask the same question."""
        self.assertIn("login_siblings.same_access(",
                      _block(self.doc, "sibling_logins"))
        controller = _read("utils", "fints_controller.py")
        self.assertIn("login_siblings.same_access(",
                      _block(controller, "_sibling_login_filters"))

    def test_but_the_account_of_a_sibling_stays(self):
        """account_iban says which account a login fetches. Pulling it out
        from under a sibling would break it. clear_fints_caches may do it
        to this one row, because a person pressed the button -- and that is
        why it lives there and not in discard_connection_state."""
        from kefiya.utils import fints_state_fields
        self.assertNotIn("account_iban", fints_state_fields.CLEARED)
        self.assertNotIn("iban_list", fints_state_fields.CLEARED)
        # Beide stehen in IDENTITY, und clear_fints_caches leert genau
        # diese Liste -- seit die Kundensystem-ID als drittes dazugehoert
        # und die Aufzaehlung an eine Stelle gewandert ist.
        self.assertIn("account_iban", fints_state_fields.IDENTITY)
        self.assertIn("iban_list", fints_state_fields.IDENTITY)
        leeren = _block(self.doc, "clear_fints_caches")
        self.assertIn("for fieldname in fints_state_fields.IDENTITY:", leeren)
        self.assertIn("self.set(fieldname, None)", leeren)
        # Auf die Zuweisung, nicht auf den Namen: der Docstring dort
        # erklaert gerade, warum account_iban stehen bleibt, und ein Test,
        # der den Namen sucht, findet die Erklaerung statt des Codes.
        verwerfen = _block(self.doc, "discard_connection_state")
        self.assertNotIn("self.account_iban =", verwerfen)
        self.assertNotIn("self.iban_list =", verwerfen)


class TestTheMessageSaysWhatReallyHappened(unittest.TestCase):
    """The discard can fail -- it did, on 05.10.2026. Saying it succeeded
    anyway sends the user straight back into the same wall, and the wall is
    a bank access that can no longer send."""

    def setUp(self):
        self.py = _read("utils", "fints_controller.py")

    def test_the_discard_reports_whether_it_worked(self):
        body = _block(self.py, "_forget_client_state")
        self.assertIn("return True", body)
        self.assertIn("return False", body)
        # The False belongs to the except branch, not to some early exit.
        self.assertGreater(body.index("return False"),
                           body.index("except Exception:"))

    def test_the_send_asks_before_it_promises(self):
        send = _block(self.py, "submit_sepa_transfer")
        self.assertIn("if self._forget_client_state():", send)

    def test_and_names_the_way_out_when_it_failed(self):
        send = _block(self.py, "submit_sepa_transfer")
        self.assertIn("could NOT be discarded", send)
        self.assertIn("Reset Connection", send)

    def test_the_warning_stands_in_both_cases(self):
        """Whatever became of the stored state, the segments were on the
        wire. Looking before sending again is the part that must not depend
        on it."""
        send = _block(self.py, "submit_sepa_transfer")
        self.assertEqual(
            send.count("Before sending again, look in your online banking"), 1)
        hint = send.index("Before sending again, look in your online banking")
        self.assertLess(send.index("could NOT be discarded"), hint)
        self.assertLess(send.index("has been discarded"), hint)


class TestTheDeviceSurvivesTheDiscard(unittest.TestCase):
    """Die Kundensystem-ID ist die Kennung, unter der die Bank diese
    Installation als Geraet kennt. Sie lag nur im Blob stored_client_state,
    und der wird bei jedem Dialogfehler verworfen -- fuer alle
    Geschwister-Logins des Zugangs mit. Danach schickt python-fints

        dialog.init(HKSYN3(SynchronizationMode.NEW_SYSTEM_ID))

    und die Bank sieht ein neues Geraet: volle Authentifizierung, und die
    90 Tage, in denen PSD2 den Abruf ohne TAN erlaubt, fangen bei null an.
    An einem Zugang mit fuenfzehn Logins kostete ein Fehlschlag die
    TAN-Freiheit von fuenfzehn Konten.

    Eine Umsatzabfrage selbst braucht dagegen nie eine TAN -- das sagt die
    Bank in HIPINS je Geschaeftsvorfall, und die Bibliothek haelt sich
    daran. Die TAN galt immer nur der Anmeldung.
    """

    def setUp(self):
        self.py = _read("utils", "fints_controller.py")

    def test_it_is_handed_to_the_client(self):
        body = _block(self.py, "__init_fints_connection")
        self.assertIn("system_id=fints_state_fields.usable_system_id(", body)

    def test_a_restored_blob_still_wins(self):
        """set_data laeuft nach dem Konstruktor, also sticht ein frischer
        Zustand die aufgehobene ID. Beides mitzugeben ist deshalb sicher --
        aber nur, solange from_data auch wirklich mitgeht."""
        body = _block(self.py, "__init_fints_connection")
        self.assertIn("from_data=self.kefiya_login.stored_client_blob,", body)

    def test_it_is_written_down_beside_the_blob(self):
        body = _block(self.py, "_persist_fints_state")
        self.assertIn("fints_state_fields.SYSTEM_ID_FIELD", body)
        self.assertIn('getattr(self.fints_connection, "system_id", None)',
                      body)

    def test_and_outside_the_bpd_guard(self):
        """Eine ID, die die Bank schon vergeben hat, gilt auch dann, wenn
        der Dialog sonst nichts Brauchbares hinterlassen hat. Stuende der
        Schreibvorgang im BPD-Zweig, ginge sie genau in dem Fall verloren,
        fuer den sie gedacht ist."""
        body = _block(self.py, "_persist_fints_state")
        zweig = body.index("_has_bank_parameters(self.fints_connection)")
        schreiben = body.index("fints_state_fields.SYSTEM_ID_FIELD")
        eingerueckt = [
            line for line in body[zweig:schreiben].split("\n")[1:]
            if line.strip()]
        self.assertTrue(
            any(not line.startswith("            ") for line in eingerueckt),
            "der Schreibvorgang haengt noch im if-Zweig")

    def test_the_zero_is_never_kept(self):
        """'0' heisst 'die Bank hat noch keine vergeben'."""
        from kefiya.utils import fints_state_fields

        self.assertIsNone(fints_state_fields.usable_system_id(
            fints_state_fields.SYSTEM_ID_UNASSIGNED))

    def test_an_automatic_discard_leaves_it_alone(self):
        from kefiya.utils import fints_state_fields

        self.assertNotIn(fints_state_fields.SYSTEM_ID_FIELD,
                         fints_state_fields.CLEARED)

    def test_but_the_button_takes_it(self):
        """Wer 'Verbindung zuruecksetzen' drueckt, will das Geraet neu
        anmelden -- und bezahlt das mit einer TAN. Das ist der Sinn."""
        login = _read("kefiya", "doctype", "kefiya_login", "kefiya_login.py")
        body = _block(login, "clear_fints_caches")
        self.assertIn("for fieldname in fints_state_fields.IDENTITY:", body)

    def test_the_access_shares_one_device(self):
        """Die ID gehoert dem Zugang, nicht dem einzelnen Login."""
        geerbt = _block(self.py, "_seed_client_state_from_sibling")
        self.assertIn("fints_state_fields.SYSTEM_ID_FIELD", geerbt)
        geteilt = _block(self.py, "_propagate_client_state_to_siblings")
        self.assertIn("fints_state_fields.SYSTEM_ID_FIELD", geteilt)

    def test_sharing_never_empties_a_sibling(self):
        """Ein Geschwister ohne eigene ID wuerde sich sonst bei der Bank neu
        anmelden -- genau der Fehler, den das hier verhindern soll."""
        body = _block(self.py, "_propagate_client_state_to_siblings")
        self.assertIn("if meine_system_id:", body)
