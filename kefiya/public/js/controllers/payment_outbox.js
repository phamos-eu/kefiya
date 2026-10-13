// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

// The outgoing-payments list: every order between "entered" and "at the bank".
//
// This used to live as a 19 KB script inside a stored Custom HTML Block on one
// site. That made it invisible to review, untestable, unversioned, and every
// change a 19 KB round trip through the database. It is the page that moves
// money -- of everything in this app it had the weakest safety net. So it lives
// here now and the block is a bootstrap of a few lines.
//
// What the list does beyond showing rows:
//
//   * sorts by any column, and remembers that per user;
//   * opens an order by clicking anywhere in its row, rather than through a
//     row of small buttons that repeated on every line;
//   * acts on a selection: approve, hold, release, delete, send.
//
// The batch actions are the reason the selection exists at all. Approving
// thirty orders one at a time is thirty identical decisions; approving them
// together is the one decision that was actually made. Sending stays a
// separate, confirmed step -- approval locks an order, it does not move money.

frappe.provide("kefiya");

//: The columns, as data: the header, the sort key and the cell all read from
//: the same entry, so a column cannot be sortable by something other than what
//: it shows.
kefiya.OUTBOX_COLUMNS = [
	{
		key: "state", label: __("Order state"),
		sort: function (r) { return kefiya.outbox_state(r).rank; },
	},
	{
		key: "account", label: __("Paying account"),
		sort: function (r) { return (r.bank_account || "").toLowerCase(); },
	},
	{
		key: "recipient", label: __("Recipient / Reference"),
		sort: function (r) {
			return kefiya.outbox_recipient(r).toLowerCase();
		},
	},
	{
		key: "due", label: __("Due on"),
		// No date means "as soon as possible", which is sooner than any date.
		sort: function (r) { return r.execution_date || "0000-00-00"; },
	},
	{
		key: "amount", label: __("Amount"), right: true,
		sort: function (r) { return Number(r.total_amount || 0); },
	},
	{
		// The original receipt, counted where the payment is looked at. It is
		// counted across the documents the purpose names, not only the ones
		// hanging on the order itself -- the travel expense PDF lives on the
		// Business Trip and never moves.
		key: "receipt", label: __("Receipts"), right: true,
		sort: function (r) { return Number(r.receipts || 0); },
	},
	{
		key: "note", label: __("Remark"),
		sort: function (r) { return (r.blocked || "").toLowerCase(); },
	},
];

//: The state of an order in one word, plus the colours it is shown in and the
//: rank it sorts by. The rank follows the life of an order rather than the
//: alphabet: a draft comes before an approved order, and a sent one is done.
kefiya.outbox_state = function (r) {
	if (!r) return { key: "draft", label: "", rank: 0, fg: "", bg: "" };
	if (r.docstatus === 0) {
		return { key: "draft", label: __("Draft"), rank: 0,
			fg: "#854F0B", bg: "#FDF3E3" };
	}
	if (r.status === "Failed") {
		return { key: "failed", label: __("Failed to send"), rank: 1,
			fg: "#A32D2D", bg: "#FBEDED" };
	}
	// Not a failure and not on its way: the bank is holding the order until
	// somebody confirms the payee check. Saying "approved for sending" here
	// would leave a parked order looking like every other one in the list.
	if (r.vop_pending && r.status !== "Sent") {
		return { key: "vop", label: __("Payee check open"), rank: 2,
			fg: "#854F0B", bg: "#FDF3E3" };
	}
	if (r.on_hold) {
		return { key: "hold", label: __("Held back"), rank: 2,
			fg: "#A32D2D", bg: "#FBEDED" };
	}
	if (r.status === "Sent") {
		return { key: "sent", label: __("Sent to the bank"), rank: 5,
			fg: "#3B6D11", bg: "#EFF6E8" };
	}
	if (r.status === "Scheduled at Bank") {
		return { key: "bank", label: __("At the bank"), rank: 4,
			fg: "#14532d", bg: "#EDF3EE" };
	}
	return { key: "approved", label: __("Approved for sending"), rank: 3,
		fg: "#14532d", bg: "#EDF3EE" };
};

