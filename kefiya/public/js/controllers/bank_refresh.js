// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// One collective fetch across every bank access, for any page that wants one.
//
//   kefiya.bank_refresh({mount, btn, buttonLabel, onRefreshView, onDone, only})
//
// "only" is a list of Kefiya Login names. Given, the run touches those and
// nothing else -- that is what the "fetch the failed ones again" link in the
// finished panel uses, and what a caller wanting a single access passes.
//
// Returns a Promise resolving to the run summary, or to null when the run was
// not started at all -- missing permission, no accesses configured, or another
// run already going.
//
// Three things this exists to get right, each of them learned the hard way:
//
// No modal progress dialog. frappe.show_progress reuses its dialog only while
// the previous one is still visible, so every hide_progress made the next
// access open a fresh one -- one dialog per access -- and the overlapping
// fade-outs left orphaned modals behind. Progress lives in a panel on the page
// instead, which the modal used to cover anyway.
//
// One line of progress, not one per access. With 54 accesses the per-access
// list pushed the whole page out of sight. Everything that used to stand in
// those rows now goes into a log that is opened on demand: one block per
// account saying what was fetched and what was not, with the reason. Normally
// nobody needs it.
//
// A refusal is not a failure. The bank not offering a query and the query
// going wrong are shown as different things. Rendering both as "not available"
// once made 88 real failures of a single run read like an absent bank feature.
//
// Parallel per bank, sequential within one. FinTS allows a single dialog per
// access: two at once trigger two strong authentications and overwrite the
// shared client state that makes one TAN count for the whole bank. Different
// banks are independent. The server says which accesses belong together.

frappe.provide("kefiya");

