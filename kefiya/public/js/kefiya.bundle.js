// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// Everything this app wants loaded on every desk page.
//
// It is a bundle and not a plain file path for one reason: esbuild gives the
// built file a content hash and records it in assets.json, and hooks.py names
// the bundle rather than the built name. Assets under /assets are served with
// a long cache, so a plain path would leave browsers on the version they
// happened to fetch first -- the fix would be deployed and nobody would have
// it until they cleared their cache.
//
// Adding something here means it loads for every user on every page. That is
// a cost; the bar is that it must be needed by more than one page.

// An IBAN in groups of four, wherever one is shown to a person.
import "./controllers/iban_display";
// What the bank allows on an account. Both transfer forms ask it now, so
// it belongs to every page rather than to one doctype.
import "./controllers/account_capabilities";
// What our own payment history says about a recipient. Asked at entry,
// because the person who sends the order is not the one who saw the
// invoice.
import "./controllers/payee_check";
// What a failed frappe.call actually said. Shared by the fetch and the TAN
// box, which were one file until the box moved out.
import "./controllers/call_error";
// The TAN box. Three callers -- the collective fetch, the outgoing payments
// and the Kefiya Login form -- and no share in what any of them does
// otherwise, so it is not a part of any one of them.
import "./controllers/tan_prompt";
// The box that shows what the bank said about a payee and releases the
// order. Two callers -- the transfer form and the outgoing payments --
// and the second one had no way to reach it while it lived in a file that
// is included into doctype JS rather than bundled.
import "./controllers/vop_prompt";
// One account's fetch result as log lines. A pure function of the server's
// summary, kept apart from the run that produces it.
import "./controllers/fetch_log";
import "./controllers/bank_refresh";
// Opened from the outgoing-payments page, and from anywhere else that wants
// to show one transfer without navigating away from what the reader is doing.
import "./controllers/transfer_details";
// Entering and correcting one transfer, with the execution options the
// document has always carried.
import "./controllers/transfer_form";
// The outgoing-payments list. It used to be a stored script on one site; the
// page there is now a bootstrap that calls kefiya.payment_outbox().
// Das Aussehen dieser Liste, getrennt von ihrem Verhalten.
import "./controllers/outbox_style";
import "./controllers/payment_outbox";
