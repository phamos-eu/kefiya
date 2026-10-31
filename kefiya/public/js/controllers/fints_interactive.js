// Copyright (c) 2019, jHetzer and contributors
// For license information, please see license.txt

frappe.provide("kefiya.interactive");

kefiya.interactive = {
	updateQueue: Promise.resolve(),
	progressState: {},

	enqueueUpdate: function(fn, delay) {
		this.updateQueue = this.updateQueue
			.then(() => {
				// call the function
				return Promise.resolve(fn());
			})
			.then(() => {
				if (!delay) {
					return;
				}

				// delay the next update only when explicitly requested
				return new Promise((resolve) => setTimeout(resolve, delay));
			});

		return this.updateQueue;
	},

	/**
	 * Button of the "TAN Required" dialog (fints_controller_legacy):
	 * switch TAN authentication on for the Kefiya Login and try again.
	 */
	use_tan_and_try_again: async function(kefiya_login) {
		frappe.hide_msgprint();
		const frm = cur_frm;

		if (frm && frm.doctype === "Kefiya Login" && frm.docname === kefiya_login) {
			// set_value + save keeps other unsaved edits on the form
			await frm.set_value("enable_tan_authentication", 1);
			await frm.save();
			frm.events.call_get_login_accounts(frm);
			return;
		}

		await frappe.db.set_value("Kefiya Login", kefiya_login, "enable_tan_authentication", 1);
		if (frm && frm.doctype === "Kefiya Import") {
			frm.events.call_import_transaction(frm);
		} else {
			frappe.show_alert({
				message: __("TAN authentication is on for {0}. Please try again.", [kefiya_login]),
				indicator: "green",
			});
		}
	},

	/**
	 * After the bank accepted the TAN, carry on with what the user started,
	 * so they don't have to click it again: on Kefiya Login, offer the
	 * accounts that were loaded with the TAN. (The server's own "reload"
	 * comes by socket and usually arrives after the request has ended, when
	 * it is ignored.)
	 */
	continue_after_tan: function(frm, result) {
		if (frm.doctype === "Kefiya Login" && result.accounts) {
			frm.events.choose_account(frm, result.accounts);
		}
	},

	/**
	 * Mark a FinTS request as started in this tab. Progress and TAN events go
	 * by socket to every tab of the user; only the tab that started the
	 * request follows them, so other open tabs don't stack dialogs.
	 */
	start_progress: function(frm) {
		this.progressState[frm.doc.name] = {
			progress: 0,
			active: true,
		};
	},

	/** True while this tab waits for the FinTS request it started. */
	is_active: function(frm) {
		const state = this.progressState[frm.doc.name];
		return Boolean(state && state.active);
	},

	/**
	 * Close the progress dialog once the request has ended or needs the user.
	 * Progress events arrive by socket and can come later; they are ignored
	 * then, so they don't open the dialog again on top of a message or prompt.
	 */
	finish_progress: function(frm) {
		this.progressState[frm.doc.name] = {
			progress: 100,
			active: false,
		};
		return this.enqueueUpdate(() => {
			const dialog = frappe.cur_progress;
			frappe.hide_progress();
			// Bootstrap ignores hide() while the dialog still fades in, which
			// left it on screen on top of the next dialog. Hide it once shown.
			if (dialog) {
				dialog.$wrapper.one("shown.bs.modal", () => dialog.$wrapper.modal("hide"));
			}
		});
	},

	progressbar: function(frm) {
		frappe.realtime.off("fints_progressbar");
		frappe.realtime.on("fints_progressbar", function(data) {
			if(data.docname === frm.doc.name) {
				const state = kefiya.interactive.progressState[frm.doc.name];

				if (!kefiya.interactive.is_active(frm) || data.progress < state.progress) {
					return;
				}

				state.progress = data.progress;
				state.active = data.progress < 100;

				kefiya.interactive.enqueueUpdate(() => {
					if(data.reload && data.reload === true) {
						frm.reload_doc();
					}
					if(data.progress==100) {
						frappe.hide_progress();
					} else {
						frappe.show_progress(data.docname,data.progress,100,data.message);
					}

					if(data.reload && data.reload === true) {
						// reload with short delay, to have progress update come active before
						kefiya.interactive.enqueueUpdate(() => {
							frm.reload_doc()
						}, 500);
					}
				}, 0);
			}
		});

		/**
		 * Handle TAN interaction requirements for several steps:
		 * 1. TAN Mode selection
		 * 1.2. TAN Medium selection (if available)
		 * 2. Later User Interaction after Push TAN fulfillment or TAN entered
		 */
		frappe.realtime.off("fints_tan_interaction_required");
		frappe.realtime.on("fints_tan_interaction_required", async function(data) {
			if(data.docname !== frm.doc.name || !kefiya.interactive.is_active(frm)) {
				return;
			}

			await kefiya.interactive.finish_progress(frm);

			let fields = [];

			if (data.possible_tan_modes) {
				fields.push(
					{
						fieldtype: "Select",
						fieldname: "tan_mode",
						label: __("TAN Mode"),
						options: data.possible_tan_modes,
						reqd: 1,
					},
				);

				if (data.possible_tan_mediums) {
					// if mediums are available for the selected mode, keep previously selected mode read-only
					fields[0].default = data.possible_tan_modes[0];
					fields[0].read_only = 1;

					fields.push(
						{
							fieldtype: "Select",
							fieldname: "tan_medium",
							label: __("TAN Medium"),
							options: data.possible_tan_mediums,
							reqd: 1,
						},
					);
				}
			}

			// TAN Mode selection
			if (data.tan_required || data.mfa_required) {
				if (data.possible_tan_modes && !fields[0].default) {
					fields[0].default = data.possible_tan_modes[0];
					fields[0].read_only = 1;
				}

				// if we're on confirmation already, keep previously selected mode read-only
				if (data.possible_tan_mediums) {
					const disableIdx = data.possible_tan_mediums ? 1 : 0;
					fields[disableIdx].default = data.possible_tan_mediums[disableIdx];
					fields[disableIdx].read_only = 1;
				}

				// User Interaction requirement for 2FA
				fields.push(
					{
						fieldtype: "Check",
						fieldname: "mfa_confirmation",
						label: __("Confirm MFA"),
						default: 1,
						hidden: 1,
					},
				);

				// The bank's instructions and, for chipTAN QR or photoTAN, the code to scan.
				// The server sends the text HTML-safe and the image as a data URI.
				if (data.challenge) {
					let challenge_html = "";
					if (data.challenge.text) {
						challenge_html += `<p>${data.challenge.text}</p>`;
					}
					if (data.challenge.image) {
						// white on purpose, also in dark mode: TAN generators read a
						// transparent QR code badly on a dark background
						challenge_html += `<img src="${data.challenge.image}" alt="${__("TAN challenge")}" style="max-width: 100%; background: #fff; padding: var(--padding-sm);">`;
					}
					fields.push(
						{
							fieldtype: "HTML",
							fieldname: "challenge",
							options: challenge_html,
						},
					);
				}

				if (data.tan_required) {
					fields.push(
						{
							fieldtype: "Data",
							fieldname: "tan",
							label: __("TAN"),
							reqd: 1,
						},
					);
				} else if (data.mfa_required) {
					fields.push(
						{
							fieldtype: "HTML",
							fieldname: "waiting",
							label: __("Waiting for Interaction"),
							options: __("Follow the instructions on your banking app or device."),
						},
					);
				}
			}

			const prompt = frappe.prompt(
				fields,
				(values) => {
					kefiya.interactive.start_progress(frm);
					// the server doesn't need the challenge (and its image) back
					const { challenge, ...context } = data;
					frappe.call({
						method: "kefiya.utils.client.resolve_tan_interaction",
						args: {
							fints_login: frm.doc.name,
							values: { ...context, ...values },
						},
						always: () => kefiya.interactive.finish_progress(frm),
					}).then((r) => {
						// after "always", so the next request's progress isn't cut off
						if (r && r.message && r.message.tan_resolved) {
							kefiya.interactive.continue_after_tan(frm, r.message);
						}
					});
				},
				__("Verification required")
			);

			// Frappe focuses the first field, here the read-only TAN Mode.
			// Put the cursor in the TAN field instead (after Frappe's own focus).
			if (data.tan_required) {
				prompt.$wrapper.one("shown.bs.modal", () => prompt.fields_dict.tan.set_focus());
			}
		});
	},
}
