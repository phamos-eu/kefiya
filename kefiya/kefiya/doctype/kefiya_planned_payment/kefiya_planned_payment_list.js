// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// The forecast as a list: what is expected, what was replaced by a later
// stage of the same payment, what someone took out by hand.

frappe.listview_settings["Kefiya Planned Payment"] = {
	add_fields: ["status", "cancellation_reason", "direction", "from_bank"],

	get_indicator: function (doc) {
		if (doc.status === "Cancelled"
				&& doc.cancellation_reason === "Superseded by higher stage") {
			return [__("Replaced"), "gray",
				"cancellation_reason,=,Superseded by higher stage"];
		}
		var colour = {
			"Open": "blue", "Partially Matched": "orange", "Matched": "green",
			"Booked": "green", "Expired": "red", "Cancelled": "gray",
		}[doc.status] || "gray";
		return [__(doc.status), colour, "status,=," + doc.status];
	},

	onload: function (listview) {
		var selected = function () {
			return listview.get_checked_items(true);
		};
		var refresh = function () { listview.refresh(); };
		listview.page.add_inner_button(__("Plan a payment"), function () {
			kefiya.forecast.create({}, refresh);
		});
		listview.page.add_actions_menu_item(__("Remove from the forecast"),
			function () { kefiya.forecast.cancel(selected(), refresh); });
		listview.page.add_actions_menu_item(__("Back into the forecast"),
			function () { kefiya.forecast.reopen(selected(), refresh); });
	},
};
