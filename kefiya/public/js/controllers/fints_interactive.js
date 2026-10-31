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
	 * Which FinTS request of a document this tab follows. One state per
	 * docname, and one place that reads it (accepts_progress):
	 *
	 *   no entry   untracked: the form never called start_progress -- most of
	 *              kefiya's FinTS buttons don't. Every progress message is
	 *              shown, as it always was; going silent would hide a fetch.
	 *   running    this tab started the request (start_progress) and shows
	 *              its progress, never going backwards.
	 *   done       the request ended or stopped for the user. Late socket
	 *              messages are ignored, so they don't reopen the progress on
	 *              top of a message or prompt. Progress and TAN events go to
	 *              every tab of the user; only the starting tab follows them.
	 */
	start_progress: function(frm) {
		this.progressState[frm.doc.name] = { phase: "running", progress: 0 };
	},

	/** A request this tab follows has ended or needs the user. */
	stop_following: function(frm) {
		const state = this.progressState[frm.doc.name];
		if (state) {
			state.phase = "done";
		}
	},

	/** Whether to show this progress message, recording it if so. */
	accepts_progress: function(frm, progress) {
		const state = this.progressState[frm.doc.name];
		if (!state) {
			return true;
		}
		if (state.phase === "done" || progress < state.progress) {
			return false;
		}
		state.progress = progress;
		if (progress >= 100) {
			state.phase = "done";
		}
		return true;
	},

	/**
	 * Close the progress dialog once the request has ended or needs the user.
	 * Progress events arrive by socket and can come later; they are ignored
	 * then, so they don't open the dialog again on top of a message or prompt.
	 */
	finish_progress: function(frm) {
		this.progressState[frm.doc.name] = { phase: "done", progress: 100 };
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
				if (!kefiya.interactive.accepts_progress(frm, data.progress)) {
					return;
				}

				kefiya.interactive.enqueueUpdate(() => {
					// One persistent window collecting every message, instead
					// of show_progress/hide_progress flashing a separate one
					// per step and per account.
					kefiya.progress.update(
						data.message, data.progress, data.docname
					);

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
			// Not gated on the tab's progress state, unlike the progress above. The TAN box
			// is the only way to release what is waiting at the bank, and on
			// most forms nothing called start_progress -- the transfer form's
			// send among them. The event can also arrive after the request
			// that published it has ended. A box that does not open there
			// leaves an order unreleasable; a second box in another tab only
			// asks the same question twice.
			if(data.docname !== frm.doc.name) {
				return;
			}

			// "Freigabe erforderlich" named no bank and no account. During a
			// collective run one access after the other stops for a release
			// and the box looked identical every time, so the user had to
			// guess which banking app to open. The payload now carries the
			// access it belongs to.
			const title = data.account_label
				? __("Verification required") + " – " + data.account_label
				: __("Verification required");

			// A request this tab follows has stopped for the user.
			kefiya.interactive.stop_following(frm);

			await kefiya.interactive.enqueueUpdate(() => {
				// Step out of the way for the TAN prompt, but keep the session
				// log: hide() retains the entries and the next update()
				// restores the window with its history intact.
				kefiya.progress.update(title, 0);
				kefiya.progress.hide();
			});

			// A transfer's box answers for the order, not for the login. The
			// box built below sends the TAN to resolve_tan_interaction, which
			// releases the parked order at the bank and marks nothing: the
			// transfer stayed "Approved" with its money gone, and once the
			// send lock ran out (10 minutes) "Send now" would have paid it a
			// second time. tan_prompt with the transfer answers through
			// send_transfer_tan, which reads what the bank said and marks the
			// order sent only when it was released.
			if (frm.doctype === "Kefiya Transfer" && kefiya.tan_prompt) {
				kefiya.tan_prompt(data, () => frm.reload_doc(), {
					transfer: {
						login: data.fints_login || frm.doc.kefiya_login,
						names: [frm.doc.name],
					},
				});
				return;
			}

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

				// The bank's instructions and, for chipTAN QR or photoTAN, the code
				// to scan -- rendered by tan_prompt.js, which escapes the bank's text.
				const challenge_field = data.challenge && kefiya.tan_challenge_field
					? kefiya.tan_challenge_field(data)
					: null;
				if (challenge_field) {
					fields.push(challenge_field);
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

			// Prepended only now: everything above addresses the fields by
			// index (fields[0] is the mode, fields[1] the medium), so an entry
			// inserted ahead of them earlier would silently make the wrong
			// field read-only.
			if (data.account_detail) {
				fields.unshift({
					fieldtype: "HTML",
					fieldname: "kefiya_tan_context",
					options: '<div class="text-muted small" style="margin-bottom:8px">'
						+ frappe.utils.escape_html(data.account_detail)
						+ "</div>",
				});
			}

			const dialog = frappe.prompt(
				fields,
				(values) => {
					kefiya.interactive.start_progress(frm);
					// the server doesn't need the challenge (and its image) back
					const { challenge, ...context } = data;
					frappe.call({
						method: "kefiya.utils.client.resolve_tan_interaction",
						args: {
							// The login the run belongs to, which is this form only
							// on the Kefiya Login screen. A transfer's box used to
							// answer against its own KEF-TRF-... name.
							fints_login: data.fints_login || frm.doc.name,
							values: { ...context, ...values },
						},
						always: () => kefiya.interactive.finish_progress(frm),
					}).then((r) => {
						// after "always", so the next request's progress isn't cut off
						if (r && r.message && r.message.status === "submitted") {
							kefiya.interactive.continue_after_tan(frm, r.message);
						}
					});
				},
				title
			);

			// Straight into the TAN field: the mode and the medium above it
			// are read-only, so the only thing to do here is type. The rule
			// itself lives in bank_refresh.js, which owns the other TAN box.
			if (kefiya.focus_tan_field) kefiya.focus_tan_field(dialog);
		});
	},
}
