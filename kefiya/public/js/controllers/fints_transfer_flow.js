// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

/**
 * Shared outgoing-transfer flow: what to do with the bank's answer.
 *
 * Used by both entry points -- a transfer raised from a Payment Request and a
 * free-entry Kefiya Transfer -- so the money-moving dialogs stay identical
 * rather than drifting apart in two copies.
 *
 * It opens no TAN box. The server publishes one before it answers, and that
 * box shows the bank's challenge picture and closes itself when a decoupled
 * release lands. A second one here was a mandatory TAN field over the top of
 * it, for procedures that produce no code to type.
 */
function kefiya_handle_transfer_response(frm, msg) {
    if (!msg) {
        frappe.msgprint(__("No response from server."));
        return;
    }
    if (msg.status === "submitted") {
        frappe.show_alert({ message: __("Transfer submitted."), indicator: "green" });
        frm.reload_doc();
    } else if (msg.status === "tan_required") {
        // Nothing to open. The server published the release box before it
        // answered -- the same box the collective fetch uses, which shows the
        // bank's own challenge picture and closes itself when the release
        // lands. This file used to build a SECOND box on top of it, with a
        // mandatory TAN field even for a decoupled procedure that produces no
        // code. Two boxes for one question, and only one of them could ever
        // be right.
        frappe.show_alert({
            message: __("The bank asks for a release."),
            indicator: "orange"
        }, 8);
    } else if (msg.status === "vop_mismatch") {
        kefiya_prompt_vop_release(frm, msg.docname, msg.vop_result);
    } else {
        frappe.msgprint({
            title: __("Transfer failed"),
            indicator: "red",
            message: msg.message || __("Unknown error")
        });
    }
}

/**
 * Verification of Payee mismatch: the bank could not confirm that the payee
 * name belongs to the IBAN. No money has moved. The order is parked
 * server-side and can only continue through this box, after a human compared
 * the payee against the underlying document -- a VoP mismatch is exactly what
 * a payment diversion (redirected invoice) looks like, so it is never waved
 * through.
 *
 * The box itself is kefiya.vop_prompt, in the bundle. It used to live here,
 * and this file is included into doctype JS rather than bundled -- so the
 * outgoing-payments page could not reach it and reported a parked order as
 * sent. One box, both callers.
 */
function kefiya_prompt_vop_release(frm, kefiya_login, answer) {
    kefiya.vop_prompt({
        login: kefiya_login,
        scope: frm.docname,
        answer: answer,
        onResult: function (msg) {
            kefiya_handle_transfer_response(frm, msg);
        }
    });
}

