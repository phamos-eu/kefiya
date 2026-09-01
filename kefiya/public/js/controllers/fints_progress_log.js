// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

/**
 * A single, persistent progress window for FinTS operations.
 *
 * The previous behaviour called frappe.show_progress() per message and
 * frappe.hide_progress() on every completed step. Fetching several accounts --
 * or several data types for one account via "fetch all" -- therefore flashed
 * one window after another and left no trace of what had happened.
 *
 * This keeps one window open for the whole run and appends every message as a
 * timestamped line, the way a classic banking client shows its session log.
 * The window stays open when the run finishes so the log stays readable; the
 * user closes it.
 */
frappe.provide("kefiya.progress");

kefiya.progress = {
	dialog: null,
	$log: null,
	$bar: null,
	$status: null,
	finished: false,

	// The log lives here, not in the DOM. A TAN prompt hides the window
	// mid-run, and the session log has to survive that and be restored when
	// the window comes back.
	entries: [],
	percent: 0,

	/** Open the window if needed and return it. */
	ensure: function (title) {
		if (this.dialog && this.dialog.$wrapper && this.dialog.display) {
			return this.dialog;
		}

		this.dialog = new frappe.ui.Dialog({
			title: title || __("Bank transfer session"),
			size: "large",
			primary_action_label: __("Close"),
			primary_action: () => this.dialog.hide(),
		});

		this.dialog.$body.html(`
			<div class="kefiya-progress">
				<div class="progress" style="height: 10px; margin-bottom: 8px;">
					<div class="progress-bar kefiya-progress-bar"
						role="progressbar" style="width: 0%;"></div>
				</div>
				<div class="text-muted kefiya-progress-status"
					style="margin-bottom: 8px;">${__("Starting...")}</div>
				<div class="kefiya-progress-log"
					style="max-height: 340px; overflow-y: auto;
					       font-family: var(--font-stack-monospace, monospace);
					       font-size: 11px; line-height: 1.7;
					       background: var(--control-bg, #f4f5f6);
					       border-radius: 6px; padding: 8px 10px;"></div>
			</div>
		`);

		this.$bar = this.dialog.$body.find(".kefiya-progress-bar");
		this.$status = this.dialog.$body.find(".kefiya-progress-status");
		this.$log = this.dialog.$body.find(".kefiya-progress-log");

		this.dialog.onhide = () => {
			this.dialog = null;
		};

		this.render();
		this.dialog.show();
		return this.dialog;
	},

	/** Paint the retained entries into a freshly opened window. */
	render: function () {
		if (!this.$log) {
			return;
		}
		this.$log.empty();
		this.entries.forEach((entry) => {
			const $line = $("<div>").text(`${entry.stamp}  ${entry.message}`);
			if (entry.isError) {
				$line.css("color", "var(--text-danger, #c0392b)");
			}
			this.$log.append($line);
		});
		this.$bar.css("width", this.percent + "%");
		this.$log.scrollTop(this.$log[0].scrollHeight);
		const last = this.entries[this.entries.length - 1];
		this.$status.text(
			this.finished ? __("Finished.") : (last ? last.message : __("Starting..."))
		);
	},

	/** Start a new session log (call before kicking off a fresh run). */
	reset: function () {
		this.entries = [];
		this.percent = 0;
		this.finished = false;
	},

	push: function (message, isError) {
		// Skip consecutive duplicates: the backend reports the same step for
		// each account, which would otherwise bury the real events.
		const last = this.entries[this.entries.length - 1];
		if (last && last.message === message && !isError) {
			return false;
		}
		this.entries.push({
			stamp: frappe.datetime.now_time(),
			message: message,
			isError: !!isError,
		});
		return true;
	},

	/**
	 * Append one line and move the bar.
	 *
	 * @param {string} message  text to append
	 * @param {number} progress 0-100
	 * @param {string} [title]  window title, e.g. the login being fetched
	 */
	update: function (message, progress, title) {
		this.percent = Math.max(0, Math.min(100, parseInt(progress, 10) || 0));
		const appended = message ? this.push(message, false) : false;
		if (this.percent >= 100) {
			this.finished = true;
		}

		this.ensure(title);
		if (title && this.dialog.set_title) {
			this.dialog.set_title(title);
		}

		this.$bar.css("width", this.percent + "%");
		if (appended) {
			const entry = this.entries[this.entries.length - 1];
			this.$log.append($("<div>").text(`${entry.stamp}  ${entry.message}`));
			this.$log.scrollTop(this.$log[0].scrollHeight);
		}
		if (message) {
			this.$status.text(message);
		}
		if (this.finished) {
			this.$status.text(__("Finished."));
		}
	},

	/** Record a failure without closing the window -- the log is the point. */
	error: function (message, title) {
		this.push(message, true);
		this.ensure(title);
		const entry = this.entries[this.entries.length - 1];
		this.$log.append(
			$("<div>")
				.css("color", "var(--text-danger, #c0392b)")
				.text(`${entry.stamp}  ${entry.message}`)
		);
		this.$log.scrollTop(this.$log[0].scrollHeight);
		this.$status.text(message);
	},

	/**
	 * Hide the window without discarding the log -- used while a TAN prompt
	 * takes over the screen. The next update() restores it, entries intact.
	 */
	hide: function () {
		if (this.dialog) {
			this.dialog.hide();
		}
	},
};
