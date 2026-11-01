// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// Correcting the forecast by hand: take a line out, bring it back, add one.
//
//   kefiya.forecast.cancel(names, done)    -- with a reason, never a delete
//   kefiya.forecast.reopen(names, done)
//   kefiya.forecast.create(defaults, done) -- a plan of one's own
//
// Bundled rather than kept with the doctype, because the form, the list and
// any other page that shows the forecast all want the
// same three dialogs. done() receives the server's answer.
//
// Why "remove" cancels instead of deleting: nearly every line is written by
// something that writes it again on its next run, under the same key. A
// deleted line comes back; a cancelled one stays out, and can be reopened.
// See kefiya.utils.forecast_actions.

frappe.provide("kefiya.forecast");

// The stored values are the forecast's cancellation reasons; the labels are
// our own. "Duplicate" and "Other" are words the framework translates for the
// whole site -- the Duplicate button among them -- so they are never shown
// here as such.
kefiya.forecast.REASONS = [
	{ value: "Duplicate", label: __("Counted twice") },
	{ value: "Settlement", label: __("Settled otherwise") },
	{ value: "Waiver", label: __("Waived") },
	{ value: "Uncollectible", label: __("Cannot be collected") },
	{ value: "Reversal", label: __("Reversed") },
	{ value: "Other", label: __("Another reason") },
];

kefiya.forecast._list = function (names) {
	return [].concat(names || []).filter(function (n) { return !!n; });
};

kefiya.forecast._report = function (answer, key) {
	var skipped = (answer && answer.skipped) || [];
	var done = (answer && answer[key]) || [];
	if (done.length) {
		frappe.show_alert({
			message: key === "cancelled"
				? __("{0} removed from the forecast", [done.length])
				: __("{0} back in the forecast", [done.length]),
			indicator: "green",
		});
	}
	if (skipped.length) {
		frappe.msgprint({
			title: __("Not changed"),
			indicator: "orange",
			message: skipped.map(function (s) {
				return frappe.utils.escape_html(s.name) + ": "
					+ frappe.utils.escape_html(s.why);
			}).join("<br>"),
		});
	}
};

kefiya.forecast.cancel = function (names, done) {
	names = kefiya.forecast._list(names);
	if (!names.length) {
		frappe.msgprint(__("Select the lines to remove first."));
		return;
	}
	var d = new frappe.ui.Dialog({
		title: names.length === 1
			? __("Remove from the forecast")
			: __("Remove {0} lines from the forecast", [names.length]),
		fields: [
			{
				fieldname: "reason", fieldtype: "Select",
				label: __("Reason for removing"), reqd: 1, default: "Duplicate",
				options: kefiya.forecast.REASONS,
			},
			{
				fieldname: "note", fieldtype: "Small Text",
				label: __("Remark for the record"),
			},
			{
				fieldname: "hint", fieldtype: "HTML",
				options: "<p class='text-muted small'>" + __(
					"The line is cancelled, not deleted: whatever wrote it does "
					+ "not write it again, and it can be brought back.") + "</p>",
			},
		],
		primary_action_label: __("Take it out"),
		primary_action: function (values) {
			frappe.call({
				method: "kefiya.utils.forecast_actions.cancel_lines",
				args: { names: names, reason: values.reason, note: values.note },
				freeze: true,
			}).then(function (r) {
				d.hide();
				kefiya.forecast._report(r.message, "cancelled");
				if (done) done(r.message);
			});
		},
	});
	d.show();
};

kefiya.forecast.reopen = function (names, done) {
	names = kefiya.forecast._list(names);
	if (!names.length) {
		frappe.msgprint(__("Select the lines to bring back first."));
		return;
	}
	frappe.confirm(
		names.length === 1
			? __("Bring this line back into the forecast?")
			: __("Bring {0} lines back into the forecast?", [names.length]),
		function () {
			frappe.call({
				method: "kefiya.utils.forecast_actions.reopen_lines",
				args: { names: names },
				freeze: true,
			}).then(function (r) {
				kefiya.forecast._report(r.message, "reopened");
				if (done) done(r.message);
			});
		}
	);
};

kefiya.forecast.create = function (defaults, done) {
	defaults = defaults || {};
	var d = new frappe.ui.Dialog({
		title: __("Plan a payment"),
		fields: [
			{
				fieldname: "bank_account", fieldtype: "Link", options: "Bank Account",
				label: __("Bank Account"), reqd: 1,
				default: defaults.bank_account,
				get_query: function () {
					return { filters: { is_company_account: 1, disabled: 0 } };
				},
			},
			{
				fieldname: "direction", fieldtype: "Select",
				label: __("Incoming or outgoing"),
				reqd: 1, default: defaults.direction || "Outgoing",
				options: [
					{ value: "Outgoing", label: __("Outgoing") },
					{ value: "Incoming", label: __("Incoming") },
				],
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "due_date", fieldtype: "Date", label: __("Due Date"),
				reqd: 1, default: defaults.due_date || frappe.datetime.get_today(),
			},
			{
				fieldname: "amount", fieldtype: "Currency", label: __("Amount"),
				reqd: 1, default: defaults.amount,
			},
			{ fieldtype: "Section Break" },
			{
				fieldname: "counterparty_name", fieldtype: "Data",
				label: __("Counterparty"), default: defaults.counterparty_name,
			},
			{
				fieldname: "counterparty_iban", fieldtype: "Data",
				label: __("Counterparty IBAN"), default: defaults.counterparty_iban,
				description: __("Lets the forecast recognise the payment when "
					+ "the bank reports it, and replace this plan."),
			},
			{
				fieldname: "purpose", fieldtype: "Small Text", label: __("Purpose"),
				default: defaults.purpose,
			},
		],
		primary_action_label: __("Add to the forecast"),
		primary_action: function (values) {
			frappe.call({
				method: "kefiya.utils.forecast_actions.create_line",
				args: values,
				freeze: true,
			}).then(function (r) {
				d.hide();
				frappe.show_alert({
					message: __("Added to the forecast"), indicator: "green",
				});
				if (done) done(r.message);
			});
		},
	});
	d.show();
};
