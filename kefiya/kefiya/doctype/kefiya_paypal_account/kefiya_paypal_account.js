// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

frappe.ui.form.on("Kefiya PayPal Account", {
	refresh: function (frm) {
		if (frm.is_new() || !frm.doc.client_id) return;
		frm.add_custom_button(__("Fetch now"), function () {
			frappe.call({
				method: "kefiya.utils.paypal.fetch_now",
				args: { name: frm.doc.name },
				freeze: true,
				freeze_message: __("Fetching from PayPal …"),
			}).then(function (r) {
				const m = (r && r.message) || {};
				frappe.show_alert({ message: m.summary || "", indicator: "green" }, 8);
				frm.reload_doc();
			});
		});
	},
});
