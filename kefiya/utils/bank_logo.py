# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt
"""Find a bank's logo for a Kefiya Login from its FinTS endpoint URL.

FinTS endpoints such as ``https://fints.ing.de/fints/`` never serve a favicon
themselves, but the bank's website on the parent domain (``ing.de``) does. The
lookup therefore derives the website domain from the endpoint URL, reads the icon
links from the website's HTML and downloads the largest usable raster icon, falling
back to ``/favicon.ico``. FinTS hosts run by shared IT providers (Sparkassen,
Volks- und Raiffeisenbanken, DKB) are mapped to the brand's website first.

Everything here is plain Python without Frappe calls, so it can be unit tested and
run outside a site.
"""
from __future__ import annotations

import io
import ipaddress
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

REQUEST_TIMEOUT = 6  # seconds, per request
MAX_HTML_BYTES = 512 * 1024  # the icon links live in <head>; no need to read more
MAX_ICON_BYTES = 3 * 1024 * 1024
MAX_LOGO_PX = 256
MIN_LOGO_PX = 16
USER_AGENT = (
    "Mozilla/5.0 (compatible; Kefiya FinTS connector; "
    "+https://github.com/phamos-eu/kefiya)"
)

# FinTS hosts operated by shared IT providers, whose own domain carries no bank
# logo. Matched as host suffixes; the first match wins.
SHARED_FINTS_HOSTS = (
    ("s-fints-pt-dkb.de", "dkb.de"),
    ("s-hbci.de", "sparkasse.de"),
    ("fiducia.de", "vr.de"),
    ("atruvia.de", "vr.de"),
    ("gad.de", "vr.de"),
)
# Sparkassen-Finanzportal hosts: banking-bw4.s-fints-pt-bw.de, banking-by1.s-fints-pt-by.de, ...
SPARKASSE_HOST = re.compile(r"(^|\.)s-fints-pt-[a-z0-9]+\.de$")

# Second-level labels under which the registrable domain has three labels (bank.co.uk).
SECOND_LEVEL_LABELS = {"co", "com", "org", "net", "gov", "edu", "ac"}

ICON_RELS = {"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}
TOUCH_ICON_RELS = {"apple-touch-icon", "apple-touch-icon-precomposed"}


def website_domain(fints_url: str) -> str | None:
    """Return the domain of the bank website behind a FinTS endpoint URL, or None.

    ``https://fints.ing.de/fints/`` gives ``ing.de``; a Sparkassen-Finanzportal
    host gives ``sparkasse.de``; an IP address or an unusable URL gives None.
    """
    host = _hostname(fints_url)
    if not host:
        return None

    for suffix, domain in SHARED_FINTS_HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return domain
    if SPARKASSE_HOST.search(host):
        return "sparkasse.de"

    labels = host.split(".")
    if len(labels) < 2:
        return None
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL_LABELS and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _hostname(url: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None

    host = (parsed.hostname or "").strip(".").lower()
    if not host or host == "localhost":
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


def icon_candidates(html: str, page_url: str) -> list[str]:
    """Return the icon URLs linked from an HTML page, largest raster icon first.

    Apple touch icons rank as 180px unless they declare a size. SVG icons are
    skipped: they cannot be rasterised here and would be stored unsanitised.
    Relative hrefs are resolved against ``page_url``.
    """
    soup = BeautifulSoup(html, "html.parser")
    scored = []
    for position, link in enumerate(soup.find_all("link", href=True)):
        rels = {rel.lower() for rel in (link.get("rel") or [])}
        href = link["href"].strip()
        if not href or not rels & ICON_RELS or _is_svg(link.get("type"), href):
            continue
        scored.append((-_declared_size(link.get("sizes"), rels), position, urljoin(page_url, href)))

    candidates: list[str] = []
    for _, _, url in sorted(scored):
        if url not in candidates:
            candidates.append(url)
    return candidates


def _declared_size(sizes: str | None, rels: set[str]) -> int:
    found = [max(int(w), int(h)) for w, h in re.findall(r"(\d+)x(\d+)", (sizes or "").lower())]
    if found:
        return max(found)
    return 180 if rels & TOUCH_ICON_RELS else 32


def _is_svg(mime_type: str | None, href: str) -> bool:
    return "svg" in (mime_type or "").lower() or urlparse(href).path.lower().endswith(".svg")


def fetch_bank_logo(fints_url: str) -> tuple[bytes, str] | None:
    """Download the bank logo for a FinTS endpoint URL.

    Returns ``(png_bytes, source_url)`` or None when no usable icon was found.
    Network and decoding errors are swallowed on purpose: a missing logo is
    cosmetic and must never block saving a login.
    """
    domain = website_domain(fints_url)
    if not domain:
        return None

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    candidates: list[str] = []
    for page_url in (f"https://www.{domain}/", f"https://{domain}/"):
        page = _get(session, page_url, MAX_HTML_BYTES, truncate=True)
        if page is None:
            continue
        html, final_url = page
        candidates.extend(icon_candidates(html.decode("utf-8", "replace"), final_url))
        if candidates:
            break
    candidates += [f"https://www.{domain}/favicon.ico", f"https://{domain}/favicon.ico"]

    tried: set[str] = set()
    for url in candidates:
        if url in tried:
            continue
        tried.add(url)
        got = _get(session, url, MAX_ICON_BYTES)
        png = to_png(got[0]) if got else None
        if png:
            return png, url
    return None


def _get(session: requests.Session, url: str, limit: int, truncate: bool = False) -> tuple[bytes, str] | None:
    """GET ``url`` and return ``(body, final_url)``; None on any error or non-200.

    Bodies larger than ``limit`` are cut off when ``truncate`` is set (enough for
    an HTML head) and rejected otherwise.
    """
    try:
        with session.get(url, timeout=REQUEST_TIMEOUT, stream=True) as response:
            if response.status_code != 200:
                return None
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > limit:
                    if truncate:
                        chunks.append(chunk)
                        break
                    return None
                chunks.append(chunk)
            return b"".join(chunks), response.url
    except requests.RequestException:
        return None


def to_png(data: bytes | None) -> bytes | None:
    """Re-encode icon bytes (ICO, PNG, JPEG, ...) as a PNG of at most MAX_LOGO_PX.

    Returns None for anything that is not a real raster image, such as an HTML
    error page served with status 200, and for icons smaller than MIN_LOGO_PX.
    For a multi-size ICO, Pillow opens the largest frame.
    """
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if min(image.size) < MIN_LOGO_PX:
                return None
            logo = image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None

    logo.thumbnail((MAX_LOGO_PX, MAX_LOGO_PX))
    out = io.BytesIO()
    logo.save(out, format="PNG", optimize=True)
    return out.getvalue()
