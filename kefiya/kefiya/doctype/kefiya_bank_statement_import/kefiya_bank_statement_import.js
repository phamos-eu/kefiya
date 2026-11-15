// Copyright (c) 2024, jHetzer and contributors
// For license information, please see license.txt

const FINAL_STATUSES = ["Success", "Partial Success", "Error"];
const RUNNING_STATUSES = ["Queued", "In Progress"];

frappe.ui.form.on("Kefiya Bank Statement Import", {
	setup(frm) {
		frappe.realtime.on("update_import_status", (data) => {
			// other imports report on the same event
			if (data.docname === frm.doc.name) frm.reload_doc();
		});
	},

	refresh(frm) {
		$(".menu-btn-group").remove();
		frm.page.hide_icon_group();
		frm.disable_save();
		const status = frm.doc.status || "Not Started";

		// The column mapping wizard is in the Kefiya Hub; here a file runs with the
		// mapping it was started with, or with the built-in one for DKB and PayPal.
		if (frm.doc.import_file && !FINAL_STATUSES.includes(status) && !RUNNING_STATUSES.includes(status)) {
			frm.page.set_primary_action(__("Start Import"), () => frm.trigger("start_import"));
		}
		if (status === "Error") {
			frm.page.set_primary_action(__("Retry"), () => frm.trigger("start_import"));
		}

		if (frm.doc.imported_records) {
			const journal_entries = frm.doc.import_as === "Journal Entry";
			frm.add_custom_button(
				journal_entries ? __("Go to Journal Entry List") : __("Go to Bank Transaction List"),
				() =>
					journal_entries
						? frappe.set_route("List", "Journal Entry", { kefiya_bank_account: frm.doc.bank_account })
						: frappe.set_route("List", "Bank Transaction", { bank_account: frm.doc.bank_account })
			);
		}

		if (!frm.is_new()) {
			frm.trigger("set_headline");
			frm.trigger("render_log");
		}
	},

	start_import(frm) {
		const mapping = frm.doc.mapping ? JSON.parse(frm.doc.mapping) : null;
		frm.call("start_import", {
			file_url: frm.doc.import_file,
			bank_account: frm.doc.bank_account,
			company: frm.doc.company,
			mapping,
			template: frm.doc.template,
			import_as: frm.doc.import_as,
			counter_account: frm.doc.counter_account,
			fee_account: frm.doc.fee_account,
			row_accounts: frm.doc.row_accounts,
		}).then(() => frm.reload_doc());
	},

	set_headline(frm) {
		const doc = frm.doc;
		const messages = {
			Queued: [__("The import is queued."), "blue"],
			"In Progress": [__("The import is running."), "blue"],
			Success: [__("{0} records imported, {1} skipped.", [doc.imported_records, doc.skipped_records || 0]), "green"],
			"Partial Success": [
				__("{0} records imported, {1} skipped, {2} failed. See the import log.", [
					doc.imported_records,
					doc.skipped_records || 0,
					doc.failed_records || 0,
				]),
				"orange",
			],
			Error: [__("Nothing was imported. See the import log."), "red"],
		};
		const [message, indicator] = messages[doc.status] || [];
		if (message) frm.dashboard.set_headline(message, indicator);
	},

	render_log(frm) {
		const $wrapper = frm.get_field("import_log_html").$wrapper;
		const log = JSON.parse(frm.doc.import_log || "[]");
		if (!log.length) return $wrapper.empty();
		const esc = frappe.utils.escape_html;
		const link = (r) =>
			r.name ? `<a href="/app/${frappe.router.slug(r.doctype)}/${encodeURIComponent(r.name)}">${esc(r.name)}</a>` : "";
		$wrapper.html(`<table class="table table-sm table-bordered">
			<thead><tr><th>${__("Line")}</th><th>${__("Status")}</th><th>${__("Date")}</th><th>${__("Amount")}</th>
				<th>${__("Document")}</th><th>${__("Message")}</th></tr></thead>
			<tbody>${log
				.map(
					(r) => `<tr>
						<td>${r.line || ""}</td><td>${__(r.status)}</td>
						<td>${r.date ? frappe.datetime.str_to_user(r.date) : ""}</td>
						<td class="text-right">${r.amount !== null && r.amount !== undefined ? format_number(r.amount) : ""}</td>
						<td>${link(r)}</td><td>${esc(r.message || "")}</td>
					</tr>`
				)
				.join("")}</tbody></table>`);
	},
});