//: Everything a person needs to say yes to, before the money leaves.
//:
//: The confirmation used to be a recipient name and an amount. That is not
//: enough to check anything: it does not say which account is debited, it
//: does not show the recipient's IBAN, it does not name the reference, and it
//: does not say whether the order goes out today or on a date. The person
//: pressing this button is often not the person who entered the order --
//: which is the whole reason the payee check moved to entry time -- so what
//: they confirm has to be readable on its own.
//:
//: The execution and the kind come from kefiya.execution_sentence() and
//: kefiya.transfer_kind(), the same two the detail view uses. A confirmation
//: that described the execution differently from the box the order was
//: entered in would be worse than a short one.
kefiya.outbox_confirm_html = function (rows, payer) {
	const esc = frappe.utils.escape_html;
	const money = function (v) { return format_currency(Number(v || 0)); };
	const line = function (label, value) {
		return "<div class='zk-cf'><span>" + esc(label) + "</span><b>"
			+ value + "</b></div>";
	};

	let out = "<div class='zk-confirm'>";

	// --- who pays -----------------------------------------------------------
	out += "<div class='zk-cbox'><div class='zk-ch'>"
		+ __("Ordering party") + "</div>"
		+ line(__("Paying account"), esc((payer && payer.bank_account)
			|| (rows[0] && rows[0].bank_account) || ""));
	if (payer && payer.iban) {
		out += line(__("IBAN"), "<span class='zk-mono'>"
			+ esc(kefiya.iban_pretty(payer.iban)) + "</span>");
	}
	if (payer && payer.company) {
		out += line(__("Company"), esc(payer.company));
	}
	out += "</div>";

	// --- what leaves --------------------------------------------------------
	// One block per payment, not per order: a collective order carries several
	// recipients, and a row per order would hide all but the first.
	rows.forEach(function (row) {
		(row.items || []).forEach(function (item) {
			out += "<div class='zk-cbox'>"
				+ line(__("Recipient"), esc(item.recipient_name || "—"))
				+ line(__("IBAN"), "<span class='zk-mono'>"
					+ esc(kefiya.iban_pretty(item.recipient_iban)) + "</span>")
				+ (item.recipient_bic
					? line(__("BIC"), esc(item.recipient_bic)) : "")
				+ line(__("Amount"), money(item.amount))
				+ line(__("Reference"), esc(item.purpose || "—"))
				+ line(__("Execution"), esc(kefiya.execution_sentence(row)))
				+ line(__("Transfer type"), esc(kefiya.transfer_kind(row)))
				+ "<div class='zk-cref'>" + esc(row.name)
				+ (Number(row.receipts) > 0
					? " · " + __("{0} receipts", [row.receipts])
					: " · " + __("No receipt")) + "</div>"
				+ "</div>";
		});
	});

	let sum = 0;
	rows.forEach(function (r) { sum += Number(r.total_amount || 0); });
	if (rows.length > 1) {
		out += "<div class='zk-csum'>" + __("Total") + ": <b>"
			+ money(sum) + "</b></div>";
	}

	// The style travels with the markup. This is rendered by frappe.confirm,
	// which puts it in a modal in the main document -- not inside the page's
	// shadow root, where the outbox stylesheet lives and could not reach it.
	return out + "</div><style>"
		+ ".zk-confirm{max-height:52vh;overflow-y:auto;margin-top:10px}"
		+ ".zk-confirm .zk-cbox{border:1px solid var(--border-color);"
		+ "border-radius:6px;padding:8px 10px;margin-bottom:8px;"
		+ "background:var(--card-bg)}"
		+ ".zk-confirm .zk-ch{font-size:11px;text-transform:uppercase;"
		+ "letter-spacing:.04em;color:var(--text-muted);margin-bottom:4px}"
		+ ".zk-confirm .zk-cf{display:flex;justify-content:space-between;"
		+ "gap:12px;font-size:12px;padding:2px 0;border-bottom:1px solid "
		+ "var(--border-color)}"
		+ ".zk-confirm .zk-cf:last-of-type{border-bottom:0}"
		+ ".zk-confirm .zk-cf>span{color:var(--text-muted);white-space:nowrap}"
		+ ".zk-confirm .zk-cf>b{text-align:right;word-break:break-word}"
		+ ".zk-confirm .zk-mono{font-family:ui-monospace,Menlo,monospace}"
		+ ".zk-confirm .zk-cref{margin-top:5px;font-size:11px;"
		+ "color:var(--text-muted)}"
		+ ".zk-confirm .zk-csum{text-align:right;font-size:13px;padding:2px 2px"
		+ " 6px}"
		+ "</style>";
};

//: A draft that would go out today if somebody approved it.
//:
//: The server's answer, not a second opinion. It was written here first --
//: docstatus, on_hold and a date comparison -- which made three copies of
//: "is this order due yet": one in outbox.py, one in client.py, one here. The
//: browser copy decided which drafts the send button offered to approve and
//: the server copy decided whether to refuse the send, so the day they
//: disagreed the user would confirm a batch, watch it be approved, and then
//: be told it could not go. sendable_if_approved is the same function that
//: writes `blocked`, asked with the approval assumed.
kefiya.outbox_only_lacks_approval = function (r) {
	return !!(r && r.sendable_if_approved);
};

kefiya.outbox_recipient = function (r) {
	const items = (r && r.items) || [];
	if (items.length > 1) {
		return items.map(function (i) { return i.recipient_name || ""; })
			.join(", ");
	}
	return (items[0] && items[0].recipient_name) || "";
};

// What the user chose, kept for the next visit.
//
// Written to both stores on purpose. The framework's user settings follow the
// user to another browser, which is what "remembered" should mean; localStorage
// is what still answers when the framework has not loaded settings for this
// DocType yet. Reading prefers the framework, so the two cannot disagree for
// long.
kefiya.outbox_settings = {
	doctype: "Kefiya Transfer",
	key: "kefiya_payment_outbox",

	read: function () {
		try {
			const stored = (frappe.model.user_settings || {})[this.doctype];
			if (stored && stored[this.key]) return stored[this.key];
		} catch (ignored) {}
		try {
			const raw = window.localStorage.getItem(this.key);
			if (raw) return JSON.parse(raw);
		} catch (ignored) {}
		return {};
	},

	write: function (value) {
		try {
			window.localStorage.setItem(this.key, JSON.stringify(value));
		} catch (ignored) {}
		try {
			frappe.model.user_settings.save(this.doctype, this.key, value);
		} catch (ignored) {}
	},
};

