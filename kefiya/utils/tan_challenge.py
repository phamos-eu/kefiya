# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""What the bank actually asks, so it can be put on the screen.

Some banks do not ask for a TAN in words. comdirect sends a photoTAN: a
coloured mosaic that the photoTAN app on the phone reads, and only then does it
show the six digits to type. Sparkassen do the same thing with chipTAN-QR. The
challenge is an IMAGE, and the prompt has to show it -- an input field with no
picture above it is a question nobody can answer.

Until now the prompt carried the TAN method and the account and nothing else.
The challenge object was right there in ask_for_tan() and was never passed on,
so on a comdirect access the box asked for a TAN and showed nothing to scan.

Two things arrive from the bank and both need care:

  challenge_hhduc  binary. For photoTAN and chipTAN-QR it is a small container
                   holding a MIME type and the image; for chipTAN optisch it is
                   flicker data, which is not an image at all. Which one it is
                   has to be decided by reading it, not by trusting the TAN
                   method name -- banks name their methods freely.

  challenge_html   text from the bank, marked up. It is not ours and it goes
                   onto a desk page, so it arrives as text: the markup is
                   stripped here rather than trusted there.

No frappe import: this is a decoder, and it is tested as one.
"""

import base64
import re

#: The container both photoTAN and chipTAN-QR use ("Matrix-Code" in HHD_UC):
#:
#:     2 bytes  length of the MIME type, big endian
#:     n bytes  the MIME type, e.g. "image/png"
#:     2 bytes  length of the image, big endian
#:     n bytes  the image itself
#:
#: Nothing announces this layout -- it is recognised by parsing and by the
#: parts making sense together.
_MIME_MAX = 64

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def matrix_code(raw):
    """The image out of a photoTAN / chipTAN-QR challenge, or None.

    Deliberately strict. The same field carries flicker data for chipTAN
    optisch, which is ASCII and would produce a nonsense "MIME type" and a
    nonsense length; every one of the checks below is there to say None for it
    rather than hand a broken image to the browser.

    :param raw: bytes of challenge_hhduc
    :return: (mime type, image bytes), or None
    """
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 5:
        return None

    data = bytes(raw)
    mime_length = int.from_bytes(data[:2], "big")
    if not 1 <= mime_length <= _MIME_MAX or len(data) < 2 + mime_length + 2:
        return None

    try:
        mime = data[2:2 + mime_length].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not mime.startswith("image/"):
        return None

    start = 2 + mime_length
    image_length = int.from_bytes(data[start:start + 2], "big")
    image = data[start + 2:start + 2 + image_length]
    # A truncated image is worse than none: the browser shows a broken frame
    # and the user has nothing to scan and no idea why.
    if not image_length or len(image) != image_length:
        return None

    return mime, image


def as_text(value):
    """Bank-supplied markup as plain text.

    The challenge text comes from the bank and is shown on a desk page. It is
    not ours to render: the markup comes off here, so nothing downstream has to
    decide whether this particular bank can be trusted with HTML.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    text = _TAG.sub(" ", str(value))
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return _WHITESPACE.sub(" ", text).strip()


def picture_of(response):
    """The photoTAN image, wherever this library version keeps it.

    Two places, and the one we looked at is the empty one.

    python-fints parses the HHD_UC container itself and hands the result over
    as ``challenge_matrix`` -- a plain (mime, bytes) tuple. Having done that it
    leaves the top-level ``challenge_hhduc`` at None, and that is the attribute
    this module read. So on a comdirect access the response carried

        challenge         'Siehe Grafik'
        challenge_hhduc   None
        challenge_matrix  ('image/png', b'\x89PNG...')

    and the prompt said "see the graphic" with no graphic under it -- which is
    precisely the failure the whole module was written to prevent, moved one
    attribute to the left.

    The library's own parse is preferred where it exists; matrix_code() stays
    for the versions and paths that hand the raw container over instead. Both
    are checked because neither is guaranteed.

    :return: (mime type, image bytes), or None
    """
    matrix = getattr(response, "challenge_matrix", None)
    if isinstance(matrix, (tuple, list)) and len(matrix) == 2:
        mime, image = matrix
        mime = mime.decode("ascii", "replace") if isinstance(
            mime, (bytes, bytearray)) else str(mime or "")
        if mime.startswith("image/") and isinstance(
                image, (bytes, bytearray)) and image:
            return mime, bytes(image)

    return matrix_code(getattr(response, "challenge_hhduc", None))


def challenge_of(response):
    """Everything the prompt needs in order to be answerable.

    :param response: python-fints NeedTANResponse, or anything with the same
        attributes -- nothing here requires the class itself
    :return: {"text", "image": {"mime", "data"}} with the image as a base64
        payload ready for a data: URI, or {} when the bank sent nothing usable
    """
    if response is None:
        return {}

    challenge = {}
    text = as_text(getattr(response, "challenge", None))
    if not text:
        text = as_text(getattr(response, "challenge_html", None))
    if text:
        challenge["text"] = text

    picture = picture_of(response)
    if picture:
        mime, image = picture
        challenge["image"] = {
            "mime": mime,
            "data": base64.b64encode(image).decode("ascii"),
        }

    return challenge
