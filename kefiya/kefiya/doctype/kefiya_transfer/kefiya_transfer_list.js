// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

/**
 * Outbox view for outgoing transfers.
 *
 * Orders are entered one at a time and wait here until someone sends them.
 * Selecting several and sending them together produces a single collective
 * order (HKCCM) that the bank authorises with one TAN -- otherwise every
 * order would cost its own TAN, which is the whole reason for collecting
 * them.
 */
frappe.listview_settings["Kefiya Transfer"] = {
	add_fields: ["status", "on_hold", "total_amount", "kefiya_login", "docstatus"],

	get_indicator: function (doc) {
		if (doc.status === "Sent") {
			return [__("Sent"), "green", "status,=,Sent"];
		}
		if (doc.on_hold) {
			return [__("Held back"), "gray", "on_hold,=,1"];
		}
		if (doc.docstatus === 1) {
			return [__("Ready to send"), "blue", "status,=,Approved"];
		}
		if (doc.docstatus === 2) {
			return [__("Cancelled"), "red", "docstatus,=,2"];
		}
		return [__("Draft"), "orange", "docstatus,=,0"];
	},

	onload: function (listview) {
		listview.page.add_inner_button(__("Ready to send"), function () {
			listview.filter_area.clear().then(() => {
				listview.filter_area.add([
					["Kefiya Transfer", "docstatus", "=", 1],
					["Kefiya Transfer", "status", "=", "Approved"],
				]);
			});
		});

		listview.page.add_actions_menu_item(__("Send selected"), function () {
			kefiya_outbox_send(listview);
		});

		listview.page.add_actions_menu_item(__("Hold back"), function () {
			kefiya_outbox_set_hold(listview, 1);
		});

		listview.page.add_actions_menu_item(__("Release"), function () {
			kefiya_outbox_set_hold(listview, 0);
		});
	},
};

function kefiya_outbox_selection(listview) {
	const docs = listview.get_checked_items();
	if (!docs.length) {
		frappe.msgprint(__("Select at least one transfer."));
		return null;
	}
	return docs;
}

function kefiya_outbox_set_hold(listview, on_hold) {
	const docs = kefiya_outbox_selection(listview);
	if (!docs) {
		return;
	}
	const eligible = docs.filter((d) => d.docstatus === 1 && d.status !== "Sent");
	if (!eligible.length) {
		frappe.msgprint(__("Only approved, unsent transfers can be held or released."));
		return;
	}

	frappe.call({
		method: "kefiya.utils.client.set_transfer_hold",
		args: {
			transfer_names: eligible.map((d) => d.name),
			on_hold: on_hold,
		},
		freeze: true,
		callback: function () {
			frappe.show_alert({
				message: on_hold
					? __("{0} transfer(s) held back.", [eligible.length])
					: __("{0} transfer(s) released.", [eligible.length]),
				indicator: "blue",
			});
			listview.refresh();
		},
	});
}

function kefiya_outbox_send(listview) {
	const docs = kefiya_outbox_selection(listview);
	if (!docs) {
		return;
	}

	const held = docs.filter((d) => d.on_hold);
	if (held.length) {
		frappe.msgprint({
			title: __("Held-back transfers selected"),
			indicator: "orange",
			message: __("{0} of the selected transfers are held back. Release them first or deselect them — they are not sent silently.", [held.length]),
		});
		return;
	}

	const notReady = docs.filter((d) => d.docstatus !== 1 || d.status === "Sent");
	if (notReady.length) {
		frappe.msgprint({
			title: __("Not ready"),
			indicator: "red",
			message: __("{0} of the selected transfers are not approved, or were already sent.", [notReady.length]),
		});
		return;
	}

	const accounts = Array.from(new Set(docs.map((d) => d.kefiya_login)));
	if (accounts.length > 1) {
		frappe.msgprint({
			title: __("Different paying accounts"),
			indicator: "red",
			message: __("One collective order can only be paid from a single account. Selected: {0}", [
				frappe.utils.escape_html(accounts.join(", ")),
			]),
		});
		return;
	}

	let total = 0;
	docs.forEach((d) => {
		total += flt(d.total_amount);
	});

	const rows = docs.map((d) =>
		"<tr><td>" + frappe.utils.escape_html(d.name)
		+ "</td><td style='text-align:right'>" + format_currency(d.total_amount)
		+ "</td></tr>"
	).join("");

	const d = new frappe.ui.Dialog({
		title: docs.length > 1 ? __("Send collective order") : __("Send transfer"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options:
					"<div class='alert alert-warning'>"
					+ __("About to send {0} order(s) totalling {1} from {2}. This cannot be undone here.", [
						docs.length,
						"<b>" + format_currency(total) + "</b>",
						frappe.utils.escape_html(accounts[0] || ""),
					])
					+ "</div>"
					+ (docs.length > 1
						? "<p>" + __("They are combined into one collective order, so the bank asks for a single TAN.") + "</p>"
						: "")
					+ "<table class='table table-bordered'><thead><tr><th>"
					+ __("Order") + "</th><th style='text-align:right'>" + __("Amount")
					+ "</th></tr></thead><tbody>" + rows + "</tbody></table>",
			},
			{
				fieldtype: "Check",
				fieldname: "confirmed",
				label: __("I checked every order in this list"),
				reqd: 1,
			},
		],
		primary_action_label: __("Send now"),
		primary_action: function (values) {
			if (!values.confirmed) {
				frappe.msgprint(__("Please confirm you checked the orders."));
				return;
			}
			d.hide();
			frappe.call({
				method: "kefiya.utils.client.send_transfer_outbox",
				args: {
					transfer_names: docs.map((x) => x.name),
					user_scope: docs[0].name,
					confirmed: 1,
				},
				freeze: true,
				freeze_message: __("Sending to bank..."),
				callback: function (r) {
					const msg = r.message || {};
					if (msg.status === "submitted") {
						frappe.show_alert({
							message: __("Sent."),
							indicator: "green",
						});
					} else if (msg.status === "tan_required") {
						frappe.msgprint({
							title: __("TAN required"),
							indicator: "blue",
							message: __("The bank asked for a TAN. Open {0} to enter it.", [
								frappe.utils.escape_html(docs[0].name),
							]),
						});
					} else if (msg.status === "vop_mismatch") {
						frappe.msgprint({
							title: __("Verification of Payee — mismatch"),
							indicator: "orange",
							message: __("The bank could not confirm a payee name. No money was sent. Open {0} to review and release it.", [
								frappe.utils.escape_html(docs[0].name),
							]),
						});
					} else {
						frappe.msgprint({
							title: __("Send failed"),
							indicator: "red",
							message: msg.message || __("Unknown error"),
						});
					}
					listview.refresh();
				},
			});
		},
	});
	d.show();
}