(function () {
    "use strict";

    // Resolved when it is needed, not when the file loads: the bundle may be
    // parsed before the translations for this session are in place.
    function idleLabel() { return __("Fetch transactions"); }
    var run = null;

    // Colours come from the desk's own tokens, so the panel follows whatever
    // theme the site uses instead of carrying one of its own.
    var C_ERR = "var(--red-500, #c0392b)";
    var C_OK = "var(--green-500, #1a7f37)";
    var C_WAIT = "var(--orange-500, #b26a00)";
    var C_MUTED = "var(--text-muted, #6c757d)";

    // Server error boxes are muted for the whole run. Depth counting keeps
    // nested muting correct, and every exit path unmutes, so a failed run
    // never leaves the session silent.
    var muteDepth = 0;
    var muteKeep = null;

    function muteErrors() {
        if (muteDepth === 0 && frappe.request && frappe.request.report_error) {
            muteKeep = frappe.request.report_error;
            frappe.request.report_error = function () {};
        }
        muteDepth++;
    }

    function unmuteErrors() {
        if (muteDepth === 0) return;
        muteDepth--;
        if (muteDepth === 0 && frappe.request && muteKeep) {
            frappe.request.report_error = muteKeep;
            muteKeep = null;
        }
    }

    function esc(v) {
        if (v === null || v === undefined) v = "";
        return frappe.utils.escape_html("" + v);
    }


    // Held by reference, not by selector, so it works inside a shadow root as
    // well as in plain DOM.
    function panel() {
        if (!run || !run.mount) return null;
        if (run.panel && run.panel.isConnected) return run.panel;
        var el = document.createElement("div");
        el.setAttribute("style",
            "border:1px solid var(--border-color);border-radius:8px;"
            + "padding:10px 12px;margin-bottom:12px;background:var(--card-bg)");
        run.mount.insertAdjacentElement("afterbegin", el);
        run.panel = el;
        return el;
    }

    function render() {
        var el = panel();
        if (!el) return;
        var total = (run.order || []).length || 1;
        var pct = Math.min(100, Math.round(run.done / total * 100));

        var head;
        if (run.busy) {
            head = __("Fetching accounts …") + " " + pct + " %";
        } else {
            head = __("Account fetch finished") + " · "
                + __("{0} of {1} accesses", [run.done, total]);
            if (run.fail) head += " · " + __("{0} failed", [run.fail]);
            if (run.tan) head += " · " + __("{0}× awaiting release", [run.tan]);
        }

        var barColor = run.fail ? C_ERR : C_OK;
        var bar = "<div style='height:6px;border-radius:3px;"
            + "background:var(--border-color);overflow:hidden;margin:6px 0 2px'>"
            + "<div style='height:6px;width:" + pct + "%;background:" + barColor
            + "'></div></div>";

        // The log link used to appear only once the run had finished. A
        // collective fetch takes minutes, so an access that broke in the first
        // one stayed invisible for the rest of them -- and by the time the log
        // could be opened, the run had long moved past it. It is offered from
        // the first entry on.
        var links = "";
        if (run.log && run.log.length) {
            links = "<div style='margin-top:6px'>"
                + "<a href='#' data-kefiya='log' style='font-size:11px'>"
                + esc(__("Show log")) + "</a>";
        }
        if (!run.busy && run.onRefreshView) {
            links = (links || "<div style='margin-top:6px'>")
                + (run.log && run.log.length
                    ? " <span style='color:" + C_MUTED + "'>·</span> " : "")
                + "<a href='#' data-kefiya='upd' style='font-size:11px'>"
                + esc(__("Refresh view")) + "</a>";
        }
        // Repeating the whole run to reach the three accesses that broke costs
        // minutes and a strong authentication per bank. This repeats only
        // those three.
        var again = run.busy ? [] : unfinished();
        if (again.length) {
            links = (links || "<div style='margin-top:6px'>")
                + (links ? " <span style='color:" + C_MUTED + "'>·</span> " : "")
                + "<a href='#' data-kefiya='again' style='font-size:11px'>"
                + esc(__("Fetch the {0} unfinished accesses again",
                         [again.length])) + "</a>";
        }
        if (links) links += "</div>";

        el.innerHTML = "<div style='font-weight:600;color:"
            + (run.fail ? C_ERR : "var(--text-color)") + "'>"
            + esc(head) + "</div>" + bar + liveProblems() + links;

        var u = el.querySelector("[data-kefiya=upd]");
        if (u) u.onclick = function (e) { e.preventDefault(); run.onRefreshView(); };
        var g = el.querySelector("[data-kefiya=log]");
        if (g) g.onclick = function (e) { e.preventDefault(); showLog(); };
        var a = el.querySelector("[data-kefiya=again]");
        if (a) {
            // Read before the new run replaces `run` -- afterwards the list
            // this link was drawn for no longer exists.
            var names = again.slice();
            var opts = (run && run.options) || {};
            a.onclick = function (e) {
                e.preventDefault();
                kefiya.bank_refresh(Object.assign({}, opts, { only: names }));
            };
        }

        // A log that is already open must not freeze at the state it had when
        // it was opened -- that was the other half of "you only see it
        // afterwards".
        if (run.logDialog && run.logDialog.$wrapper
                && run.logDialog.$wrapper.is(":visible")) {
            var field = run.logDialog.fields_dict
                && run.logDialog.fields_dict.body;
            if (field) field.$wrapper.html(logBody());
        }
    }

    // The accesses of the current run that did not come through: a real
    // failure, or a release the bank is still waiting for. Both are worth a
    // second attempt -- the release because the user has meanwhile confirmed
    // it in the app, the failure because most of them are transient.
    function unfinished() {
        return ((run && run.log) || []).filter(function (e) {
            return e.state === "err" || e.state === "tan";
        }).map(function (e) { return e.ln; });
    }

    // The shortest true sentence about one failed access: the reason the
    // server gave, or the first line that came back marked as an error.
    function shortProblem(entry) {
        if (entry.detail) return entry.detail;
        var bad = (entry.lines || []).filter(function (l) {
            return l.kind === "err";
        })[0];
        if (bad) return bad.label + ": " + bad.text;
        return entry.state === "tan"
            ? __("awaiting release") : __("failed");
    }

    // Failures while the run is still going, right under the bar. Bounded, so
    // a long run cannot push the rest of the page away.
    function liveProblems() {
        var bad = ((run && run.log) || []).filter(function (e) {
            return e.state === "err" || e.state === "tan";
        });
        if (!bad.length) return "";

        var shown = bad.slice(-5);
        var hidden = bad.length - shown.length;
        var rows = shown.map(function (e) {
            var col = e.state === "err" ? C_ERR : C_WAIT;
            var icon = e.state === "err" ? "✗" : "🔑";
            return "<div style='margin-top:3px;line-height:1.35'>"
                + "<span style='color:" + col + "'>" + icon + "</span> "
                + "<span style='font-weight:600'>" + esc(e.ln) + "</span>"
                + "<span style='color:" + C_MUTED + "'> — </span>"
                + "<span style='color:" + col + "'>"
                + esc(String(shortProblem(e)).slice(0, 160)) + "</span></div>";
        }).join("");

        var more = hidden > 0
            ? "<div style='margin-top:3px;color:" + C_MUTED + "'>"
                + esc(__("and {0} more — see the log", [hidden])) + "</div>"
            : "";

        return "<div style='margin-top:8px;font-size:11px'>" + rows + more
            + "</div>";
    }


    function logBody() {
        var entries = (run && run.log) || [];
        var body;
        if (!entries.length) {
            body = "<div style='color:" + C_MUTED + "'>"
                + esc(__("No entries.")) + "</div>";
        } else {
            body = entries.map(function (e) {
                var icon = e.state === "err" ? "✗"
                    : (e.state === "tan" ? "🔑"
                        : (e.state === "skip" ? "·" : "✓"));
                var col = e.state === "err" ? C_ERR
                    : (e.state === "tan" ? C_WAIT
                        : (e.state === "skip" ? C_MUTED : C_OK));
                var rows = (e.lines || []).map(function (l) {
                    var lc = l.kind === "err" ? C_ERR
                        : (l.kind === "none" ? C_MUTED : "inherit");
                    var lw = l.kind === "err" ? "600" : "400";
                    return "<tr><td style='padding:1px 12px 1px 0;color:"
                        + C_MUTED + ";white-space:nowrap;vertical-align:top'>"
                        + esc(l.label) + "</td><td style='padding:1px 0;color:"
                        + lc + ";font-weight:" + lw + "'>"
                        + esc(l.text) + "</td></tr>";
                }).join("");
                var detail = e.detail
                    ? "<div style='white-space:pre-wrap;word-break:break-word;"
                        + "color:" + C_ERR + ";font-size:11px;margin-top:4px'>"
                        + esc(e.detail) + "</div>"
                    : "";
                return "<div style='margin-bottom:12px;padding-bottom:8px;"
                    + "border-bottom:1px solid var(--border-color)'>"
                    + "<div style='font-weight:600'><span style='color:" + col
                    + "'>" + icon + "</span> " + esc(e.ln) + "</div>"
                    + "<table style='font-size:12px;margin-top:4px'>" + rows
                    + "</table>" + detail + "</div>";
            }).join("");
        }
        return body;
    }

    function showLog() {
        var dlg = new frappe.ui.Dialog({
            title: __("Log of the account fetch"),
            size: "large",
            fields: [{ fieldtype: "HTML", fieldname: "body",
                       options: logBody() }],
            primary_action_label: __("Close"),
            primary_action: function () { dlg.hide(); }
        });

        // Held on to so render() can refresh it while the run continues. The
        // log used to be a snapshot of the moment it was opened, which during
        // a running fetch is the least interesting moment there is.
        if (run) run.logDialog = dlg;
        dlg.$wrapper.on("hidden.bs.modal", function () {
            if (run && run.logDialog === dlg) run.logDialog = null;
        });

        dlg.show();
    }

    function record(ln, state, lines, detail) {
        if (!run) return;
        run.log.push({ ln: ln, state: state, lines: lines || [],
                       detail: detail || "" });
        run.done++;
        if (state === "err") run.fail++;
        else if (state === "tan") run.tan++;
        else if (state === "skip") run.skip++;
        else run.ok++;
        render();
    }

    // Safety net only. Bootstrap loses track of its backdrop stack when modals
    // overlap, and a surviving backdrop leaves the page dimmed and unclickable.
    // Runs once at the end and never while a dialog is open.
    function cleanupBackdrops() {
        try {
            if (document.querySelector(".modal.show")) return;
            var b = document.querySelectorAll(".modal-backdrop");
            for (var i = 0; i < b.length; i++) {
                if (b[i].parentNode) b[i].parentNode.removeChild(b[i]);
            }
            document.body.classList.remove("modal-open");
        } catch (ignoredD) {}
    }

    // "Freigabe erforderlich" named no bank and no account. In a collective
    // run a dozen accesses stop for a release one after the other, and the box
    // on screen looked identical every time -- so the user had to guess which
    // banking app to open. The payload now carries the access; this puts it
    // where the eye lands, in the heading and in the first line of the dialog.

    // Set when the user has answered a TAN box during a run: the release is
    // given, so the accounts the bank held back can be fetched -- and nothing
    // else was going to do it.
    //
    // Three steps used to stand between the release and the bookings: confirm
    // in the banking app, click OK in the box (which only opens the session --
    // resolve_tan_interaction builds a controller and stops), then find the
    // "fetch the unfinished accesses again" link. Two of those three are
    // invisible, and 24 accounts sat unfetched behind them.
    var resumeWanted = false;

    function bindRealtime() {
        if (kefiya._bank_refresh_realtime) return;
        kefiya._bank_refresh_realtime = true;
        // No progressbar handler: it wrote into the per-access row that no
        // longer exists. The TAN prompt stays -- it is the only point at
        // which the run needs the user.
        frappe.realtime.on("fints_tan_interaction_required", function (data) {
            if (!run || !run.busy) return;
            kefiya.tan_prompt(data, function (ok) {
                // Only a release the server accepted. A refused one used to
                // arm this just the same, and the fetch it started made the
                // bank ask again -- a circle the user could only leave by
                // refusing to answer the box.
                if (!ok) return;
                resumeWanted = true;
                // Not now: the worker still holds the access, and a second
                // dialog on it is the very damage the run is built to avoid.
                // The end of the run picks this up.
                maybeResume();
            });
        });
    }

    // Fetch what the bank held back, once the run that hit the release has
    // finished. Guarded four ways: only after a release the server ACCEPTED,
    // only when no run is going, only once per armed release, and never from
    // inside a run that is itself a continuation.
    function maybeResume() {
        if (!resumeWanted) return;
        if (run && run.busy) return;
        resumeWanted = false;
        // At most one automatic continuation per run the user started. Even
        // an accepted release can come back needing another one -- a bank
        // entitled to ask again for the next command -- and then this would
        // start a run that starts a run. One is a continuation; two is a
        // loop. What is still unfetched after it stays on the panel, behind
        // the link that exists for exactly that.
        if (run && run.resumed) return;
        var again = unfinished();
        if (!again.length) return;
        var opts = (run && run.options) || {};
        frappe.show_alert({
            message: __("Release accepted — fetching the {0} accounts that"
                        + " were waiting for it.", [again.length]),
            indicator: "blue"
        }, 7);
        kefiya.bank_refresh(Object.assign({}, opts, {
            only: again, resumed: true
        }));
    }

    // One account's outcome, whoever fetched it. The worker and the
    // account-by-account fallback below hand in the same summary, so they must
    // read it the same way -- two copies of this drifted once and the log said
    // "2 neu" for every account in the run.
    function noteResult(ln, x, failure) {
        if (!run) return "err";
        if (failure) {
            record(ln, "err",
                [{ label: __("Fetch"), text: __("failed"), kind: "err" }],
                String(failure).slice(0, 600));
            return "err";
        }
        x = x || {};
        var t = x.transactions || {};
        var state = "ok";
        if (x.skipped || t.status === "skipped") state = "skip";
        else if (x.tan_required || t.status === "tan_required") state = "tan";
        run.tot += (t.new_count || 0);
        record(ln, state, kefiya.fetch_log_lines(x));
        return state;
    }

    function progressLabel(btn, total) {
        if (btn) {
            btn.textContent = __("Fetching …") + " ("
                + run.done + "/" + total + ")";
        }
    }

    // Runs a worker is holding, by the id start_fetch_group gave them. The
    // realtime handlers are bound once for the page, so this map is how an
    // event finds the group it belongs to.
    var pending = {};

    function bindGroupRealtime() {
        if (kefiya._bank_refresh_group_realtime) return;
        kefiya._bank_refresh_group_realtime = true;
        frappe.realtime.on("kefiya_fetch_progress", function (d) {
            var g = d && pending[d.run];
            if (!g) return;
            g.seen();
            noteResult(d.login, d.summary, d.error);
            progressLabel(g.btn, g.total);
        });
        frappe.realtime.on("kefiya_fetch_group_done", function (d) {
            var g = d && pending[d.run];
            if (!g) return;
            g.done();
        });
    }

    // How long a group may say nothing before the run stops waiting for it.
    // A worker that dies -- killed, out of memory, a bank that never answers
    // -- publishes no closing event, and without this the panel would sit at
    // "fetching" until the page is reloaded.
    var GROUP_SILENCE_MS = 10 * 60 * 1000;

    // One bank access, one dialog, all of its accounts -- the whole point of
    // fetch_group, which the server has had all along and nobody called. The
    // browser used to fetch account by account, so thirty accounts on one
    // access meant thirty handshakes and thirty strong authentications
    // against the same bank.
    function fetchWholeAccess(logins, btn, total) {
        return new Promise(function (res) {
            frappe.call({
                method: "kefiya.utils.client.start_fetch_group",
                args: { logins: logins, user_scope: logins[0] },
                silent: true,
                callback: function (r) {
                    var id = r && r.message && r.message.run;
                    if (!id) {
                        failAll(logins, __("The server accepted the request"
                            + " but named no run."));
                        res(null);
                        return;
                    }
                    var timer = null;
                    var settled = false;
                    var finish = function () {
                        if (settled) return;
                        settled = true;
                        if (timer) clearTimeout(timer);
                        delete pending[id];
                        res(null);
                    };
                    var arm = function () {
                        if (timer) clearTimeout(timer);
                        timer = setTimeout(function () {
                            // Whatever never reported is neither fetched nor
                            // failed as far as we know. Say that, rather than
                            // leave the bar short of its total for good.
                            logins.forEach(function (ln) {
                                if (!recorded(ln)) {
                                    noteResult(ln, null, __(
                                        "The worker stopped reporting. Fetch"
                                        + " this access again."));
                                }
                            });
                            finish();
                        }, GROUP_SILENCE_MS);
                    };
                    pending[id] = { btn: btn, total: total,
                                    seen: arm, done: finish };
                    arm();
                },
                // There used to be a second, account-by-account fetch path
                // here as a fallback. It carried its own copy of the rule that
                // stops an access at a parked release -- the same rule
                // fetch_group applies on the server, in another language, with
                // its own translated sentence. Two implementations of one rule
                // is one that gets forgotten.
                //
                // What it guarded against was a client and a server out of
                // step, and those ship as one app. The one real window is a
                // browser holding stale JS for a few minutes after a deploy,
                // and the honest answer there is the one every other server
                // error already gets: say so.
                error: function (r) {
                    failAll(logins, kefiya.call_error(r) || __("Error"));
                    res(null);
                }
            });
        });
    }

    // Every account of an access that never got started. Used where the run
    // cannot say anything about them individually, so saying nothing would
    // leave the bar short of its total for good.
    function failAll(logins, why) {
        logins.forEach(function (ln) {
            if (!recorded(ln)) noteResult(ln, null, why);
        });
    }

    function recorded(ln) {
        return ((run && run.log) || []).some(function (e) {
            return e.ln === ln;
        });
    }

    kefiya.bank_refresh = function (options) {
        options = options || {};
        var btn = options.btn || null;
        var label = options.buttonLabel || (btn && btn.textContent)
            || idleLabel();
        var release = function () {
            if (btn) { btn.disabled = false; btn.textContent = label; }
        };

        if (run && run.busy) {
            frappe.show_alert({
                message: __("An account fetch is already running."),
                indicator: "orange"
            }, 5);
            return Promise.resolve(null);
        }

        // Without read access frappe.db.get_list returns an empty list, which
        // would otherwise read as "no bank access configured".
        if (frappe.model && frappe.model.can_read
                && !frappe.model.can_read("Kefiya Login")) {
            frappe.msgprint({
                title: __("No permission"),
                message: __("You do not have permission to fetch the bank accesses."),
                indicator: "red"
            });
            return Promise.resolve(null);
        }

        bindRealtime();
        bindGroupRealtime();
        if (btn) { btn.disabled = true; btn.textContent = __("Checking …"); }

        // A restricted run still asks the server for the list: `only` names
        // logins, and a name that has meanwhile been deleted or had its
        // account removed must drop out rather than fail the run.
        var wanted = null;
        if (options.only && options.only.length) {
            wanted = {};
            options.only.forEach(function (ln) { wanted[ln] = true; });
        }

        return frappe.db.get_list("Kefiya Login", {
            fields: ["name", "account_iban"], limit: 200
        }).then(function (rows) {
            var logins = (rows || [])
                .filter(function (r) { return r.account_iban; })
                .filter(function (r) { return !wanted || wanted[r.name]; })
                .map(function (r) { return r.name; });
            if (!logins.length) {
                frappe.msgprint(wanted
                    ? __("None of these accesses is still configured.")
                    : __("No bank access with an account configured."));
                release();
                return null;
            }
            // Panels used to stack: the second run drew its own above the
            // first, and the finished one stayed to be read as current.
            if (run && run.panel && run.panel.isConnected) {
                run.panel.parentNode.removeChild(run.panel);
            }
            run = {
                busy: true, order: logins.slice(), log: [],
                tot: 0, ok: 0, tan: 0, fail: 0, skip: 0, done: 0,
                mount: options.mount || null, panel: null, logDialog: null,
                onRefreshView: options.onRefreshView || null,
                options: options,
                resumed: !!options.resumed
            };
            render();

            muteErrors();
            // The server says which accesses share a bank; each group is one
            // chain, the chains run beside each other. Where the call is not
            // available, everything stays in a single chain.
            return new Promise(function (res) {
                frappe.call({
                    method: "kefiya.utils.client.get_fetch_groups",
                    silent: true,
                    callback: function (r) {
                        var g = ((r && r.message) || []).map(function (x) {
                            return x.logins || [];
                        });
                        res(g.length ? g : [logins]);
                    },
                    error: function () { res([logins]); }
                });
            }).then(function (groups) {
                // The server leaves out accesses marked "skip". Without this
                // reconciliation the progress counted against a total that is
                // never reached, and the bar stopped short.
                var known = {};
                logins.forEach(function (ln) { known[ln] = true; });
                // get_fetch_groups answers for every access. In a restricted
                // run the groups are the right chains but the wrong contents,
                // so `known` is what decides -- it already holds only the
                // logins this run is allowed to touch.
                var planned = [];
                groups.forEach(function (grp) {
                    grp.forEach(function (ln) {
                        if (known[ln]) planned.push(ln);
                    });
                });
                if (planned.length) run.order = planned;
                var total = run.order.length;

                var chains = groups.map(function (grp) {
                    var mine = grp.filter(function (ln) { return known[ln]; });
                    if (!mine.length) return Promise.resolve();
                    // The whole access in one go. The stop at a parked
                    // release lives in fetch_group now: the server is the
                    // only one that sees it the moment it happens, and it is
                    // the one holding the dialog the release belongs to.
                    return fetchWholeAccess(mine, btn, total);
                });
                return Promise.all(chains).then(function () {
                    run.busy = false;
                    render();
                    unmuteErrors();
                    cleanupBackdrops();
                    release();
                    var summary = { total: run.tot, ok: run.ok, tan: run.tan,
                                    fail: run.fail, accounts: total };
                    frappe.show_alert({
                        message: __("Account fetch done") + " · "
                            + __("{0} new transactions", [run.tot]) + " · "
                            + run.ok + "/" + total + " "
                            + __("accounts")
                            + (run.tan
                                ? (" · " + __("{0}× awaiting release", [run.tan]))
                                : "")
                            + (run.fail
                                ? (" · " + __("{0} failed", [run.fail])) : ""),
                        indicator: (run.tan || run.fail) ? "orange" : "green"
                    }, 9);
                    if (options.onDone) {
                        try { options.onDone(summary); } catch (ignoredC) {}
                    }
                    // A release given while this run was going: the accounts
                    // the bank held back are fetched now, without the user
                    // having to find a link for it.
                    maybeResume();
                    return summary;
                });
            });
        }).catch(function (r) {
            if (run) { run.busy = false; render(); }
            unmuteErrors();
            cleanupBackdrops();
            release();
            maybeResume();
            frappe.msgprint(__("The account fetch was aborted.") + " "
                + kefiya.call_error(r));
            return null;
        });
    };
})();
