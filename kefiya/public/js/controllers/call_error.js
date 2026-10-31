// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// What a failed frappe.call actually said, as one readable line.
//
// A frappe.call failure arrives in one of four shapes depending on how it
// failed, and none of them is the message. This unwraps them in order and
// gives up quietly rather than inventing a sentence.
//
// Shared by the collective fetch and the TAN box, which were one file until
// the box moved out.
//
// Not shared with payment_outbox.js, which has a function of the same name.
// That one is not a copy: it asks kefiya.server_message first and falls back
// to r.message, which is a different unwrapping for a different set of
// answers. Merging them on the strength of the name would have changed what
// the outgoing-payments page reports about a failed send.

frappe.provide("kefiya");

(function () {
    "use strict";

    function callError(r) {
        try {
            if (!r) return "";
            var d = r.responseJSON || r;
            if (d && d.exception) return "" + d.exception;
            if (d && d._error_message) return "" + d._error_message;
            var sm = (d && d._server_messages) || r._server_messages;
            if (sm) {
                try {
                    var a = JSON.parse(sm);
                    if (a && a.length) {
                        var o = JSON.parse(a[0]);
                        return o.message || a[0];
                    }
                } catch (ignoredA) {
                    return "" + sm;
                }
            }
            if (r.responseText) return ("" + r.responseText).slice(0, 220);
        } catch (ignoredB) {}
        return "";
    }
    kefiya.call_error = callError;
})();
