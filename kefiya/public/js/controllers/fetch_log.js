// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// One account's fetch result, turned into the lines the log shows.
//
// A pure function of the server's summary: no run state, no DOM, nothing to
// mock. That is why it is here and not in bank_refresh.js -- it was a third of
// that file and had nothing to do with orchestrating a run.
//
// The distinction it exists to keep: a refusal is not a failure. The bank not
// offering a query and the query going wrong are shown as different things.
// Rendering both as "not available" once made 88 real failures of a single run
// read like an absent bank feature.

frappe.provide("kefiya");

(function () {
    "use strict";

    // What the server reported per account, turned into readable lines.
    // errors and unsupported are kept strictly apart: a real failure must not
    // look like an absent bank feature.
    function fetchLogLines(x) {
        var lines = [];
        var errs = x.errors || [];
        var uns = x.unsupported || [];
        var why = x.error_details || {};
        var whyNot = x.unsupported_details || {};

        function add(label, text, kind) {
            lines.push({ label: label, text: text, kind: kind || "ok" });
        }

        // name = the identifier _optional_fetch uses on the server.
        function report(label, name, describe) {
            if (errs.indexOf(name) >= 0) {
                add(label, __("Error") + ": "
                    + (why[name] || __("reason in the Error Log")), "err");
                return;
            }
            if (uns.indexOf(name) >= 0) {
                // Some banks refuse the query for this account only, rather
                // than not knowing it. Where the server could tell the two
                // apart, the more precise reason stands here.
                add(label, whyNot[name] || __("not offered by the bank"),
                    "none");
                return;
            }
            var d = describe();
            add(label, d === null ? __("nothing delivered") : d,
                d === null ? "none" : "ok");
        }

        var t = x.transactions || {};
        if (x.skipped || t.status === "skipped") {
            add(__("Fetch"), __("This access is excluded from the fetch"), "none");
            return lines;
        }
        if (t.status === "tan_required") {
            add(__("Transactions"),
                x.message || __("Release required, then fetch again"),
                "err");
            return lines;
        }
        add(__("Transactions"), __("{0} new", [t.new_count || 0]));

        if (x.account_kind) {
            // The kind is a stable English key in the database -- a Select
            // option other code matches on. Only what is shown gets
            // translated; the stored value must not.
            add(__("Account Kind"), __(x.account_kind), "none");
        }
        // Only present when the books and the bank disagree about what this
        // account is. Marked as an error rather than a note: an account that
        // is not money counted as money overstates what can be paid out.
        if (x.ledger_complaint) {
            add(__("Ledger"), x.ledger_complaint, "err");
        }

        report(__("Balance"), "balance", function () {
            var b = x.balance;
            if (!b) return null;
            if (!b.stored) {
                return __("not stored") + " ("
                    + (b.reason || __("unknown")) + ")";
            }
            var cur = b.currency || undefined;
            var s = format_currency(b.balance, cur);
            if (b.line_of_credit) {
                s += " · " + __("Credit line") + " "
                    + format_currency(b.line_of_credit, cur);
            }
            // The same balance, counted back over the bookings just fetched.
            if (b.running && b.running.updated) {
                s += " · " + __("{0} bookings given a balance",
                                [b.running.updated]);
            } else if (b.running && b.running.reason) {
                s += " · " + b.running.reason;
            }
            return s;
        });

        report(__("Pending"), "pending_transactions", function () {
            var p = x.pending;
            if (!p) return null;
            return __("{0} new, {1} unchanged, {2} settled",
                      [p.created || 0, p.updated || 0, p.cancelled || 0]);
        });

        report(__("Securities"), "holdings", function () {
            var h = x.holdings;
            if (!h) return null;
            return __("{0} new, {1} updated",
                      [h.created || 0, h.updated || 0]);
        });

        // HKDBS: money we collect once on a date. It was labelled "standing
        // orders" here, which is a different business transaction entirely.
        report(__("Scheduled debits"), "scheduled_debits", function () {
            var p = x.planned;
            if (!p) return null;
            return __("{0} new, {1} unchanged, {2} cancelled",
                      [p.created || 0, p.updated || 0, p.cancelled || 0]);
        });

        report(__("Standing orders"), "standing_orders", function () {
            var o = x.standing_orders;
            if (!o) return null;
            var txt = __("{0} held by the bank", [o.count || 0]);
            if (o.count && !o.schedule_confirmed) {
                txt += " · " + __("cycle not yet verified");
            }
            return txt;
        });

        report(__("Statements"), "statements", function () {
            var s = x.statements;
            if (!s) return null;
            if (s.reason) return s.reason;
            var txt = __("{0} downloaded of {1} available",
                         [s.downloaded || 0, s.available || 0]);
            if (s.already_present) {
                txt += ", " + __("{0} already present", [s.already_present]);
            }
            if (s.failed_at) {
                txt += " · " + __("stopped at statement {0}", [s.failed_at]);
            }
            return txt;
        });

        report(__("Credit card"), "credit_card", function () {
            var c = x.credit_card;
            if (!c) return null;
            if (c.reason) return c.reason;
            return __("{0} new", [c.created || 0])
                + (c.skipped
                    ? (", " + __("{0} already known", [c.skipped])) : "");
        });

        report(__("Transfer limit"), "transfer_limit", function () {
            var l = x.transfer_limit;
            if (!l) return null;
            if (l.reason) return l.reason;
            if (!l.amount) return null;
            return format_currency(l.amount)
                + (l.limit_type ? (" · " + l.limit_type) : "");
        });

        return lines;
    }
    kefiya.fetch_log_lines = fetchLogLines;
})();
