# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

import io
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate
from PIL import Image

from kefiya.utils import bank_logo
from kefiya.utils.bank_logo import fetch_bank_logo, icon_candidates, to_png, website_domain

LOGIN_NAME = "_Test Bank Logo Login"
FINTS_URL = "https://fints.example-bank.de/fints"

HTML = """<html><head>
<link rel="icon" type="image/svg+xml" sizes="any" href="/icons/favicon.svg"/>
<link rel="icon" type="image/png" sizes="16x16 32x32 48x48 192x192" href="/icons/favicon-192.png"/>
<link rel="apple-touch-icon" type="image/png" sizes="180x180" href="/icons/touch-180.png"/>
<link rel="icon" type="image/png" sizes="310x310" href="icons/favicon-310.png"/>
<link rel="shortcut icon" href="https://cdn.example-bank.de/favicon.ico"/>
<link rel="apple-touch-icon" href="/icons/touch.png"/>
<link rel="stylesheet" href="/style.css"/>
<link rel="mask-icon" href="/icons/mask.svg" color="#000"/>
</head><body></body></html>"""


def image_bytes(size: int, fmt: str = "PNG", color: str = "red") -> bytes:
    """A square test image; an ICO gets 16 and 32 px frames next to the full size."""
    image = Image.new("RGB" if fmt == "JPEG" else "RGBA", (size, size), color)
    out = io.BytesIO()
    if fmt == "ICO":
        image.save(out, format="ICO", sizes=[(16, 16), (32, 32), (size, size)])
    else:
        image.save(out, format=fmt)
    return out.getvalue()


def image_size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        return image.size


def responses(table: dict):
    """Stand-in for bank_logo._get: answers from `table`, None (= error/404) otherwise."""

    def _get(session, url, limit, truncate=False):
        return table.get(url)

    return _get


class TestWebsiteDomain(FrappeTestCase):
    def test_parent_domain_of_the_fints_host(self):
        cases = {
            "https://fints.ing.de/fints/": "ing.de",
            "https://fints.dkb.de/fints": "dkb.de",
            "https://hbci.1822direkt.com/": "1822direkt.com",
            "https://banking.example.co.uk/fints": "example.co.uk",
            "fints.comdirect.de/fints": "comdirect.de",
            "https://FINTS.Commerzbank.DE/fints": "commerzbank.de",
        }
        for url, domain in cases.items():
            with self.subTest(url=url):
                self.assertEqual(website_domain(url), domain)

    def test_shared_provider_hosts_map_to_the_brand(self):
        cases = {
            "https://banking-dkb.s-fints-pt-dkb.de/fints30": "dkb.de",
            "https://banking-bw4.s-fints-pt-bw.de/fints30": "sparkasse.de",
            "https://hbci-pintan-by.s-hbci.de/PinTanServlet": "sparkasse.de",
            "https://hbci11.fiducia.de/cgi-bin/hbciservlet": "vr.de",
            "https://fints2.atruvia.de/cgi-bin/hbciservlet": "vr.de",
        }
        for url, domain in cases.items():
            with self.subTest(url=url):
                self.assertEqual(website_domain(url), domain)

    def test_unusable_urls_give_none(self):
        for url in (
            "",
            None,
            "ftp://fints.ing.de/",
            "https://127.0.0.1/fints",
            "https://[::1]/fints",
            "https://localhost/fints",
            "https://bank/",
        ):
            with self.subTest(url=url):
                self.assertIsNone(website_domain(url))


class TestIconCandidates(FrappeTestCase):
    def test_largest_raster_icon_first_and_svg_skipped(self):
        self.assertEqual(
            icon_candidates(HTML, "https://www.example-bank.de/start"),
            [
                "https://www.example-bank.de/icons/favicon-310.png",
                "https://www.example-bank.de/icons/favicon-192.png",
                "https://www.example-bank.de/icons/touch-180.png",
                "https://www.example-bank.de/icons/touch.png",
                "https://cdn.example-bank.de/favicon.ico",
            ],
        )

    def test_page_without_icons(self):
        html = "<html><head><title>Bank</title><link rel=stylesheet href=a.css></head></html>"
        self.assertEqual(icon_candidates(html, "https://www.example-bank.de/"), [])

    def test_duplicate_links_collapse(self):
        html = '<link rel="icon" href="/a.png"><link rel="shortcut icon" href="/a.png">'
        self.assertEqual(icon_candidates(html, "https://x.de/"), ["https://x.de/a.png"])


