// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// The way out of "over the bank's transfer limit".
//
//   kefiya.limit_way_out(answer, { changed: fn })
//
// answer is what the send came back with (over_limit, names, can_split,
// message). The dialog asks the server for proposals and for what is still
// free per account and day, and shows them as a table the person edits:
// day, account and amount of every part. "Place parts" hands the table back;
// the server checks it again and creates one draft per part. An approved
// order is cancelled and entered again first -- only after a confirmation
// that says so. changed() reloads whatever shows the orders.

frappe.provide("kefiya");

kefiya.limit_way_out = function (answer, opts) {
	opts = opts || {};
	const m = answer || {};
	const esc = frappe.utils.escape_html;
	const name = (m.names || [])[0];
	if (!name) return null;

	function money(v) { return format_currency(Number(v || 0)); }
	function round2(v) { return Math.round(Number(v || 0) * 100) / 100; }

	let info = null;
	let parts = [];

	const d = new frappe.ui.Dialog({
		title: __("Over the bank's limit — split into parts"),
		size: "extra-large",
		fields: [
			{ fieldtype: "HTML", fieldname: "why",
				options: "<div class='alert alert-warning' style='margin-bottom:12px'>"
					+ esc(m.message || "") + "</div>" },
			{ fieldtype: "HTML", fieldname: "body",
				options: "<div class='text-muted'>" + __("Loading proposals …")
					+ "</div>" },
		],
		primary_action_label: __("Place parts"),
		primary_action: place,
	});

	function accountLabel(login) {
		const a = (info.accounts || []).find(function (x) {
			return x.kefiya_login === login;
		});
		return a ? (a.bank_account || a.kefiya_login) : login;
	}

	function usable() {
		return (info.accounts || []).filter(function (a) { return a.limit; });
	}

	// What is left on an account on a day once the table's own rows are
	// counted. Exact for a daily limit; for a longer window the server's
	// check when placing is what counts.
	function leftAfter(login, date) {
		const base = ((info.room || {})[login] || {})[date];
		if (base === undefined) return null;
		let used = 0;
		parts.forEach(function (p) {
			if (p.kefiya_login === login && p.date === date) {
				used += Number(p.amount || 0);
			}
		});
		return round2(base - used);
	}

	function rest() {
		let sum = 0;
		parts.forEach(function (p) { sum += Number(p.amount || 0); });
		return round2(info.total - sum);
	}

	function options(selected) {
		return usable().map(function (a) {
			return "<option value='" + esc(a.kefiya_login) + "'"
				+ (a.kefiya_login === selected ? " selected" : "") + ">"
				+ esc(a.bank_account || a.kefiya_login)
				+ (a.own ? " " + esc(__("(this order)")) : "")
				+ "</option>";
		}).join("");
	}

	function bodyHtml() {
		let h = "";
		if ((info.proposals || []).length) {
			h += "<div style='margin-bottom:10px'><span class='text-muted'>"
				+ __("Proposals:") + "</span> "
				+ info.proposals.map(function (p, i) {
					return "<button class='btn btn-xs btn-default'"
						+ " data-proposal='" + i + "'>" + esc(p.label)
						+ "</button>";
				}).join(" ") + "</div>";
		} else {
			h += "<div class='text-muted' style='margin-bottom:10px'>"
				+ __("No proposal fits within the next banking days. Enter the"
					+ " parts yourself.") + "</div>";
		}
		h += "<table class='table table-bordered' style='margin-bottom:6px'>"
			+ "<thead><tr><th>" + __("Part no.") + "</th><th>"
			+ __("Day") + "</th><th>" + __("Account") + "</th><th>"
			+ __("Amount") + "</th><th>" + __("Left that day") + "</th>"
			+ "<th></th></tr></thead><tbody>";
		parts.forEach(function (p, i) {
			const left = leftAfter(p.kefiya_login, p.date);
			h += "<tr><td>" + (i + 1) + "</td>"
				+ "<td><input type='date' class='form-control' min='"
				+ esc(info.today) + "' data-i='" + i + "' data-k='date'"
				+ " value='" + esc(p.date || "") + "'></td>"
				+ "<td><select class='form-control' data-i='" + i
				+ "' data-k='kefiya_login'>" + options(p.kefiya_login)
				+ "</select></td>"
				+ "<td><input type='number' step='0.01' min='0.01'"
				+ " class='form-control' data-i='" + i + "' data-k='amount'"
				+ " value='" + esc(String(p.amount)) + "'></td>"
				+ "<td data-left='" + i + "' style='text-align:right"
				+ (left !== null && left < 0 ? ";color:#A32D2D" : "") + "'>"
				+ (left === null ? "–" : esc(money(left))) + "</td>"
				+ "<td><button class='btn btn-xs btn-default' data-drop='" + i
				+ "'>×</button></td></tr>";
		});
		h += "</tbody></table>"
			+ "<button class='btn btn-xs btn-default' data-add='1'>"
			+ __("Add a part") + "</button>"
			+ " <span class='small' data-rest='1'></span>"
			+ "<p class='text-muted small' style='margin-top:10px'>"
			+ __("Each part becomes a draft of its own, kept here until its"
				+ " day and then approved and sent like any order, each with"
				+ " its own TAN. The purpose says which part it is.")
			+ "</p>";
		if (info.approved) {
			h += "<div class='alert alert-info small' style='margin-top:6px'>"
				+ __("{0} is approved. To split it, it is cancelled and entered"
					+ " again as a draft — you will be asked to confirm.",
					[esc(info.name)]) + "</div>";
		}
		return h;
	}

	function showRest(el) {
		const span = el.querySelector("[data-rest]");
		if (!span) return;
		const r = rest();
		span.textContent = r === 0
			? __("The parts add up to {0}.", [money(info.total)])
			: __("Still to distribute: {0}", [money(r)]);
		span.style.color = r === 0 ? "" : "#A32D2D";
	}

	function render() {
		const el = d.fields_dict.body.$wrapper.get(0);
		el.innerHTML = bodyHtml();
		el.querySelectorAll("[data-i]").forEach(function (inp) {
			const update = function () {
				const p = parts[Number(inp.getAttribute("data-i"))];
				p[inp.getAttribute("data-k")] = inp.value;
				// The "left" column depends on every row with the same day
				// and account, so it is redrawn for all of them.
				parts.forEach(function (q, j) {
					const cell = el.querySelector("[data-left='" + j + "']");
					if (!cell) return;
					const left = leftAfter(q.kefiya_login, q.date);
					cell.textContent = left === null ? "–" : money(left);
					cell.style.color = left !== null && left < 0 ? "#A32D2D" : "";
				});
				showRest(el);
			};
			inp.oninput = update;
			inp.onchange = update;
		});
		el.querySelectorAll("[data-drop]").forEach(function (b) {
			b.onclick = function (e) {
				e.preventDefault();
				parts.splice(Number(b.getAttribute("data-drop")), 1);
				render();
			};
		});
		el.querySelectorAll("[data-proposal]").forEach(function (b) {
			b.onclick = function (e) {
				e.preventDefault();
				useProposal(Number(b.getAttribute("data-proposal")));
			};
		});
		el.querySelector("[data-add]").onclick = function (e) {
			e.preventDefault();
			const r = rest();
			const last = parts[parts.length - 1];
			parts.push({ date: last ? last.date : info.today,
				kefiya_login: last ? last.kefiya_login : (usable()[0] || {}).kefiya_login,
				amount: r > 0 ? r : "" });
			render();
		};
		showRest(el);
	}

	function useProposal(index) {
		const p = (info.proposals || [])[index];
		parts = p ? p.parts.map(function (x) {
			return { date: x.date, kefiya_login: x.kefiya_login, amount: x.amount };
		}) : [];
		render();
	}

	function place() {
		if (!info) return;
		if (rest() !== 0) {
			frappe.msgprint(__("The parts must add up to {0}.",
				[money(info.total)]));
			return;
		}
		const go = function (reopen) {
			frappe.call({
				method: "kefiya.utils.limit_way_out.apply",
				args: { transfer_name: info.name, parts: JSON.stringify(parts),
					reopen: reopen ? 1 : 0 },
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
					message: (res.cancelled
						? "<p>" + __("{0} was cancelled.", [esc(res.cancelled)])
							+ "</p>" : "")
						+ "<p>" + __("{0} drafts: {1}. Approve and send each of"
							+ " them on its day.", [res.documents.length,
							res.documents.map(esc).join(", ")]) + "</p>" });
				if (opts.changed) opts.changed();
			});
		};
		if (!info.approved) { go(false); return; }
		frappe.confirm(
			__("{0} is approved. It will be cancelled and entered again as"
				+ " {1} drafts, one per part. Each of them has to be approved"
				+ " and sent again. Continue?", [esc(info.name), parts.length])
				+ "<ul style='margin-top:8px'>" + parts.map(function (p) {
				return "<li>" + esc(frappe.datetime.str_to_user(p.date)) + " · "
					+ esc(accountLabel(p.kefiya_login)) + " · "
					+ esc(money(p.amount)) + "</li>";
			}).join("") + "</ul>",
			function () { go(true); });
	}

	d.show();
	frappe.call({
		method: "kefiya.utils.limit_way_out.options",
		args: { transfer_name: name },
	}).then(function (r) {
		const res = (r && r.message) || {};
		if (res.status !== "ok") {
			d.fields_dict.body.$wrapper.html("<div class='text-danger'>"
				+ esc(res.message || __("No split can be planned.")) + "</div>");
			d.get_primary_btn().prop("disabled", true);
			return;
		}
		info = res;
		if ((info.proposals || []).length) {
			useProposal(0);
		} else {
			parts = [{ date: info.today, kefiya_login: (usable()[0] || {}).kefiya_login,
				amount: "" }];
			render();
		}
	});
	return d;
};
