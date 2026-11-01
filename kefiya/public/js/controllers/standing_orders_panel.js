// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// The standing orders, below the outgoing payments.
//
//   kefiya.standing_orders_panel(el)
//
// A standing order is a transfer on a cycle, kept in one of two places: here
// (each execution lands in the outgoing payments above, approved, waiting for
// its TAN) or at the bank (the bank executes it; the fetch reads the bank's
// list and keeps it on file). Both in one list is what keeps one payment from
// being set up twice -- once here and once at the bank.
//
// Entering and changing an order happens in its form: activating one kept
// here approves every transfer it will place, under the four-eyes rule, and
// that check lives with the document, not with a list.

frappe.provide("kefiya");

kefiya.standing_orders_panel = function (el) {
	if (!el) return null;
	const esc = frappe.utils.escape_html;
	const view = { scope: "current", data: null };

	function money(v) { return format_currency(Number(v || 0)); }
	function dmy(d) { return d ? frappe.datetime.str_to_user(d) : ""; }

	const SCOPES = [
		["current", __("Active and paused")],
		["proposals", __("Proposals")],
		["ended", __("Finished")],
		["all", __("All")],
	];

	const CYCLE = {
		"Monthly": __("monthly on the {0}."),
		"Quarterly": __("quarterly on the {0}."),
		"Half-Yearly": __("half-yearly on the {0}."),
		"Yearly": __("yearly on the {0}."),
	};

	const STATE = {
		"Active": [__("Running"), "#3B6D11", "#EAF3DE"],
		"Paused": [__("On pause"), "#854F0B", "#FDF3E3"],
		"Proposed": [__("Proposal"), "#185FA5", "#E6F1FB"],
		"Ended": [__("Finished"), "#5F5E5A", "#F1EFE8"],
		"Rejected": [__("Turned down"), "#5F5E5A", "#F1EFE8"],
	};

	function chip(text, fg, bg) {
		return "<span style='display:inline-block;padding:1px 8px;"
			+ "border-radius:10px;font-size:11px;color:" + fg
			+ ";background:" + bg + "'>" + esc(text) + "</span>";
	}

	function where(r) {
		return r.execution_mode === "Bank"
			? __("at the bank") : __("here, sent from the outgoing payments");
	}

	function bankCell(r) {
		if (r.bank_check === "reported") {
			return chip(__("listed by the bank on {0}", [dmy(r.bank_seen_on)]),
				"#3B6D11", "#EAF3DE");
		}
		if (r.bank_check === "stale") {
			return chip(__("last listed on {0}", [dmy(r.bank_seen_on)]),
				"#854F0B", "#FDF3E3");
		}
		if (r.bank_check === "never") {
			return chip(__("not listed by the bank yet"), "#854F0B", "#FDF3E3");
		}
		return "";
	}

	function load() {
		el.innerHTML = "<div class='zk-load'>" + __("Loading …") + "</div>";
		return frappe.call({
			method: "kefiya.utils.standing_order_overview.overview",
			args: { scope: view.scope },
		}).then(function (r) {
			view.data = (r && r.message) || { rows: [], counts: {}, can: {} };
			render();
		}).catch(function () {
			el.innerHTML = "<div class='zk-err'>"
				+ __("The standing orders could not be loaded.") + "</div>";
		});
	}

	function render() {
		const d = view.data;
		let h = "<h4 style='margin:28px 0 8px'>" + __("Standing orders")
			+ " <span class='zk-sub' style='font-weight:normal'>"
			+ __("active: {0} a month", [money(d.monthly)]) + "</span></h4>";

		h += "<div class='zk-bar'>";
		if (d.can && d.can.create) {
			h += "<button class='zk-primary' data-so='new'>"
				+ __("New standing order") + "</button>";
		}
		SCOPES.forEach(function (s) {
			const n = (d.counts || {})[s[0]] || 0;
			h += "<button data-scope='" + s[0] + "'"
				+ (view.scope === s[0] ? " class='zk-primary'" : "") + ">"
				+ esc(s[1]) + " (" + n + ")</button>";
		});
		h += "<button data-so='reload'>" + __("Refresh") + "</button></div>";

		if (!d.rows.length) {
			el.innerHTML = h + "<div class='zk-empty'>"
				+ __("No standing orders here.") + "</div>";
			bind();
			return;
		}

		h += "<table class='zk-tb'><thead><tr>"
			+ "<th>" + __("Order state") + "</th>"
			+ "<th>" + __("Kept") + "</th>"
			+ "<th>" + __("Paying account") + "</th>"
			+ "<th>" + __("Recipient / Reference") + "</th>"
			+ "<th>" + __("Cycle") + "</th>"
			+ "<th>" + __("Next execution") + "</th>"
			+ "<th class='r'>" + __("Amount") + "</th>"
			+ "<th>" + __("What the bank says") + "</th>"
			+ "</tr></thead><tbody>";
		d.rows.forEach(function (r) {
			const st = STATE[r.status] || [r.status, "#5F5E5A", "#F1EFE8"];
			const cycle = (CYCLE[r.frequency] || "{0}")
				.replace("{0}", String(r.day_of_month || ""));
			h += "<tr class='zk-row' data-so-n='" + esc(r.name) + "' tabindex='0'>"
				+ "<td>" + chip(st[0], st[1], st[2]) + "</td>"
				+ "<td>" + esc(where(r)) + "</td>"
				+ "<td>" + esc(r.bank_account || "") + "</td>"
				+ "<td>" + esc(r.counterparty_name || "")
				+ "<div class='zk-sub'>" + esc(r.purpose || "") + "</div></td>"
				+ "<td>" + esc(cycle) + (r.valid_until
					? "<div class='zk-sub'>" + __("until {0}", [dmy(r.valid_until)])
						+ "</div>" : "") + "</td>"
				+ "<td>" + dmy(r.next_execution_date) + "</td>"
				+ "<td class='r'>" + money(r.amount) + "</td>"
				+ "<td>" + bankCell(r) + "</td></tr>";
		});
		el.innerHTML = h + "</tbody></table>";
		bind();
	}

	function bind() {
		el.querySelectorAll("[data-scope]").forEach(function (b) {
			b.onclick = function () {
				view.scope = b.getAttribute("data-scope");
				load();
			};
		});
		el.querySelectorAll("[data-so]").forEach(function (b) {
			b.onclick = function () {
				if (b.getAttribute("data-so") === "new") {
					frappe.new_doc("Kefiya Standing Order");
				} else {
					load();
				}
			};
		});
		el.querySelectorAll("tr[data-so-n]").forEach(function (tr) {
			const open = function () {
				frappe.set_route("Form", "Kefiya Standing Order",
					tr.getAttribute("data-so-n"));
			};
			tr.onclick = open;
			tr.onkeydown = function (e) {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					open();
				}
			};
		});
	}

	load();
	return { reload: load };
};
