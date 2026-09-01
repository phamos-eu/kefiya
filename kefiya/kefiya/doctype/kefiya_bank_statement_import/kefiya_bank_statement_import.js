// Copyright (c) 2024, jHetzer and contributors
// For license information, please see license.txt

frappe.provide("kefiya");

kefiya.show_import_plan = function (plan) {
	if (!plan) return;

	if (!plan.profile) {
		frappe.msgprint({
			title: __("Format not recognised"),
			message: plan.reason || __(
				"No known column layout was recognised in this file."),
			indicator: "red",
		});
		return;
	}

	const esc = frappe.utils.escape_html;
	let rows = (plan.sample || []).map((s) =>
		`<tr><td>${esc(s.date)}</td><td>${esc(s.bank_account || "")}</td>`
		+ `<td>${esc(s.description)}</td>`
		+ `<td class="text-right">${s.in ? format_currency(s.in) : ""}</td>`
		+ `<td class="text-right">${s.out ? format_currency(s.out) : ""}</td></tr>`
	).join("");

	// One file, many accounts: the per-account split is the number that says
	// whether the run understood the file, not the grand total.
	const perAccount = Object.keys(plan.accounts || {}).sort().map((a) =>
		`<tr><td>${esc(a)}</td>`
		+ `<td class="text-right">${plan.accounts[a].new}</td>`
		+ `<td class="text-right">${plan.accounts[a].duplicates}</td></tr>`
	).join("");

	const unmatched = Object.keys(plan.unmatched || {}).map((k) =>
		`${esc(k)} (${plan.unmatched[k]})`).join(", ");

	frappe.msgprint({
		title: __("Dry run — nothing was written"),
		indicator: (plan.would_create || plan.created) ? "blue" : "orange",
		message: `
			<p>${__("Recognised format")}: <b>${esc(plan.label)}</b></p>
			<ul>
				<li>${__("Rows read")}: <b>${plan.total}</b></li>
				<li>${__("New")}: <b>${plan.would_create || plan.created || 0}</b></li>
				<li>${__("Already present")}: <b>${plan.duplicates}</b></li>
				<li>${__("Unreadable")}: <b>${plan.unreadable || 0}</b></li>
			</ul>
			${unmatched ? `<p class="text-muted">${__("Unknown accounts")}: ${unmatched}</p>` : ""}
			<table class="table table-bordered">
				<thead><tr><th>${__("Bank Account")}</th>
				<th class="text-right">${__("New")}</th>
				<th class="text-right">${__("Already present")}</th></tr></thead>
				<tbody>${perAccount}</tbody>
			</table>
			<p class="text-muted">${__(
				"Check the direction of the first bookings: money in belongs"
				+ " in the left column, money out in the right.")}</p>
			<table class="table table-bordered">
				<thead><tr><th>${__("Date")}</th><th>${__("Bank Account")}</th>
				<th>${__("Description")}</th>
				<th class="text-right">${__("In")}</th>
				<th class="text-right">${__("Out")}</th></tr></thead>
				<tbody>${rows}</tbody>
			</table>`,
	});
};

frappe.ui.form.on("Kefiya Bank Statement Import", {
	setup(frm){
		frappe.realtime.on('update_import_status', function(data) {
			frm.reload_doc();
		});
	},
	refresh(frm) {
		$('.menu-btn-group').remove()
		frm.page.hide_icon_group();


		if (frm.doc.import_file &&
			frm.doc.status !== 'Success' &&
            frm.doc.status !== 'Partial Success' &&
            frm.doc.status !== 'Error'
		) {
			frm.disable_save();

			// Look before you write. A statement import creates bookings in
			// bulk, and the one thing that goes wrong silently is the sign:
			// on a card statement a positive amount is money leaving, on a
			// giro account it is money arriving. The dry run shows how the
			// first rows were understood before a single booking exists.
			frm.add_custom_button(__("Dry run"), () =>
				frm.call("plan_import", {
					file_url: frm.doc.import_file,
					bank_account: frm.doc.bank_account,
				}).then((r) => kefiya.show_import_plan(r.message))
			);

			frm.page.set_primary_action("Start Import", () =>  {
					frm.call("start_import", {
						file_url: frm.doc.import_file,
						bank_account: frm.doc.bank_account,
					}).then((r) => {
						kefiya.show_import_plan(r.message);
						frm.reload_doc();
					});
				});
		}


		if (frm.doc.status.includes("Success")) {
			frm.disable_save();
			frm.add_custom_button(__("Go to Bank Transaction List"), () =>
				frappe.set_route("List", "Bank Transaction")
			);
		}

		if (frm.doc.status.includes("Error")) {
			frm.disable_save();
			frm.page.set_primary_action("Retry", () =>  {
					frm.call("start_import", {
						file_url: frm.doc.import_file,
						bank_account: frm.doc.bank_account,
					}).then((r) => {
						kefiya.show_import_plan(r.message);
						frm.reload_doc();
					});
				});
		}

		if(!frm.is_new()){
			frm.trigger('set_headline')
		}
	},

	set_headline(frm){
		let message='';
		let indicator='';
	
		if (frm.doc.status === 'Success') {
			message = `Successfully imported ${ frm.doc.imported_records} records.`;
			indicator = 'blue';
		} 
		else if (frm.doc.status === 'Partial Success') {
			message = `Successfully imported ${ frm.doc.imported_records} records out of ${frm.doc.payload_count}. Fix the error for unimported rows.`;
			indicator = 'orange';
		}
		
		frm.dashboard.set_headline(message, indicator);
	}
});
