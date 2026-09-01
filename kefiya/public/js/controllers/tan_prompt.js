// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// The box that asks for a TAN, or says the release happens in the banking app.
//
//   kefiya.tan_prompt(data, done)     data is the fints_tan_interaction_required
//                                     payload; done fires once the answer is in
//   kefiya.focus_tan_field(dialog)    the focus rule on its own, for a caller
//                                     that builds its own box
//
// Its own file because it has three callers -- the collective fetch, the
// outgoing payments, and the Kefiya Login form -- and no share in what any of
// them is doing otherwise. It sat in bank_refresh.js for historical reasons
// while already being exported from it, which is the definition of the wrong
// home.
//
// Two copies of this drifted apart once already: the second one named neither
// the bank nor the account, which is the whole reason the context lines exist.

frappe.provide("kefiya");

(function () {
    "use strict";

    // The boxes that are open right now, by the login they belong to. A
    // decoupled release is confirmed on a phone, so the answer arrives from
    // the server rather than from this box -- and a box asking for something
    // that has already been given is a box the user has to dismiss for no
    // reason.
    var openBoxes = {};

    function bindReleaseArrived() {
        if (kefiya._tan_release_realtime) return;
        kefiya._tan_release_realtime = true;
        frappe.realtime.on("kefiya_release_arrived", function (d) {
            var box = d && openBoxes[d.docname];
            if (!box) return;
            delete openBoxes[d.docname];
            // hide(), not the primary action: the release already went
            // through on the server. Running the action would answer a
            // challenge that is no longer waiting.
            try { box.hide(); } catch (ignored) {}
            frappe.show_alert({
                message: __("Release received — carrying on."),
                indicator: "green"
            }, 5);
        });
    }

    function tanTitle(data) {
        return data.account_label
            ? __("Verification required") + " – " + data.account_label
            : __("Verification required");
    }

    function tanContextField(data) {
        if (!data.account_detail) return null;
        return {
            fieldtype: "HTML", fieldname: "kefiya_tan_context",
            options: '<div class="text-muted small" '
                + 'style="margin-bottom:8px">'
                + frappe.utils.escape_html(data.account_detail) + "</div>"
        };
    }

    // What the bank asks, where it does not ask in words. comdirect sends a
    // photoTAN -- a coloured mosaic the phone app reads before it shows the
    // digits -- and a Sparkasse sends chipTAN-QR the same way. Without the
    // picture the box asks for a TAN and shows nothing to scan.
    //
    // Sized in millimetres, not pixels: a photoTAN app reads the mosaic off
    // the screen, and on a high-resolution display a picture given in pixels
    // comes out too small to focus on. 40 mm is what the banks' own web
    // interfaces use.
    function tanChallengeField(data) {
        var c = data.challenge;
        if (!c || (!c.image && !c.text)) return null;

        var html = '<div class="kefiya-tan-challenge" '
            + 'style="text-align:center;margin-bottom:10px">';
        if (c.image && c.image.data) {
            html += '<img alt="' + __("Bank challenge") + '" '
                + 'style="width:40mm;height:40mm;image-rendering:pixelated;'
                + 'border:1px solid var(--border-color);border-radius:4px;'
                + 'background:#fff;padding:4px" src="data:'
                + frappe.utils.escape_html(c.image.mime || "image/png")
                + ";base64," + frappe.utils.escape_html(c.image.data) + '">';
            html += '<div class="text-muted small" style="margin-top:6px">'
                + __("Scan this with your banking app, then enter the TAN it"
                    + " shows.") + "</div>";
        }
        if (c.text) {
            html += '<div class="small" style="margin-top:6px;text-align:left">'
                + frappe.utils.escape_html(c.text) + "</div>";
        }
        return { fieldtype: "HTML", fieldname: "kefiya_tan_challenge",
                 options: html + "</div>" };
    }

    // done(ok) fires once the answer has been handed to the server: true when
    // it was accepted, false when it was refused. A caller that acts on the
    // release MUST look at the flag.
    function tanPrompt(data, done) {
        var fields = [];
        if (data.possible_tan_modes) {
            fields.push({ fieldtype: "Select", fieldname: "tan_mode",
                label: __("TAN Mode"), options: data.possible_tan_modes,
                reqd: 1 });
            if (data.possible_tan_mediums) {
                fields[0].default = data.possible_tan_modes[0];
                fields[0].read_only = 1;
                fields.push({ fieldtype: "Select", fieldname: "tan_medium",
                    label: __("TAN Medium"),
                    options: data.possible_tan_mediums, reqd: 1 });
            }
        }
        if (data.tan_required || data.mfa_required) {
            if (data.possible_tan_modes && !fields[0].default) {
                fields[0].default = data.possible_tan_modes[0];
                fields[0].read_only = 1;
            }
            if (data.possible_tan_mediums) {
                fields[1].default = data.possible_tan_mediums[0];
                fields[1].read_only = 1;
            }
            fields.push({ fieldtype: "Check", fieldname: "mfa_confirmation",
                label: __("Confirm MFA"), default: 1, hidden: 1 });
            if (data.tan_required) {
                fields.push({ fieldtype: "Data", fieldname: "tan",
                    label: __("TAN"), reqd: 1 });
            } else if (data.mfa_required) {
                fields.push({ fieldtype: "HTML", fieldname: "waiting",
                    label: __("Waiting for Interaction"),
                    options: __("Follow the instructions on your banking app or device.") });
            }
        }
        // Prepended only now: everything above addresses the fields by index
        // (fields[0] is the mode, fields[1] the medium), so an entry inserted
        // ahead of them earlier would silently make the mode read-only field
        // the wrong one.
        var context = tanContextField(data);
        if (context) fields.unshift(context);
        // Above the account line: the picture is the question, and the
        // question belongs at the top.
        var challenge = tanChallengeField(data);
        if (challenge) fields.unshift(challenge);

        var dialog = frappe.prompt(fields, function (values) {
            // Explicit feedback, because the run mutes the standard error box.
            frappe.call({
                method: "kefiya.utils.client.resolve_tan_interaction",
                args: { fints_login: data.fints_login || data.docname,
                        values: Object.assign({}, data, values) },
                silent: true,
                // done(ok). The flag is the whole point: it used to fire the
                // same way whether the release went through or not, and the
                // caller took that as "released, carry on". A refused release
                // then started a fetch, the bank asked again, the box came
                // back, and the user confirmed their way around that circle
                // for as long as they were willing to.
                callback: function () { if (done) done(true); },
                error: function (r) {
                    frappe.show_alert({
                        message: __("TAN release failed.") + " "
                            + kefiya.call_error(r),
                        indicator: "red"
                    }, 10);
                    if (done) done(false);
                }
            });
        }, tanTitle(data));

        // Into the TAN field, not into the dialog. The mode and the medium
        // above it are read-only -- the only thing to do here is type six
        // digits, and Frappe leaves the focus on the dialog itself, so it took
        // a click first. A TAN expires while you look for the cursor.
        //
        // On the decoupled procedure there IS no TAN field: the release
        // happens in the banking app and the dialog only says so. Focusing
        // whatever happens to be first would put the cursor somewhere the user
        // must not change, so nothing is focused at all.
        focusTanField(dialog);

        // Only the decoupled box waits on the server; the others are answered
        // in the box itself.
        if (data.mfa_required && data.docname) {
            bindReleaseArrived();
            openBoxes[data.docname] = dialog;
            dialog.$wrapper.on("hidden.bs.modal", function () {
                delete openBoxes[data.docname];
            });
        }
        return dialog;
    }

    // frappe.prompt resolves its dialog synchronously but shows it through a
    // Bootstrap transition, so the input is not focusable at the moment the
    // call returns. shown.bs.modal is the event that says it is; the timeout
    // is the fallback for a desk that renders the modal without the
    // transition, where the event has already fired.
    function focusTanField(dialog) {
        if (!dialog || !dialog.get_field) return;
        var put = function () {
            try {
                var f = dialog.get_field("tan");
                if (f && f.$input) f.$input.focus().select();
            } catch (ignored) {}
        };
        try { dialog.$wrapper.on("shown.bs.modal", put); } catch (ignoredA) {}
        setTimeout(put, 300);
    }

    kefiya.tan_prompt = tanPrompt;

    // Exported on its own as well: the Kefiya Login form builds its own box
    // (it has the form's docname where this one has a login from a run) and
    // takes the focus rule from here rather than carrying a copy.
    kefiya.focus_tan_field = focusTanField;
})();
