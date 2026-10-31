# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""An answer without a payee-check segment is the bank's to judge, not a crash.

Measured on 30.10.2026 at a comdirect account. The order went out as
HKVPP + HKCCS + HKTAN, and the answer carried no HIVPP. python-fints asks for
it with ``throw=True``, so the send ended in::

    FinTSNoResponseError: The bank's response did not contain a response to
    your request, please inspect debug log.

-- raised before anything read what the bank had said about the order or the
TAN. Nobody could tell whether it had refused, wanted a TAN, or simply not
run the check.

These tests EXECUTE the send path with stand-ins for the library, because a
source test only proves that the line is there.
"""

import unittest

from kefiya.utils import fints_vop_client


class Line:
    def __init__(self, code, text=""):
        self.code = code
        self.text = text
        self.parameters = []


class Answer:
    """A bank answer with no HIVPP and the given codes on the TAN segment."""

    def __init__(self, tan_codes):
        self.tan_codes = tan_codes
        self.tan_seg = None

    def find_segment_first(self, kind, throw=False):
        if throw:
            raise AssertionError("asked for the payee check with throw=True")
        return None

    def responses(self, segment, *codes):
        if segment is not self.tan_seg:
            return []
        return [Line(code) for code in self.tan_codes
                if not codes or code in codes]


class Dialog:
    def __init__(self, answer):
        self.answer = answer
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, *segments):
        self.sent.append(segments)
        self.answer.tan_seg = segments[-1]
        return self.answer


class NeedTANResponse:
    def __init__(self, command_seg, challenge, resume_func, structured,
                 decoupled, vop_result):
        self.command_seg = command_seg
        self.vop_result = vop_result
        self.decoupled = decoupled


class NeedVOPResponse:
    def __init__(self, **kwargs):
        raise AssertionError("nothing to park without a payee check")


class Base:
    def _find_vop_format_for_segment(self, seg):
        return "urn:example:vop"

    def _need_twostep_tan_for_segment(self, seg):
        return True

    def _get_tan_segment(self, seg, process, tan_request=None):
        return object()

    def is_challenge_structured(self):
        return False

    def _send_pay_with_possible_retry(self, dialog, seg, resume):
        raise AssertionError("the library's own method must not run")

    def send_tan(self, challenge, tan):
        pass

    def _get_dialog(self):
        pass

    def process_response_message(self, dialog, message, internal_send=True):
        pass


def segment(**kwargs):
    return ("segment", kwargs)


def client():
    parts = {"base": Base, "NeedTANResponse": NeedTANResponse,
             "NeedVOPResponse": NeedVOPResponse, "PSRD1": segment,
             "HKVPP1": segment, "HIVPP1": object(), "HKVPA1": segment,
             "FinTSClientError": Exception}
    return fints_vop_client._build(parts)()


class TestTheBankDecides(unittest.TestCase):

    def send(self, tan_codes):
        resumed = []
        result = client()._send_pay_with_possible_retry(
            Dialog(Answer(tan_codes)), object(),
            lambda seg, response: resumed.append(response) or "resumed")
        return result, resumed

    def test_a_tan_it_asks_for_is_put_to_the_user(self):
        result, resumed = self.send(["0030"])
        self.assertIsInstance(result, NeedTANResponse)
        self.assertIsNone(result.vop_result)
        self.assertEqual(resumed, [])

    def test_a_decoupled_release_is_put_to_the_user(self):
        result, _ = self.send(["3955"])
        self.assertIsInstance(result, NeedTANResponse)
        self.assertTrue(result.decoupled)

    def test_a_refusal_stops_the_order(self):
        with self.assertRaises(Exception) as caught:
            self.send(["9210"])
        self.assertIn("Error response", str(caught.exception))

    def test_anything_else_goes_to_the_reader_of_the_answer(self):
        """Where the controller reads the verdict and refuses or reports."""
        result, resumed = self.send([])
        self.assertEqual(result, "resumed")
        self.assertEqual(len(resumed), 1)

    def test_what_was_sent_is_written_down(self):
        connection = client()
        connection._send_pay_with_possible_retry(
            Dialog(Answer(["0030"])), object(), lambda seg, response: None)
        self.assertEqual(len(connection.last_sent), 3)


if __name__ == "__main__":
    unittest.main()