class TestToPng(FrappeTestCase):
    def test_ico_is_converted_to_a_png_of_its_largest_frame(self):
        self.assertEqual(image_size(to_png(image_bytes(48, "ICO"))), (48, 48))

    def test_large_images_are_downscaled(self):
        self.assertEqual(image_size(to_png(image_bytes(1024))), (256, 256))

    def test_small_images_keep_their_size(self):
        self.assertEqual(image_size(to_png(image_bytes(64, "JPEG"))), (64, 64))

    def test_tiny_images_and_non_images_are_rejected(self):
        self.assertIsNone(to_png(image_bytes(8)))
        self.assertIsNone(to_png(b"<html><body>Not Found</body></html>"))
        self.assertIsNone(to_png(b"<svg xmlns='http://www.w3.org/2000/svg'/>"))
        self.assertIsNone(to_png(b""))
        self.assertIsNone(to_png(None))


class TestFetchBankLogo(FrappeTestCase):
    def test_prefers_the_largest_icon_linked_from_the_website(self):
        table = {
            "https://www.example-bank.de/": (HTML.encode(), "https://www.example-bank.de/start"),
            "https://www.example-bank.de/icons/favicon-310.png": (image_bytes(310), ""),
            "https://www.example-bank.de/icons/favicon-192.png": (image_bytes(192), ""),
        }
        with patch.object(bank_logo, "_get", side_effect=responses(table)) as get:
            png, source = fetch_bank_logo(FINTS_URL)

        self.assertEqual(source, "https://www.example-bank.de/icons/favicon-310.png")
        self.assertEqual(image_size(png), (256, 256))
        requested = [call.args[1] for call in get.call_args_list]
        self.assertNotIn("https://example-bank.de/", requested)

    def test_skips_icons_that_are_not_images_and_falls_back_to_favicon_ico(self):
        table = {
            "https://www.example-bank.de/": (HTML.encode(), "https://www.example-bank.de/"),
            # served with status 200 but an HTML body
            "https://www.example-bank.de/icons/favicon-310.png": (b"<html>Not Found</html>", ""),
            "https://example-bank.de/favicon.ico": (image_bytes(32, "ICO"), ""),
        }
        with patch.object(bank_logo, "_get", side_effect=responses(table)):
            png, source = fetch_bank_logo(FINTS_URL)

        self.assertEqual(source, "https://example-bank.de/favicon.ico")
        self.assertEqual(image_size(png), (32, 32))

    def test_without_website_or_icons_returns_none(self):
        with patch.object(bank_logo, "_get", return_value=None):
            self.assertIsNone(fetch_bank_logo("https://fints.example.com/kefiya-demo"))

        with patch.object(bank_logo, "_get") as get:
            self.assertIsNone(fetch_bank_logo("https://127.0.0.1/fints"))
        get.assert_not_called()