kefiya.payment_outbox = function (root) {
	if (!root) return null;
	kefiya.outbox_style(root);

	const esc = frappe.utils.escape_html;
	const kept = kefiya.outbox_settings.read();

	const view = {
		rows: [], payers: [], can: {}, today: "",
		query: "", selected: {},
		showSent: !!kept.show_sent,
		sortBy: kept.sort_by || null,
		sortDir: kept.sort_dir === "desc" ? "desc" : "asc",
		busy: false,
	};

	function money(v) { return format_currency(Number(v || 0)); }

	function dmy(d) { return d ? frappe.datetime.str_to_user(d) : ""; }

	function errText(r) {
		const fromServer = kefiya.server_message && kefiya.server_message(r);
		if (fromServer) return fromServer;
		const m = (r && r.message) || "";
		return typeof m === "string" && m ? m : "";
	}

	// A second box saying "Unknown error." under the bank's own explanation is
	// worse than no second box.
	//
	// That is what a person saw: frappe.throw on the server renders its own
	// dialog -- "the bank asked for no TAN, but requires 1 signature ..." --
	// and then this catch added a box that knew nothing, because the thrown
	// message does not come back through _server_messages on the rejected
	// value. The reader is left wondering which of the two is the real answer.
	// So: say something only when there IS something to say.
	function reportFailure(title, r) {
		const text = errText(r);
		if (!text) return;
		frappe.msgprint({ title: title, indicator: "red", message: esc(text) });
	}

	function remember() {
		kefiya.outbox_settings.write({
			sort_by: view.sortBy, sort_dir: view.sortDir,
			show_sent: view.showSent ? 1 : 0,
		});
	}

	// --- data ---------------------------------------------------------------

	function load() {
		root.innerHTML = "<div class='zk-load'>" + __("Loading …") + "</div>";
		return frappe.call({
			// In the app, not a Server Script stored on one site. That script
			// wrote its reasons as German literals typed without umlauts, and
			// nothing in a stored script notices; here every one of them is a
			// source string with a translation a test reads.
			method: "kefiya.utils.outbox.outbox_data",
			args: { q: view.query, show_sent: view.showSent ? 1 : 0 },
		}).then(function (r) {
			const m = (r && r.message) || {};
			view.rows = m.rows || [];
			view.payers = m.payers || [];
			view.can = m.can || {};
			view.today = m.today || "";
			// Which way the send dialog offers first. It still asks.
			view.sendDefault = m.send_default || "One after another";
			// An order that is gone must not stay selected: it would be
			// counted in the footer sum and sent to an endpoint that then
			// refuses the whole batch.
			const alive = {};
			view.rows.forEach(function (row) { alive[row.name] = 1; });
			Object.keys(view.selected).forEach(function (name) {
				if (!alive[name]) delete view.selected[name];
			});
			render();
		}).catch(function (r) {
			root.innerHTML = "<div class='zk-err'>"
				+ __("The outgoing payments could not be loaded.")
				+ "<br>" + esc(errText(r) || __("Unknown error.")) + "</div>";
		});
	}

	function sorted() {
		if (!view.sortBy) return view.rows.slice();
		const column = kefiya.OUTBOX_COLUMNS.find(function (c) {
			return c.key === view.sortBy;
		});
		if (!column) return view.rows.slice();
		const sign = view.sortDir === "desc" ? -1 : 1;
		return view.rows.slice().sort(function (a, b) {
			const x = column.sort(a);
			const y = column.sort(b);
			if (x < y) return -sign;
			if (x > y) return sign;
			return 0;
		});
	}

	function rowOf(name) {
		return view.rows.find(function (r) { return r.name === name; }) || null;
	}

	function selection() {
		return view.rows.filter(function (r) { return view.selected[r.name]; });
	}

	// What a given action may actually touch. Every batch button reads its
	// count from here, so a button can never offer more than it will do.
	function applicable(what) {
		return selection().filter(function (r) {
			if (what === "approve") return r.docstatus === 0;
			if (what === "delete") return r.docstatus === 0;
			if (what === "hold") {
				return r.docstatus === 1 && !r.on_hold && r.status !== "Sent";
			}
			if (what === "release") {
				return r.docstatus === 1 && !!r.on_hold;
			}
			// Sending takes a draft as well: the send button approves what it
			// is about to send. Two buttons for one intention was a step
			// people took by rote, and a step taken by rote is not an
			// approval -- it is a click. "Approve for sending" stays for the
			// case where somebody really only wants to approve.
			if (what === "send") {
				return !!r.sendable || kefiya.outbox_only_lacks_approval(r);
			}
			if (what === "approve_to_send") {
				return !r.sendable && kefiya.outbox_only_lacks_approval(r);
			}
			return false;
		});
	}

	// --- rendering ----------------------------------------------------------

	function header() {
		let h = "<div class='zk-bar'>";
		if (view.can.create) {
			h += "<button class='zk-primary' data-act='new'>"
				+ __("New transfer") + "</button>";
		}
		h += "<input data-act='q' placeholder='"
			+ esc(__("Search — recipient, IBAN, reference, amount")) + "'"
			+ " value=\"" + esc(view.query) + "\">";
		h += "<label class='zk-chk'><input type='checkbox' data-act='sent'"
			+ (view.showSent ? " checked" : "") + "> "
			+ __("show sent") + "</label>";
		h += "<button data-act='reload'>" + __("Refresh") + "</button></div>";
		return h;
	}

	// Whether there is a receipt, at a glance. An order without one is the one
	// worth stopping at, so the absence is stated rather than left as an empty
	// cell that could equally mean "not loaded".
	function receiptCell(r) {
		const count = Number(r.receipts || 0);
		if (!count) {
			return "<span class='zk-nodoc' title=\"" + esc(__("No receipt"))
				+ "\">—</span>";
		}
		return "<span class='zk-doc' title=\"" + esc(__("Open the order to"
			+ " see the receipts")) + "\">\u{1F4CE} " + count + "</span>";
	}

	function table() {
		const arrow = function (key) {
			if (view.sortBy !== key) return "";
			return view.sortDir === "asc" ? " ▲" : " ▼";
		};
		let h = "<table class='zk-tb'><thead><tr>";
		h += "<th class='zk-cx'><input type='checkbox' data-act='all'"
			+ " title=\"" + esc(__("Select all listed orders")) + "\"></th>";
		kefiya.OUTBOX_COLUMNS.forEach(function (c) {
			h += "<th class='zk-sortable" + (c.right ? " r" : "") + "'"
				+ " data-col='" + esc(c.key) + "'"
				+ " title=\"" + esc(__("Sort by this column. Clicking the"
					+ " sorted column again reverses it, once more restores"
					+ " the default order.")) + "\">"
				+ esc(c.label) + arrow(c.key) + "</th>";
		});
		h += "</tr></thead><tbody>";

		sorted().forEach(function (r) {
			const st = kefiya.outbox_state(r);
			const items = r.items || [];
			const many = items.length > 1;
			const who = many
				? __("{0} recipients", [items.length])
				: (kefiya.outbox_recipient(r) || "—");
			const sub = many
				? esc(kefiya.outbox_recipient(r)).slice(0, 110)
				: (esc((items[0] && items[0].purpose) || "")
					+ (items[0] && items[0].recipient_iban
						? "<br><span class='zk-iban'>"
							+ esc(kefiya.iban_pretty(items[0].recipient_iban))
							+ "</span>"
						: ""));
			let due;
			if (!r.execution_date) {
				due = "<span class='zk-sub'>" + __("as soon as possible")
					+ "</span>";
			} else {
				due = dmy(r.execution_date) + "<div class='zk-sub'>"
					+ (r.manage_due_date
						? __("held here")
						: __("dated at the bank"))
					+ "</div>";
			}

			h += "<tr class='zk-row' data-n='" + esc(r.name) + "' tabindex='0'"
				+ " title=\"" + esc(__("Open this order")) + "\">"
				+ "<td class='zk-cx'><input type='checkbox' data-pick='"
					+ esc(r.name) + "'"
					+ (view.selected[r.name] ? " checked" : "") + "></td>"
				+ "<td><span class='zk-badge' style='color:" + st.fg
					+ ";background:" + st.bg + "'>" + esc(st.label)
					+ "</span></td>"
				+ "<td>" + esc(r.bank_account || "")
					+ "<div class='zk-sub'>" + esc(r.company || "")
					+ "</div></td>"
				+ "<td><b>" + esc(who) + "</b><div class='zk-sub'>" + sub
					+ "</div></td>"
				+ "<td>" + due + "</td>"
				+ "<td class='r'>" + money(r.total_amount) + "</td>"
				+ "<td class='r'>" + receiptCell(r) + "</td>"
				+ "<td class='zk-sub'>" + esc(r.blocked || "")
					+ (r.instant_payment
						? "<div>" + __("Instant payment") + "</div>" : "")
					+ "</td></tr>";
		});
		return h + "</tbody></table>";
	}

	function footer() {
		const sel = selection();
		let sum = 0;
		sel.forEach(function (r) { sum += Number(r.total_amount || 0); });
		let open = 0;
		view.rows.forEach(function (r) {
			if (r.docstatus === 1 && r.status !== "Sent") {
				open += Number(r.total_amount || 0);
			}
		});

		let h = "<div class='zk-foot'><div class='zk-sums'>"
			+ "<div>" + __("Selected: {0} orders · {1}",
				[sel.length, money(sum)]) + "</div>"
			+ "<div class='zk-sub'>"
			+ __("Approved and still here: {0}", [money(open)])
			+ "</div></div><div class='zk-acts'>";

		const button = function (act, label, count, cls) {
			return "<button data-act='" + act + "' class='" + (cls || "") + "'"
				+ (count ? "" : " disabled") + ">"
				+ esc(label) + (count ? " (" + count + ")" : "") + "</button>";
		};

		if (view.can.delete) {
			h += button("delete", __("Delete"), applicable("delete").length);
		}
		if (view.can.submit) {
			h += button("release", __("Cancel hold"),
				applicable("release").length);
			h += button("hold", __("Hold back"), applicable("hold").length);
			h += button("approve", __("Approve for sending"),
				applicable("approve").length);
			// The button says what it will do. With a draft in the selection
			// that is two things, and a button that only says "Send" would
			// approve one without ever mentioning it.
			h += button("send",
				applicable("approve_to_send").length
					? __("Approve and send")
					: __("Send"),
				applicable("send").length, "zk-send");
		} else {
			h += "<div class='zk-sub'>"
				+ __("Sending needs the right to approve.") + "</div>";
		}
		return h + "</div></div>";
	}

	function render() {
		let h = header();
		if (!view.rows.length) {
			h += "<div class='zk-empty'>" + (view.query
				? __("Nothing found.")
				: __("There is nothing to send. Transfers collect here until"
					+ " they go out.")) + "</div>";
			root.innerHTML = h;
			bind();
			return;
		}
		root.innerHTML = h + table() + footer();
		bind();
	}

	// --- events -------------------------------------------------------------

	function bind() {
		const q = root.querySelector("[data-act='q']");
		if (q) {
			// stopPropagation: the desk binds single keys as shortcuts, and
			// typing an IBAN would otherwise trigger them.
			q.onkeydown = function (e) {
				e.stopPropagation();
				if (e.key === "Enter") { view.query = q.value; load(); }
			};
			q.onchange = function () { view.query = q.value; load(); };
		}

		root.querySelectorAll("[data-act]").forEach(function (el) {
			const act = el.getAttribute("data-act");
			if (act === "q") return;
			if (act === "sent") {
				el.onchange = function () {
					view.showSent = el.checked;
					remember();
					load();
				};
				return;
			}
			if (act === "all") {
				el.onchange = function () {
					view.rows.forEach(function (r) {
						if (el.checked) view.selected[r.name] = true;
						else delete view.selected[r.name];
					});
					render();
				};
				return;
			}
			el.onclick = function () { act_on(act); };
		});

		root.querySelectorAll("[data-pick]").forEach(function (el) {
			el.onchange = function () {
				const name = el.getAttribute("data-pick");
				if (el.checked) view.selected[name] = true;
				else delete view.selected[name];
				render();
			};
		});

		root.querySelectorAll("th.zk-sortable").forEach(function (th) {
			th.onclick = function () { sortBy(th.getAttribute("data-col")); };
		});

		root.querySelectorAll("tr.zk-row").forEach(function (tr) {
			const open = function (e) {
				// The checkbox is in the row and does something else.
				if (e.target && e.target.closest
						&& e.target.closest(".zk-cx")) return;
				details(tr.getAttribute("data-n"));
			};
			tr.onclick = open;
			tr.onkeydown = function (e) {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					open(e);
				}
			};
		});
	}

	// Three states, not two: ascending, descending, and back to the order the
	// server sends -- which is the useful one (drafts first, then by due date)
	// and would otherwise be unreachable once a column had been clicked.
	function sortBy(key) {
		if (view.sortBy !== key) {
			view.sortBy = key;
			view.sortDir = "asc";
		} else if (view.sortDir === "asc") {
			view.sortDir = "desc";
		} else {
			view.sortBy = null;
			view.sortDir = "asc";
		}
		remember();
		render();
	}

	function act_on(act) {
		if (act === "reload") return load();
		if (act === "new") return newTransfer();
		if (act === "approve") return approve();
		if (act === "delete") return remove();
		if (act === "hold") return hold(1);
		if (act === "release") return hold(0);
		if (act === "send") return send();
	}

	function details(name) {
		const r = rowOf(name);
		if (!r) return;
		kefiya.transfer_details(r, {
			payers: view.payers,
			canWrite: !!view.can.write,
			onChanged: load,
		});
	}

	function newTransfer() {
		kefiya.transfer_form({ payers: view.payers, onSaved: load });
	}

	// --- the actions --------------------------------------------------------

	// Approving is the step the page is asked about most often, so it says
	// what it does rather than asking "are you sure": amounts and recipients
	// are locked from here on, and nothing is paid yet.
	function approve() {
		const rows = applicable("approve");
		if (!rows.length || view.busy) return;
		const names = rows.map(function (r) { return r.name; });
		let sum = 0;
		rows.forEach(function (r) { sum += Number(r.total_amount || 0); });

		frappe.confirm(
			"<div>" + __("Approve {0} orders over {1}?",
				[rows.length, money(sum)]) + "</div>"
			+ "<div class='text-muted small' style='margin-top:8px'>"
			+ __("Approving locks the amounts and the recipients. No money"
				+ " moves: sending is the next, separate step and the bank"
				+ " asks for a TAN then.") + "</div>",
			function () {
				view.busy = true;
				frappe.call({
					method: "kefiya.utils.client.approve_transfers",
					args: { transfer_names: JSON.stringify(names) },
					freeze: true,
					freeze_message: __("Approving …"),
				}).then(function (r) {
					view.busy = false;
					const m = (r && r.message) || {};
					reportBatch(m.approved || [], m.refused || [],
						__("Approved: {0}", [(m.approved || []).length]),
						__("Not approved"));
					load();
				}).catch(function (r) {
					view.busy = false;
					reportFailure(__("Not approved"), r);
					load();
				});
			});
	}

	function hold(on) {
		const rows = applicable(on ? "hold" : "release");
		if (!rows.length || view.busy) return;
		const names = rows.map(function (r) { return r.name; });
		view.busy = true;
		frappe.call({
			method: "kefiya.utils.client.set_transfer_hold",
			args: { transfer_names: JSON.stringify(names), on_hold: on ? 1 : 0 },
		}).then(function () {
			view.busy = false;
			frappe.show_alert({
				message: on
					? __("Held back: {0}", [names.length])
					: __("Released again: {0}", [names.length]),
				indicator: "green",
			}, 4);
			load();
		}).catch(function (r) {
			view.busy = false;
			reportFailure(__("Not changed"), r);
			load();
		});
	}

	// Deleting is one call per document -- there is no batch endpoint for it,
	// and inventing one would mean writing a second delete path past the
	// framework's permission checks. They run in sequence so a refusal names
	// the order it belongs to.
	function remove() {
		const rows = applicable("delete");
		if (!rows.length || view.busy) return;

		frappe.confirm(
			__("Delete {0} drafts? They are gone afterwards.", [rows.length]),
			function () {
				view.busy = true;
				const gone = [];
				const failed = [];
				let chain = Promise.resolve();
				rows.forEach(function (r) {
					chain = chain.then(function () {
						return frappe.call({
							method: "frappe.client.delete",
							args: { doctype: "Kefiya Transfer", name: r.name },
						}).then(function () {
							gone.push(r.name);
							delete view.selected[r.name];
						}).catch(function (e) {
							failed.push({ name: r.name, reason: errText(e) });
						});
					});
				});
				chain.then(function () {
					view.busy = false;
					reportBatch(gone, failed,
						__("Deleted: {0}", [gone.length]),
						__("Not deleted"));
					load();
				});
			});
	}

	function send() {
		const rows = applicable("send");
		if (!rows.length || view.busy) return;

		// One collective order leaves from exactly ONE account -- that is
		// what makes it one order. Sent one after another, each order pays
		// from its own account, so a selection across accounts is only
		// refused for the collective way, and only there.
		const groups = groupByAccount(rows);

		const skipped = selection().length - rows.length;
		let sum = 0;
		rows.forEach(function (r) { sum += Number(r.total_amount || 0); });

		// One block per account, each with its own ordering party. A single
		// block over a mixed selection would name one account and debit
		// several.
		let message = "<div>"
			+ (groups.length > 1
				? __("{0} orders over {1} go to the bank from {2} accounts.",
					[rows.length, money(sum), groups.length])
				: __("{0} orders over {1} go from {2} to the bank.",
					[rows.length, money(sum), esc(rows[0].bank_account || "")]))
			+ "</div>"
			+ groups.map(function (group) {
				return kefiya.outbox_confirm_html(group.rows, group.payer);
			}).join("");
		if (skipped > 0) {
			message += "<div class='text-muted small' style='margin-top:8px'>"
				+ __("{0} selected orders are not being sent — they are held"
					+ " back, already sent, or not due yet.", [skipped])
				+ "</div>";
		}
		// Sending approves. Said out loud, because approving locks amounts and
		// recipients and that is not something to discover afterwards.
		const toApprove = applicable("approve_to_send");
		if (toApprove.length) {
			message += "<div class='text-muted small' style='margin-top:8px'>"
				+ __("{0} of them are still drafts and are approved as they"
					+ " are sent. Approving locks their amounts and"
					+ " recipients.", [toApprove.length])
				+ "</div>";
		}
		message += "<div class='text-muted small' style='margin-top:8px'>"
			+ __("The bank will usually ask for a release (TAN or a"
				+ " confirmation in its app). Nothing is debited until then.")
			+ "</div>";

		if (rows.length === 1) {
			frappe.confirm(message, function () {
				// One call, and the approval happens inside it -- AFTER the
				// server has accepted the batch. Approving here first, as
				// this page did, meant a batch refused for mixing execution
				// dates left its drafts locked for nothing, undoable only by
				// cancelling and re-entering them. The endpoint knows every
				// refusal; the browser knows none.
				handToBank([rows[0].name], rows[0].kefiya_login,
					toApprove.length);
			});
			return;
		}
		askHowToSend(rows, message, toApprove, groups.length === 1);
	}

	//: The selection by paying account, in the order the rows came in. The
	//: payer carries the IBAN, which is what somebody comparing this against
	//: their online banking reads; the row names only the account.
	function groupByAccount(rows) {
		const groups = [];
		const seen = {};
		rows.forEach(function (r) {
			if (seen[r.kefiya_login] === undefined) {
				seen[r.kefiya_login] = groups.length;
				groups.push({
					login: r.kefiya_login,
					payer: view.payers.find(function (p) {
						return p.login === r.kefiya_login;
					}) || null,
					rows: [],
				});
			}
			groups[seen[r.kefiya_login]].rows.push(r);
		});
		return groups;
	}


	function handToBank(names, login, approving) {
		view.busy = true;
		frappe.call({
			method: "kefiya.utils.client.send_transfer_outbox",
			args: { transfer_names: JSON.stringify(names),
				user_scope: login, confirmed: 1,
				approve_drafts: approving ? 1 : 0 },
			freeze: true,
			freeze_message: approving
				? __("Approving and sending …") : __("Sending to the bank …"),
		}).then(function (r) {
			view.busy = false;
			const m = (r && r.message) || {};
			// An order the approval turned away is named, whatever else
			// happened. A batch that reports only its successes is how half a
			// selection goes missing without anybody noticing.
			if ((m.refused || []).length) {
				reportBatch([], m.refused,
					__("Approved: {0}", [(m.approved || []).length]),
					__("Not approved"));
			}
			reportSendResult(m, (m.sent || []).length || names.length, login,
				m.sent || names);
		}).catch(function (r) {
			view.busy = false;
			reportFailure(__("Not sent"), r);
			load();
		});
	}

	// What a send came back with, said once. The release after a parked payee
	// check comes back the same three ways, and this was written out twice --
	// so the next status the server learns to return would have had two places
	// to be handled, and would have been handled in one.
	// What a send answered, in one word. Every caller asks this and none
	// reads the statuses itself, so a status the server learns to return is
	// handled in one place or in none -- never in one of two.
	function sendOutcome(m) {
		if (!m || m.status === "error") return "error";
		if (m.status === "vop_mismatch") return "payee";
		if (m.status === "tan_required") {
			return (m.decoupled && kefiya.await_release) ? "release" : "tan";
		}
		return "done";
	}

	function reportSendResult(m, count, login, names) {
		const outcome = sendOutcome(m);
		if (outcome === "error") {
			frappe.msgprint({ title: __("Not sent"), indicator: "red",
				message: esc(m.message || "") });
		} else if (outcome === "payee") {
			// Nothing was handed to the bank. This branch did not exist, so a
			// parked payee check fell into the success case below and was
			// reported in green -- for an order that never left.
			kefiya.vop_prompt({
				login: login, scope: login, answer: m.vop_result,
				// With the names. Dropping them here left the release box
				// with nothing to hand send_transfer_tan, so an order the
				// bank took after a payee release was marked nowhere.
				onResult: function (r2) {
					r2 = r2 || {};
					reportSendResult(r2, count, login,
						(r2.transfer_names || []).length
							? r2.transfer_names : names);
				}
			});
			return;
		} else if (outcome === "release" || outcome === "tan") {
			frappe.show_alert({ message: __("The bank asks for a release."),
				indicator: "orange" }, 8);
			// A decoupled release is given in the banking app, and nobody
			// asked the bank afterwards whether it had been: the order stayed
			// "Due" with no error at all. So this page asks, in later
			// requests, until the bank says yes or the wait runs out.
			if (outcome === "release") {
				awaitRelease(login, names || [], count);
				return;
			}
		} else {
			view.selected = {};
			frappe.show_alert({
				message: __("Handed to the bank: {0} orders", [count]),
				indicator: "green" }, 8);
		}
		load();
	}

	// The release after a parked decoupled challenge, and what it came to.
	// "submitted" here means the server has already marked the orders sent.
	function awaitRelease(login, names, count) {
		kefiya.await_release({
			login: login, names: names, scope: login,
			done: function (status, message) {
				if (status === "submitted") {
					view.selected = {};
					frappe.show_alert({
						message: __("Released in the banking app. Handed to"
							+ " the bank: {0} orders", [count]),
						indicator: "green" }, 8);
				} else if (status === "error") {
					frappe.msgprint({ title: __("Release failed"),
						indicator: "red", message: esc(message || "") });
				} else {
					frappe.msgprint({ title: __("Still waiting for the release"),
						indicator: "orange",
						message: __("The bank has not reported the release."
							+ " If you confirmed it in the app, check your"
							+ " online banking before sending again; the"
							+ " order is still marked as not sent here.") });
				}
				load();
			}
		});
	}

	// Several orders can go two ways, and they are two different payments.
	// A collective order is ONE order at the bank carrying every payment,
	// authorised once; sent one after another, each is its own order and the
	// bank asks for each. This page picked the first silently, and the user
	// found out from the bank.
	//
	// No preselection: the choice is the point of asking.
	//: The two ways, as the settings name them and as the dialog offers them.
	//: One list, so a default stored on the instance cannot mean something
	//: the dialog does not offer.
	const TOGETHER = "As one collective order";
	const SEND_WAYS = [
		{ stored: "One after another",
			label: __("One after another — a release for each") },
		{ stored: TOGETHER,
			label: __("As one collective order — a single release") },
	];

	function askHowToSend(rows, message, toApprove, oneAccount) {
		// Across accounts only one way exists, and it is offered as the only
		// one rather than refusing the selection: a collective order is one
		// order from one account, but sending them one after another pays
		// each from its own.
		const ways = oneAccount
			? SEND_WAYS
			: SEND_WAYS.filter(function (way) {
				return way.stored !== TOGETHER;
			});
		const preset = ways.find(function (way) {
			return way.stored === view.sendDefault;
		}) || ways[0];
		const dialog = new frappe.ui.Dialog({
			title: __("Send {0} orders", [rows.length]),
			fields: [
				{ fieldtype: "HTML", options: message },
				{
					fieldtype: "Select", fieldname: "how", reqd: 1,
					label: __("How should these go to the bank?"),
					options: ways.map(function (way) { return way.label; }),
					// Pre-filled from Kefiya Settings, never decided from
					// there: the question is asked either way.
					default: preset.label,
					description: oneAccount
						? __("A collective order reaches the bank as a single"
							+ " order over the total, and one release covers"
							+ " all of it.")
						: __("Orders from several accounts are selected. They"
							+ " can only go one after another — a collective"
							+ " order leaves from exactly one account.")
				}
			],
			primary_action_label: __("Send"),
			primary_action: function (values) {
				const chosen = ways.find(function (way) {
					return way.label === values.how;
				});
				if (!chosen) return;
				dialog.hide();
				if (chosen.stored === TOGETHER) {
					handToBank(rows.map(function (r) { return r.name; }),
						rows[0].kefiya_login, toApprove.length);
				} else {
					sendOneByOne(rows, toApprove);
				}
			}
		});
		dialog.show();
	}

	// One order, one release, then the next.
	//
	// It stops at the first order it cannot see through -- a typed TAN is
	// answered in its own box, and nothing tells this loop when that box is
	// done -- and then says how far it got, with the rest still selected so
	// pressing Send again carries on. Guessing that the TAN went through
	// would mark orders sent that never left.
	function sendOneByOne(orders, toApprove) {
		// Each order carries its own paying account: across accounts this is
		// the only way, and every send has to reach the bank the order
		// actually pays from.
		const names = orders.map(function (r) { return r.name; });
		// Which of them are still drafts. Each is approved on its own turn,
		// immediately before its own send -- not all of them upfront. An
		// order that never goes out because the run stopped earlier must not
		// be left approved, and approving locks amounts and recipients.
		const isDraft = {};
		(toApprove || []).forEach(function (r) { isDraft[r.name] = 1; });
		view.busy = true;

		function finish(sent, name, why) {
			view.busy = false;
			if (why) {
				frappe.msgprint({
					title: __("Stopped at {0}", [name]), indicator: "orange",
					message: "<div>" + __("Sent so far: {0} of {1}.",
						[sent, names.length]) + "</div>"
						+ "<div style='margin-top:8px'>" + esc(why) + "</div>",
				});
			} else {
				view.selected = {};
				frappe.show_alert({
					message: __("Sent one after another: {0}", [sent]),
					indicator: "green" }, 8);
			}
			load();
		}

		// Asked through sendOutcome like every other caller, so a status
		// the server learns to return is never handled here and not there.
		function after(index, sent, m) {
			const name = names[index];
			switch (sendOutcome(m)) {
			case "done":
				step(index + 1, sent + 1);
				return;
			case "payee":
				kefiya.vop_prompt({
					login: orders[index].kefiya_login,
					scope: orders[index].kefiya_login,
					answer: m.vop_result,
					onResult: function (r2) { after(index, sent, r2 || {}); },
				});
				return;
			case "release":
				kefiya.await_release({
					login: orders[index].kefiya_login, names: [name],
					scope: orders[index].kefiya_login,
					done: function (status, message) {
						if (status === "submitted") { step(index + 1, sent + 1); }
						else {
							finish(sent, name, message
								|| __("The bank has not reported the release."));
						}
					},
				});
				return;
			case "tan":
				finish(sent, name, __("The bank asks for a TAN for this order."
					+ " Answer it in the box that opened; the remaining orders"
					+ " stay selected — press Send again afterwards."));
				return;
			default:
				finish(sent, name, m.message || __("Unknown error"));
			}
		}

		function step(index, sent) {
			if (index >= names.length) { finish(sent, null, null); return; }
			const name = names[index];
			const ready = isDraft[name]
				? frappe.call({
					method: "kefiya.utils.client.approve_transfers",
					args: { transfer_names: JSON.stringify([name]) },
					freeze: true, freeze_message: __("Approving …"),
				}).then(function (r) {
					const m = (r && r.message) || {};
					const refused = (m.refused || [])[0];
					if (refused) {
						finish(sent, name, refused.reason
							|| __("Not approved"));
						return false;
					}
					return true;
				})
				: Promise.resolve(true);

			ready.then(function (go) {
				if (!go) return;
				return frappe.call({
					method: "kefiya.utils.client.submit_kefiya_transfer",
					args: { transfer_name: name,
						user_scope: orders[index].kefiya_login,
						confirmed: 1 },
					freeze: true,
					freeze_message: __("Sending {0} of {1} …",
						[index + 1, names.length]),
				}).then(function (r) {
					after(index, sent, (r && r.message) || {});
				});
			}).catch(function (r) {
				finish(sent, name, errText(r));
			});
		}

		step(0, 0);
	}

	// One message for a batch, and it names what did not work. A batch that
	// reports only its successes is how half a selection goes missing without
	// anybody noticing.
	function reportBatch(done, failed, okText, failTitle) {
		if (!failed.length) {
			frappe.show_alert({ message: okText, indicator: "green" }, 5);
			return;
		}
		const lines = failed.map(function (f) {
			return "<tr><td>" + esc(f.name) + "</td><td>"
				+ esc(f.reason || "") + "</td></tr>";
		}).join("");
		frappe.msgprint({
			title: failTitle, indicator: "orange",
			message: "<div>" + okText + "</div>"
				+ "<table style='width:100%;border-collapse:collapse;"
				+ "margin-top:8px'>" + lines + "</table>",
		});
	}

	// The bank asks for its release over the live connection. Without this the
	// send would sit there silently. The prompt itself is the shared one -- it
	// names the bank and the account, which a second copy here did not.
	//
	// Bound once per page load, but reading kefiya._outbox rather than this
	// closure: the page is rebuilt on every visit, and a handler holding the
	// first view would answer for a list nobody is looking at any more.
	// frappe.realtime.off() is not an option -- it would take the account
	// fetch's prompt with it.
	function bindTan() {
		if (kefiya._outbox_tan_bound) return;
		kefiya._outbox_tan_bound = true;
		try {
			frappe.realtime.on("fints_tan_interaction_required",
				function (data) {
					const live = kefiya._outbox;
					if (!live || !live.busy) return;
					if (kefiya.tan_prompt) kefiya.tan_prompt(data, live.load);
				});
		} catch (ignored) {}
	}

	view.load = load;
	view.render = render;
	kefiya._outbox = view;
	bindTan();
	load();
	return view;
};
