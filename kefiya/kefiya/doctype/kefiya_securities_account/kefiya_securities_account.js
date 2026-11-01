// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("Kefiya Securities Account", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		// Straight to the bank's own page for this depot.
		if (frm.doc.online_banking_url) {
			frm.add_custom_button(__("Open at the Bank"), () => {
				window.open(frm.doc.online_banking_url, "_blank", "noopener");
			});
		}

		frm.add_custom_button(__("Holdings"), () => {
			frappe.set_route("List", "Kefiya Security Holding", {
				securities_account: frm.doc.name,
			});
		});

		if (frm.doc.kefiya_login) {
			frm.add_custom_button(__("Fetch Holdings Now"), () => {
				frappe.call({
					method: "kefiya.utils.client.import_depot_holdings",
					args: { securities_account: frm.doc.name },
					freeze: true,
					freeze_message: __("Asking the bank for the holdings ..."),
					callback(r) {
						const res = r.message || {};
						if (res.status === "Failed") {
							frappe.msgprint({
								title: __("Holdings not fetched"),
								indicator: "red",
								message: frappe.utils.escape_html(res.message || ""),
							});
						} else {
							frappe.show_alert({
								message: __("{0} new, {1} updated", [res.created || 0, res.updated || 0]),
								indicator: "green",
							});
						}
						frm.reload_doc();
					},
				});
			});
		} else {
			frm.add_custom_button(__("Enter Holding"), () => {
				frappe.new_doc("Kefiya Security Holding", {
					securities_account: frm.doc.name,
					company: frm.doc.company,
					valuation_date: frappe.datetime.get_today(),
				});
			});
		}
	},
});
