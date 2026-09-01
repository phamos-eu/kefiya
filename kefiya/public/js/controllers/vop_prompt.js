// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// The box that shows what the bank said about a payee, and releases the order.
//
//   kefiya.vop_prompt({login, scope, answer, onResult})
//
// `answer` is what the server stored about the parked check: the result code,
// the name the bank holds for that IBAN, and who the order pays.
//
// Its own file for the same reason the TAN box has one: two callers -- the
// transfer form and the outgoing-payments page -- and no share in what either
// of them does otherwise.
//
// It moved here after a real transfer went missing. The outgoing-payments page
// had no branch for a parked payee check at all: the send returned
// "vop_mismatch", the page fell into its else and reported "handed to the bank
// -- 1 order" in green. Nothing had been handed to anyone. The order sat
// parked, and the only way to release it was a dialog that lived in a file
// that page could not reach.

frappe.provide("kefiya");

(function () {
    "use strict";

    function esc(value) {
        return frappe.utils.escape_html(
            String(value === undefined || value === null ? "" : value));
    }

    // Who the order pays, in the lines that decide the question.
    //
    // What stood here as well: a <pre> dump of the raw answer. For a parsed
    // segment that is its repr -- "HIVPP1(header=..., polling_id=b'587d...')"
    // -- shown to the person deciding whether money leaves. Nobody compares an
    // invoice against that. The segment is in the Error Log.
    function answerBlock(answer) {
        if (!answer || !answer.payee_name) return "";
        var row = function (label, value) {
            return "<tr><td style='padding:2px 12px 2px 0;color:#6c7680'>"
                + esc(label) + "</td><td><b>" + esc(value) + "</b></td></tr>";
        };
        var rows = row(__("Payee on the order"), answer.payee_name);
        if (answer.iban) rows += row(__("IBAN"), answer.iban);
        if (answer.bank_name) rows += row(__("Name at the Bank"), answer.bank_name);
        if (answer.result) rows += row(__("Bank's Answer"), answer.result);
        return "<table style='margin:10px 0'>" + rows + "</table>"
            // No verdict is not the same as no answer. This bank replies with
            // a payment status report -- a pain.002 document neither this app
            // nor python-fints reads -- so the row above is simply absent, and
            // saying nothing would leave the reviewer looking for it.
            + (answer.result ? ""
                : "<div class='alert alert-info' style='margin:10px 0'>"
                  + __("This bank returns its result in a format this app cannot read, so it is not shown above. Check the recipient against the original document yourself — here, you are the check.")
                  + "</div>");
    }

    function vopPrompt(opts) {
        opts = opts || {};
        var answer = opts.answer || {};
        var remembers = !!(answer.payee_name && answer.iban);

        var d = new frappe.ui.Dialog({
            title: __("Verification of Payee — mismatch"),
            fields: [
                {
                    fieldtype: "HTML",
                    options:
                        "<div class='alert alert-warning' style='margin-bottom:10px'>"
                        + __("The bank could not confirm that the payee name matches the IBAN. <b>No money has been sent.</b>")
                        + "</div>"
                        + __("Compare the recipient against the invoice before releasing. If the account details were changed recently or came in by email, treat this as a possible fraud attempt and clarify by phone using a known number.")
                        + answerBlock(answer)
                        + (remembers
                            ? "<div class='text-muted' style='margin-bottom:8px'>"
                              + __("Once you release it, this payee is remembered: a later payment to the same name and the same IBAN goes through without asking again.")
                              + "</div>"
                            : "")
                },
                {
                    fieldtype: "Check",
                    fieldname: "reviewed",
                    label: __("I verified the recipient against the original document"),
                    default: 0,
                    reqd: 1
                }
            ],
            primary_action_label: __("Release transfer"),
            primary_action: function (values) {
                if (!values.reviewed) {
                    frappe.msgprint(__("Please confirm you verified the recipient."));
                    return;
                }
                d.hide();
                frappe.call({
                    method: "kefiya.utils.client.approve_vop_transfer",
                    args: {
                        kefiya_login: opts.login,
                        user_scope: opts.scope || opts.login,
                        confirmed: 1
                    },
                    freeze: true,
                    freeze_message: __("Releasing transfer..."),
                    callback: function (r) {
                        if (opts.onResult) opts.onResult((r && r.message) || {});
                    }
                });
            },
            secondary_action_label: __("Cancel transfer"),
            secondary_action: function () {
                d.hide();
                frappe.show_alert({
                    message: __("Transfer not released. No money was sent."),
                    indicator: "orange"
                });
            }
        });
        d.show();
    }

    kefiya.vop_prompt = vopPrompt;
})();
