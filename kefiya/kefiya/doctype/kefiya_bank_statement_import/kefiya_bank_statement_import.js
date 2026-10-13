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

// What rebuilding an account from its statement would do -- and, after a
// confirmation, does. Shown per account and per period, because the number
// that matters is not the total but "how many bookings go, how many come,
// and does the bank agree afterwards".
kefiya.show_rebuild_plan = function (plan, onConfirm) {
	const esc = frappe.utils.escape_html;
	const money = (v) => format_currency(v);
	const konten = (plan.konten || []).map((k) => {
		const fenster = (k.fenster || []).map((f) =>
			`<tr><td>${esc(f.nach)} – ${esc(f.bis)}</td>`
			+ `<td class="text-right">${f.im_system}</td>`
			+ `<td class="text-right">${f.geschuetzt}</td>`
			+ `<td class="text-right">${f.zu_ersetzen}</td>`
			+ `<td class="text-right">${f.einzulesen}</td></tr>`).join("");
		const vorher = (k.vorher || []).map((a) =>
			`<li>${esc(a.nach)} – ${esc(a.bis)}: Bank ${money(a.bank)},`
			+ ` System ${money(a.system)}</li>`).join("");
		const uneinig = (k.zweitleser_uneinig || []).map((a) =>
			`<li>${esc(a.nach)} – ${esc(a.bis)}: ${money(a.fehlt)}</li>`).join("");
		return `<h5>${esc(k.bank_account)}</h5>
			${k.einlesbar ? "" : `<p class="text-danger">${__("The statement cannot rebuild this account.")}</p>`}
			${uneinig ? `<p class="text-danger">${__("The two readers disagree on these sheets:")}</p><ul>${uneinig}</ul>` : ""}
			<table class="table table-bordered">
				<thead><tr><th>${__("Period (after – until)")}</th>
				<th class="text-right">${__("In the system")}</th>
				<th class="text-right">${__("Kept (money attached)")}</th>
				<th class="text-right">${__("Replaced")}</th>
				<th class="text-right">${__("From the statement")}</th></tr></thead>
				<tbody>${fenster}</tbody>
			</table>
			${vorher ? `<p>${__("Deviating before the rebuild:")}</p><ul>${vorher}</ul>`
				: `<p>${__("The account already matches the bank.")}</p>`}`;
	}).join("");

	const d = new frappe.ui.Dialog({
		title: __("Rebuild from statement"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "plan", options: konten }],
		primary_action_label: plan.einlesbar ? __("Rebuild now") : null,
		primary_action: plan.einlesbar ? () => { d.hide(); onConfirm(); } : null,
	});
	d.show();
};

frappe.ui.form.on("Kefiya Bank Statement Import", {
	setup(frm){
		frappe.realtime.on('update_import_status', function(data) {
			frm.reload_doc();
		});
		frappe.realtime.on('kefiya_auszug_eingelesen', function(data) {
			frappe.show_alert({
				message: data.alles_stimmt
					? __("Rebuilt. The account now matches the bank.")
					: __("Rebuilt, but the account still deviates. See the report on the document."),
				indicator: data.alles_stimmt ? "green" : "orange",
			}, 15);
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

			// The other way round: not add what is missing, but replace what
			// the statement covers. Only for .sta files -- they carry the
			// bank's balances, and those are what the result is measured by.
			if (/\.sta$/i.test(frm.doc.import_file)) {
				frm.add_custom_button(__("Rebuild from statement"), () =>
					frm.call("plan_rebuild", {
						file_url: frm.doc.import_file,
						bank_account: frm.doc.bank_account,
					}).then((r) => kefiya.show_rebuild_plan(r.message, () =>
						frappe.confirm(
							__("Replace the bookings of the listed periods with the statement? Bookings with money attached are kept."),
							() => frm.call("start_rebuild", {
								file_url: frm.doc.import_file,
								bank_account: frm.doc.bank_account,
							}).then(() => frappe.show_alert({
								message: __("Rebuilding in the background. You will be told when it is done."),
								indicator: "blue",
							}, 10)))))
				);
			}

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