class TestKefiyaLoginBankLogo(FrappeTestCase):
    def setUp(self):
        if not frappe.get_meta("Kefiya Login").has_field("bank_logo"):
            self.skipTest("Kefiya Login is not migrated on this site: field bank_logo is missing")
        for name in frappe.get_all("Kefiya Import", {"kefiya_login": LOGIN_NAME}, pluck="name"):
            frappe.delete_doc("Kefiya Import", name, force=True, ignore_permissions=True)
        if frappe.db.exists("Kefiya Login", LOGIN_NAME):
            frappe.delete_doc("Kefiya Login", LOGIN_NAME, force=True, ignore_permissions=True)

        self.login = frappe.get_doc(
            {
                "doctype": "Kefiya Login",
                "login_name": LOGIN_NAME,
                "fints_url": FINTS_URL,
                "blz": "12345678",
                "fints_login": "user",
                "fints_password": "secret",
            }
        )
        # the bank website has no usable icon yet
        with self.lookup(found=False) as lookup:
            self.login.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)
        self.insert_lookup = lookup

    def lookup(self, found: bool, color: str = "red"):
        """Patch the website lookup: nothing found, or a PNG of `color`."""
        result = (image_bytes(64, color=color), "https://www.example-bank.de/icon.png") if found else None
        return patch.object(bank_logo, "fetch_bank_logo", return_value=result)

    def save(self, **values) -> str:
        self.login.update(values)
        self.login.flags.ignore_mandatory = self.login.flags.ignore_links = True
        self.login.save(ignore_permissions=True)
        return self.login.bank_logo

    def attached_logo_urls(self):
        return frappe.get_all("File", self.login._bank_logo_filters(), pluck="file_url")

    def new_import(self):
        return frappe.get_doc(
            {
                "doctype": "Kefiya Import",
                "kefiya_login": LOGIN_NAME,
                "from_date": add_days(getdate(), -10),
                "to_date": add_days(getdate(), -1),
            }
        ).insert(ignore_permissions=True)

    def test_insert_looks_the_logo_up(self):
        self.insert_lookup.assert_called_once_with(FINTS_URL)
        self.assertFalse(self.login.bank_logo)

    def test_save_without_logo_looks_it_up_again(self):
        with self.lookup(found=True) as lookup:
            url = self.save()
        lookup.assert_called_once_with(FINTS_URL)
        self.assertTrue(url.startswith("/private/files/"), url)
        self.assertTrue(url.endswith(".png"), url)
        self.assertEqual(frappe.db.get_value("Kefiya Login", LOGIN_NAME, "bank_logo"), url)
        self.assertEqual(self.attached_logo_urls(), [url])

    def test_save_with_logo_leaves_it_alone(self):
        with self.lookup(found=True):
            url = self.save()
        with self.lookup(found=True, color="blue") as lookup:
            self.save(blz="87654321")
        lookup.assert_not_called()
        self.assertEqual(self.attached_logo_urls(), [url])

    def test_changed_endpoint_replaces_the_logo(self):
        with self.lookup(found=True):
            first = self.save()
        with self.lookup(found=True, color="blue") as lookup:
            second = self.save(fints_url="https://fints.other-bank.de/fints")
        lookup.assert_called_once_with("https://fints.other-bank.de/fints")
        self.assertNotEqual(first, second)
        self.assertEqual(self.attached_logo_urls(), [second])

    def test_failed_lookup_never_blocks_saving(self):
        with patch.object(bank_logo, "fetch_bank_logo", side_effect=RuntimeError("boom")):
            self.save(blz="87654321")
        self.assertEqual(frappe.db.get_value("Kefiya Login", LOGIN_NAME, "blz"), "87654321")
        self.assertFalse(frappe.db.get_value("Kefiya Login", LOGIN_NAME, "bank_logo"))

    def test_loading_accounts_fetches_a_missing_logo(self):
        from kefiya.utils import client

        controller = MagicMock()
        controller.return_value.get_fints_accounts.return_value = [{"iban": "DE02120300000000202051"}]
        with patch.object(client, "_get_fints_controller", return_value=controller), self.lookup(
            found=True
        ) as lookup:
            result = client.get_accounts(LOGIN_NAME, LOGIN_NAME)

        self.assertEqual(result["accounts"][0]["iban"], "DE02120300000000202051")
        lookup.assert_called_once_with(FINTS_URL)
        self.assertTrue(frappe.db.get_value("Kefiya Login", LOGIN_NAME, "bank_logo"))

    def test_logo_is_copied_to_the_imports_of_the_login(self):
        earlier = self.new_import()
        self.assertFalse(earlier.bank_logo)

        with self.lookup(found=True):
            url = self.save()
        self.assertEqual(frappe.db.get_value("Kefiya Import", earlier.name, "bank_logo"), url)
        # a later import picks it up through fetch_from
        self.assertEqual(self.new_import().bank_logo, url)

    def test_removing_the_logo_by_hand_clears_it_on_the_imports(self):
        with self.lookup(found=True):
            self.save()
        kefiya_import = self.new_import()
        self.assertTrue(kefiya_import.bank_logo)

        # removed in the sidebar, and the website has nothing better to offer
        with self.lookup(found=False):
            self.save(bank_logo="")
        self.assertFalse(frappe.db.get_value("Kefiya Import", kefiya_import.name, "bank_logo"))
