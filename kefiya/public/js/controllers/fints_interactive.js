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

	progressbar: function(frm) {
		this.progressState[frm.doc.name] = {
			progress: 0,
			completed: false,
		};

		frappe.realtime.off("fints_progressbar");
		frappe.realtime.on("fints_progressbar", function(data) {
			if(data.docname === frm.doc.name) {
				const state = kefiya.interactive.progressState[frm.doc.name] || {
					progress: 0,
					completed: false,
				};

				if (state.completed && data.progress < 100) {
					return;
				}

				if (data.progress < state.progress) {
					return;
				}

				state.progress = data.progress;
				state.completed = data.progress >= 100;
				kefiya.interactive.progressState[frm.doc.name] = state;

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

			await kefiya.interactive.enqueueUpdate(() => {
				// Step out of the way for the TAN prompt, but keep the session
				// log: hide() retains the entries and the next update()
				// restores the window with its history intact.
				kefiya.progress.update(title, 0);
				kefiya.progress.hide();
			});

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
					frappe.call({
						method: "kefiya.utils.client.resolve_tan_interaction",
						args: {
							// The login the run belongs to, which is this form only
							// on the Kefiya Login screen. A transfer's box used to
							// answer against its own KEF-TRF-... name.
							fints_login: data.fints_login || frm.doc.name,
							values: { ...data, ...values },
						},
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
