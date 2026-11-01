// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// The way out of "the account does not cover this".
//
//   kefiya.funds_way_out(answer, { resend: fn, changed: fn })
//
// answer is what the send came back with (over_funds, names, kefiya_login,
// total, left, can_split, message). Two ways out are offered on the spot:
//
//   * a dated transfer -- the bank holds it until the day and refuses it
//     itself if the money is not there; resend() then sends it again.
//   * split the amount into parts the person chooses, each with its own day.
//     Only a draft with one payment can be split; the parts come back as
//     separate orders, and changed() reloads whatever shows them.

frappe.provide("kefiya");

kefiya.funds_way_out = function (answer, opts) {
	opts = opts || {};
	const m = answer || {};
	const esc = frappe.utils.escape_html;
	const total = Number(m.total || 0);
	const today = frappe.datetime.get_today();

	function money(v) { return format_currency(Number(v || 0)); }

	function nextBankingDay() {
		let d = frappe.datetime.str_to_obj(today);
		do { d.setDate(d.getDate() + 1); } while (d.getDay() === 0 || d.getDay() === 6);
		return frappe.datetime.obj_to_str(d);
	}
	const later = nextBankingDay();

	// The first part is what the account carries now -- when this reader may
	// know that. Otherwise the amounts are theirs to enter.
	const left = (m.left === null || m.left === undefined) ? null
		: Math.floor(Number(m.left) * 100) / 100;
	const parts = (left !== null && left > 0 && left < total)
		? [{ amount: left, date: today },
			{ amount: Math.round((total - left) * 100) / 100, date: later }]
		: [{ amount: "", date: today }, { amount: "", date: later }];

	const fields = [
		{ fieldtype: "HTML", fieldname: "why",
			options: "<div class='alert alert-warning' style='margin-bottom:12px'>"
				+ esc(m.message || "") + "</div>" },
		{ fieldtype: "Section Break", label: __("As a dated transfer") },
		{ fieldtype: "HTML", fieldname: "dated_note",
			options: "<p class='text-muted small'>"
				+ __("The bank keeps the order until that day and refuses it"
					+ " itself if the money is not there by then. It is not"
					+ " checked against the balance here.") + "</p>" },
		{ fieldtype: "Date", fieldname: "execution_date",
			label: __("Execution date"), default: later, reqd: 1 },
	];
	if (m.can_split) {
		fields.push(
			{ fieldtype: "Section Break", label: __("Split the amount") },
			{ fieldtype: "HTML", fieldname: "parts" });
	}

	const d = new frappe.ui.Dialog({
		title: __("The account does not cover this order"),
		size: m.can_split ? "large" : "small",
		fields: fields,
		primary_action_label: __("Send as a dated transfer"),
		primary_action: function (values) {
			if (!values.execution_date || values.execution_date <= today) {
				frappe.msgprint(__("A dated transfer needs a day after today."));
				return;
			}
			frappe.call({
				method: "kefiya.utils.funds_way_out.make_dated",
				args: { transfer_names: JSON.stringify(m.names || []),
					execution_date: values.execution_date },
				freeze: true,
			}).then(function (r) {
				const res = (r && r.message) || {};
				if (res.status !== "ok") {
					frappe.msgprint({ title: __("Not changed"), indicator: "red",
						message: esc(res.message || "") });
					return;
				}
				d.hide();
				if (opts.resend) opts.resend();
				else if (opts.changed) opts.changed();
			});
		},
	});

	if (m.can_split) {
		d.set_secondary_action_label(__("Split into parts"));
		d.set_secondary_action(split);
	}

	function partsHtml() {
		let h = "<table class='table table-bordered' style='margin-bottom:6px'>"
			+ "<thead><tr><th>" + __("Part no.") + "</th><th>" + __("Amount")
			+ "</th><th>" + __("Execution date") + "</th><th></th></tr></thead><tbody>";
		parts.forEach(function (p, i) {
			h += "<tr><td>" + (i + 1) + "</td>"
				+ "<td><input type='number' step='0.01' min='0.01' class='form-control'"
				+ " data-i='" + i + "' data-k='amount' value='" + esc(String(p.amount)) + "'></td>"
				+ "<td><input type='date' class='form-control' min='" + today + "'"
				+ " data-i='" + i + "' data-k='date' value='" + esc(p.date || "") + "'></td>"
				+ "<td>" + (parts.length > 2
					? "<button class='btn btn-xs btn-default' data-drop='" + i + "'>×</button>"
					: "") + "</td></tr>";
		});
		h += "</tbody></table>"
			+ "<button class='btn btn-xs btn-default' data-add='1'>"
			+ __("Add a part") + "</button>"
			+ " <span class='text-muted small' data-rest='1'></span>"
			+ "<p class='text-muted small' style='margin-top:8px'>"
			+ __("A part dated today goes out as an ordinary transfer, a later"
				+ " one as a dated transfer the bank keeps. Each part is its own"
				+ " order and is sent on its own.") + "</p>";
		return h;
	}

	function rest() {
		let sum = 0;
		parts.forEach(function (p) { sum += Number(p.amount || 0); });
		return Math.round((total - sum) * 100) / 100;
	}

	function showRest(el) {
		const r = rest();
		const span = el.querySelector("[data-rest]");
		if (!span) return;
		span.textContent = r === 0
			? __("The parts add up to {0}.", [money(total)])
			: __("Still to distribute: {0}", [money(r)]);
		span.style.color = r === 0 ? "" : "#A32D2D";
	}

	function renderParts() {
		const el = d.fields_dict.parts.$wrapper.get(0);
		el.innerHTML = partsHtml();
		el.querySelectorAll("input[data-i]").forEach(function (inp) {
			inp.oninput = function () {
				parts[Number(inp.getAttribute("data-i"))][inp.getAttribute("data-k")]
					= inp.value;
				showRest(el);
			};
		});
		el.querySelectorAll("[data-drop]").forEach(function (b) {
			b.onclick = function (e) {
				e.preventDefault();
				parts.splice(Number(b.getAttribute("data-drop")), 1);
				renderParts();
			};
		});
		el.querySelector("[data-add]").onclick = function (e) {
			e.preventDefault();
			const r = rest();
			parts.push({ amount: r > 0 ? r : "", date: later });
			renderParts();
		};
		showRest(el);
	}

	function split() {
		if (rest() !== 0) {
			frappe.msgprint(__("The parts must add up to {0}.", [money(total)]));
			return;
		}
		frappe.call({
			method: "kefiya.utils.funds_way_out.split_amounts",
			args: { transfer_name: (m.names || [])[0],
				parts: JSON.stringify(parts) },
			freeze: true,
		}).then(function (r) {
			const res = (r && r.message) || {};
			if (res.status !== "ok") {
				frappe.msgprint({ title: __("Not split"), indicator: "red",
					message: esc(res.message || "") });
				return;
			}
			d.hide();
			frappe.msgprint({ title: __("Order split"), indicator: "green",
				message: __("Split into {0} orders: {1}. Send each of them on"
					+ " its own.", [res.documents.length,
					res.documents.map(esc).join(", ")]) });
			if (opts.changed) opts.changed();
		});
	}

	d.show();
	if (m.can_split) renderParts();
	return d;
};
