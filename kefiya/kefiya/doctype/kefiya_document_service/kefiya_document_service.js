// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

frappe.provide("kefiya");

// A call that fails on the server resolves here with no message at all, and
// every handler below started with "if (!result) return". The result was a
// button that did nothing visible -- the worst possible answer, because it
// looks like the button is broken rather than the service. Whatever went
// wrong, something is said.
kefiya.saySomethingWentWrong = function (title, r) {
	const server = (r && r._server_messages) || "";
	let detail = "";
	try {
		const parsed = JSON.parse(server);
		detail = parsed.map((m) => {
			try { return JSON.parse(m).message; } catch (e) { return m; }
		}).join(" ");
	} catch (e) {
		detail = String(server || "");
	}
	frappe.msgprint({
		title: title,
		indicator: "red",
		message: frappe.utils.escape_html(detail)
			|| __("The call returned nothing. See the Error Log for details."),
	});
};

frappe.ui.form.on("Kefiya Document Service", {
	refresh(frm) {
		frm.add_custom_button(__("Test connection"), () =>
			frappe.call({
				method: "kefiya.utils.document_service.test_connection",
				freeze: true,
				freeze_message: __("Asking the service …"),
			}).then((r) => {
				if (!r || !r.message) {
					kefiya.saySomethingWentWrong(__("Connection failed"), r);
					return;
				}
				frappe.msgprint({
					title: __("Connection"),
					message: __("The service answered."),
					indicator: "green",
				});
			})
		);

		// The published client for this service documents the REQUEST shape
		// only, so the field names a document carries cannot be known here in
		// advance. This asks for one and reports which NAMES came back --
		// never the values, so no account data has to leave the site to get
		// the mapping right.
		frm.add_custom_button(__("Check field names"), () =>
			frappe.call({
				method: "kefiya.utils.document_service.probe",
				freeze: true,
			}).then((r) => {
				if (!r || !r.message) {
					kefiya.saySomethingWentWrong(__("Field names"), r);
					return;
				}
				kefiya.show_probe(r.message);
			})
		);

		frm.add_custom_button(__("Dry run"), () =>
			frappe.call({
				method: "kefiya.utils.document_service.fetch_statements",
				args: { dry_run: 1 },
				freeze: true,
				freeze_message: __("Looking, writing nothing …"),
			}).then((r) => {
				if (!r || !r.message) {
					kefiya.saySomethingWentWrong(__("Dry run failed"), r);
					return;
				}
				kefiya.show_document_run(r.message);
			})
		);

		frm.add_custom_button(__("Fetch now"), () =>
			frappe.confirm(
				__("Fetch the statements and file them against the accounts?"),
				() => frappe.call({
					method: "kefiya.utils.document_service.fetch_statements",
					args: { dry_run: 0 },
					freeze: true,
					freeze_message: __("Fetching …"),
				}).then((r) => {
					if (!r || !r.message) {
						kefiya.saySomethingWentWrong(__("Fetch failed"), r);
					} else {
						kefiya.show_document_run(r.message);
					}
					frm.reload_doc();
				})
			)
		);
	},
});

frappe.provide("kefiya");

kefiya.show_probe = function (result) {
	if (!result) return;
	const esc = frappe.utils.escape_html;

	if (!result.ok) {
		frappe.msgprint({
			title: __("Response not understood"),
			indicator: "red",
			message: `<p>${esc(result.reason || "")}</p>`
				+ `<p>${__("Top level keys")}: <code>`
				+ esc((result.top_level_keys || []).join(", ")) + "</code></p>",
		});
		return;
	}

	const keys = (result.document_keys || []).map((entry) =>
		"<li><code>"
		+ esc(Object.keys(entry).map((k) => `${k}: ${entry[k]}`).join(", "))
		+ "</code></li>").join("");
	const rec = (result.recognised || [])[0] || {};
	const tick = (v) => (v ? "✓" : "✗");

	frappe.msgprint({
		title: __("Field names returned by the service"),
		indicator: rec.id && rec.name ? "blue" : "orange",
		message: `
			<p>${__("Documents found")}: <b>${result.count}</b></p>
			<p>${__("Recognised")} — ${__("Identifier")} ${tick(rec.id)},
			   ${__("Name")} ${tick(rec.name)}, ${__("Date")} ${tick(rec.date)}</p>
			<p class="text-muted">${__(
				"Only field names are shown, never their values.")}</p>
			<ul>${keys}</ul>`,
	});
};

kefiya.show_document_run = function (summary) {
	if (!summary) return;
	const esc = frappe.utils.escape_html;

	if (summary.reason && !(summary.accounts || []).length) {
		frappe.msgprint({
			title: __("Nothing to do"),
			message: esc(summary.reason),
			indicator: "orange",
		});
		return;
	}

	const rows = (summary.accounts || []).map((a) =>
		`<tr><td>${esc(a.bank_account)}</td>`
		+ `<td class="text-right">${a.found}</td>`
		+ `<td class="text-right">${a.stored}</td>`
		+ `<td class="text-right">${a.already_present}</td>`
		+ `<td class="text-right">${a.failed}</td>`
		+ `<td>${esc(a.reason || "")}</td></tr>`).join("");

	frappe.msgprint({
		title: summary.dry_run
			? __("Dry run — nothing was filed")
			: __("Statements fetched"),
		indicator: summary.failed ? "orange" : "blue",
		message: `
			<table class="table table-bordered">
				<thead><tr><th>${__("Bank Account")}</th>
				<th class="text-right">${__("Found")}</th>
				<th class="text-right">${summary.dry_run
					? __("Would file") : __("Filed")}</th>
				<th class="text-right">${__("Already present")}</th>
				<th class="text-right">${__("Failures")}</th>
				<th>${__("Remark")}</th></tr></thead>
				<tbody>${rows}</tbody>
			</table>`,
	});
};
