// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

/**
 * Follow one FinTS request started from the Kefiya page: progress and TAN
 * prompts come by socket (fints_progressbar, fints_tan_interaction_required).
 *
 * The server tags events with the request's user_scope, and after a TAN with
 * the login name (client.resolve_tan_interaction), so both are accepted.
 * Unlike kefiya.interactive, this keeps its own handlers and removes only
 * those, so forms open at the same time keep theirs.
 */
kefiya.hub.FintsSession = class FintsSession {
	constructor({ scope, kefiya_login, on_progress, on_tan_resolved }) {
		this.scopes = [scope, kefiya_login].filter(Boolean);
		this.kefiya_login = kefiya_login;
		this.on_progress = on_progress;
		this.on_tan_resolved = on_tan_resolved;
		this.progress = 0;
		this.active = false;
		this.handle_progress = this.handle_progress.bind(this);
		this.handle_tan = this.handle_tan.bind(this);
	}

	/**
	 * Run a frappe.call while following its progress and TAN events.
	 * Resolves with { ok, message }; ok is false when the server raised
	 * (Frappe has already shown its message then).
	 */
	run(call_opts) {
		this.start();
		return new Promise((resolve) => {
			frappe.call({
				...call_opts,
				callback: (r) => resolve({ ok: true, message: r && r.message }),
				error: () => resolve({ ok: false }),
				always: () => this.stop(),
			});
		});
	}

	start() {
		this.progress = 0;
		this.active = true;
		if (!this.subscribed) {
			frappe.realtime.on("fints_progressbar", this.handle_progress);
			frappe.realtime.on("fints_tan_interaction_required", this.handle_tan);
			this.subscribed = true;
		}
	}

	stop() {
		this.active = false;
		this.stopped_at = Date.now();
		this.report({ progress: 100, message: "" });
	}

	destroy() {
		this.active = false;
		this.subscribed = false;
		frappe.realtime.off("fints_progressbar", this.handle_progress);
		frappe.realtime.off("fints_tan_interaction_required", this.handle_tan);
	}

	/**
	 * A TAN request ends the HTTP call, and its socket event can arrive just
	 * after that, so TAN events are still accepted for a few seconds.
	 */
	is_mine(data, grace_ms = 0) {
		const recent = this.stopped_at && Date.now() - this.stopped_at < grace_ms;
		return (this.active || recent) && this.scopes.includes(data.docname);
	}

	handle_progress(data) {
		// socket events can arrive late and out of order
		if (!this.is_mine(data) || data.progress < this.progress) return;
		this.progress = data.progress;
		this.report(data);
	}

	report(data) {
		if (this.on_progress) {
			this.on_progress(data);
		} else if (data.progress >= 100) {
			const dialog = frappe.cur_progress;
			frappe.hide_progress();
			// Bootstrap ignores hide() while the dialog still fades in, which
			// leaves it on screen (same fix as kefiya.interactive.finish_progress)
			if (dialog) {
				dialog.$wrapper.one("shown.bs.modal", () => dialog.$wrapper.modal("hide"));
			}
		} else {
			frappe.show_progress(__("Bank connection"), data.progress, 100, data.message);
		}
	}

	handle_tan(data) {
		if (!this.is_mine(data, 5000)) return;
		this.stopped_at = null;
		this.report({ progress: 100, message: "" });

		// The one TAN box (tan_prompt.js), as on the forms. It was built here
		// a third time, field by field.
		kefiya.tan_prompt(data, (ok, message) => {
			this.stop();
			if (ok && message && message.status === "submitted" && this.on_tan_resolved) {
				this.on_tan_resolved(message);
			}
		}, {
			// Nothing reports a release to this page; the box is how the
			// user says it is given.
			confirm_here: true,
			before_answer: () => this.start(),
		});
	}
};
