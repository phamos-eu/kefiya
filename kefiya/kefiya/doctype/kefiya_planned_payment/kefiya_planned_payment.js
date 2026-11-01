// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// One line of the forecast. The dialogs live in the bundle
// (controllers/forecast_actions.js), because the list and other pages that
// show the forecast use them too.

frappe.ui.form.on("Kefiya Planned Payment", {
	refresh: function (frm) {
		if (frm.is_new()) {
			return;
		}
		var reload = function () { frm.reload_doc(); };
		var status = frm.doc.status;
		if (status === "Open" || status === "Partially Matched") {
			frm.add_custom_button(__("Remove from the forecast"), function () {
				kefiya.forecast.cancel(frm.doc.name, reload);
			});
		}
		if (status === "Cancelled" || status === "Expired") {
			frm.add_custom_button(__("Back into the forecast"), function () {
				kefiya.forecast.reopen(frm.doc.name, reload);
			});
		}
		// A replaced line says by what, so nobody has to search for the
		// line that now stands for the payment.
		if (status === "Cancelled" && frm.doc.superseded_by
				&& frm.doc.cancellation_reason === "Superseded by higher stage") {
			frm.set_intro(__("Replaced by a later stage of the same payment: {0}",
				[frappe.utils.get_form_link("Kefiya Planned Payment",
					frm.doc.superseded_by, true)]), "blue");
		}
	},
});
