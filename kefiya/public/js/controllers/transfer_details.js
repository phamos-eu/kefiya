// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

// One outgoing transfer, laid out as the transfer form it is.
//
// Two rewrites got this here. The first replaced a six-row table of database
// fields with something that at least said what would happen to the order.
// The second is this one, and it came out of a screenshot: the box still read
// like a database, not like a transfer slip, and three things in it were
// plainly wrong.
//
//   "Bundesland"  -- the label was the source string "State", left to the
//                    framework to translate. The framework knows that word as
//                    the address field, so it named the order's state after a
//                    federal state. The lesson of the translation file, in
//                    reverse: a generic word is not safe just because we did
//                    not translate it ourselves.
//   "Sent"        -- the raw stored value, shown untranslated next to its own
//                    German explanation.
//   "Due date"    -- English, and it repeated the line above it word for word.
//
// The receipt was missing too, and that one is not a label. A travel expense
// PDF hangs on the Business Trip it belongs to; the only thing tying it to
// this order is the document's name inside the purpose line. So that is what
// is followed -- see kefiya.transfer_referenced_documents().

frappe.provide("kefiya");

kefiya.transfer_details = function (row, options) {
	if (!row) return;
	options = options || {};

	const dialog = new frappe.ui.Dialog({
		title: __("Transfer"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Open document"),
		primary_action: function () {
			dialog.hide();
			frappe.set_route("Form", "Kefiya Transfer", row.name);
		},
	});

	// Correcting happens from here, so the row itself needs no second button.
	// Only a draft can be corrected: after approval the amounts and the
	// recipients are locked, which is the entire point of approving.
	if (row.docstatus === 0 && options.canWrite !== false) {
		dialog.set_secondary_action_label(__("Correct"));
		dialog.set_secondary_action(function () {
			dialog.hide();
			kefiya.transfer_form({
				row: row,
				payers: options.payers || [],
				onSaved: options.onChanged || function () {},
			});
		});
	}

	const field = dialog.fields_dict.body;
	field.$wrapper.html(kefiya.transfer_details_html(row, undefined));
	dialog.show();

	// The receipts are fetched only once the order is actually looked at.
	// Shipping them with every row of the list would fetch hundreds of file
	// records to show none of them.
	kefiya.transfer_attachments(row).then(function (files) {
		field.$wrapper.html(kefiya.transfer_details_html(row, files));
	});
};

//: Document names as they appear inside a purpose line -- BT-0001,
//: KEF-TRF-2026-00001, RE-260427-034. Deliberately narrow: two to eight
//: capitals, then digit groups. A looser pattern would match invoice numbers
//: of the recipient and go looking for documents that are not ours.
kefiya.DOCNAME_IN_TEXT = /\b[A-Z]{2,8}(?:-[A-Z0-9]{2,6})*-\d{3,}\b/g;

//: The documents this order names, as {name, doctype}.
//:
//: The server answers this on the outbox rows and resolves the doctype on the
//: way, because it is the only side that can. Read from the row where it is
//: there; parsed here where it is not, so a caller that hands over a bare
//: document still gets the names.
kefiya.transfer_referenced_documents = function (row) {
	if (row && Array.isArray(row.referenced)) {
		return row.referenced.map(function (entry) {
			return typeof entry === "string"
				? { name: entry, doctype: null } : entry;
		});
	}

	const found = [];
	((row && row.items) || []).forEach(function (item) {
		const hits = String(item.purpose || "").match(kefiya.DOCNAME_IN_TEXT);
		(hits || []).forEach(function (hit) {
			if (hit !== row.name && !found.some(function (f) {
				return f.name === hit;
			})) found.push({ name: hit, doctype: null });
		});
	});
	return found;
};

// Every receipt that belongs to this order: the ones attached to it, and the
// ones attached to whatever document its purpose names.
//
// The second half is the whole point. Nobody attaches the travel expense PDF
// to the transfer -- it is created on the Business Trip and stays there, and
// the transfer says "Reisekosten BT-0001" in its purpose. Reading that is not
// elegant, but it is the only link that exists, and a receipt nobody can find
// from the payment is a receipt that gets asked for twice.
kefiya.transfer_attachments = function (row) {
	const names = [typeof row === "string" ? row : row.name];
	if (typeof row !== "string") {
		kefiya.transfer_referenced_documents(row).forEach(function (entry) {
			names.push(entry.name);
		});
	}

	return frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "File",
			filters: { attached_to_name: ["in", names] },
			fields: ["name", "file_name", "file_url", "is_private",
				"file_size", "attached_to_doctype", "attached_to_name"],
			limit_page_length: 0,
		},
	}).then(function (r) {
		return (r && r.message) || [];
	}).catch(function () {
		// A receipt that cannot be listed must not take the whole dialog with
		// it -- the order itself is what the reader came for.
		return null;
	});
};

//: What a receipt looks like, when it can be shown at all.
//:
//: Two kinds carry almost every receipt here: a PDF out of the travel expense
//: sheet, and a photograph of one. Both are shown. Anything else gets its name
//: and a link, which is what it had before -- guessing at a viewer for a file
//: type nobody sends is how a dialog ends up broken for the one person who
//: does send it.
kefiya.receipt_preview = function (file) {
	const url = String((file && file.file_url) || "");
	if (!url) return "";
	const esc = frappe.utils.escape_html;
	const name = String(file.file_name || file.name || "").toLowerCase();
	const isImage = /\.(png|jpe?g|gif|webp|bmp|avif)$/.test(name);
	const isPdf = /\.pdf$/.test(name);

	if (isImage) {
		return "<img src='" + esc(url) + "' alt='"
			+ esc(file.file_name || "") + "' loading='lazy'>";
	}
	if (isPdf) {
		return "<embed src='" + esc(url) + "#toolbar=0' type='application/pdf'>";
	}
	return "<div class='kef-noprev'>"
		+ __("This file type cannot be shown here — open it with the link"
			+ " above.") + "</div>";
};

//: When an order goes out, and who holds it until then -- in a sentence.
//:
//: "execution_date = null, manage_due_date = 0" is not something anybody
//: should have to translate in their head. Shared, because the detail view
//: and the send confirmation both say it: the box a managing director
//: confirms must not describe the execution differently from the box the
//: person who entered it read.
kefiya.execution_sentence = function (row) {
	const day = row && row.execution_date
		? frappe.datetime.str_to_user(row.execution_date) : "";
	if (!row || !row.execution_date) return __("As soon as possible");

	// Mirrors requested_execution_date() on the server, which is the only
	// place that decides what date the bank is actually asked for. A day that
	// has arrived is no longer a date to wait for, and saying "held here until
	// then" about a day two weeks gone is the same lie in the other
	// direction: last time the screen was right and the message wrong;
	// without this the message is right and the screen wrong.
	const diff = frappe.datetime.get_diff(
		row.execution_date, frappe.datetime.get_today());

	if (row.manage_due_date) {
		// We hold it. On the day it is sent it is an ordinary immediate
		// transfer -- the date never reaches the bank.
		if (diff > 0) return __("On {0} — held here until then", [day]);
		if (diff === 0) return __("Due today — goes to the bank now");
		return __("Due since {0} — goes to the bank today", [day]);
	}
	// The bank holds it, so the date IS the order. The server refuses a date
	// that has passed rather than moving it quietly forward.
	if (diff > 0) return __("On {0} — the bank holds the order", [day]);
	if (diff === 0) return __("Today — the bank executes it today");
	return __("Dated {0}, which has passed — the bank cannot execute this",
		[day]);
};

//: How it travels: as an instant payment or as an ordinary SEPA transfer.
kefiya.transfer_kind = function (row) {
	return row && row.instant_payment
		? __("Instant payment — arrives within seconds")
		: __("SEPA transfer — one banking day");
};

kefiya.transfer_details_html = function (row, files) {
	const esc = frappe.utils.escape_html;
	const dmy = function (d) {
		return d ? frappe.datetime.str_to_user(d) : "";
	};
	const money = function (v) {
		return format_currency(v || 0);
	};

	// One field of the slip: what it is called, and what stands in it.
	const box = function (label, value, extra) {
		return "<div class='kef-f " + (extra || "") + "'>"
			+ "<div class='kef-fl'>" + label + "</div>"
			+ "<div class='kef-fv'>" + (value || "&nbsp;") + "</div></div>";
	};

	// --- how it will be executed -------------------------------------------
	// Written as a sentence, because "execution_date = null,
	// manage_due_date = 0" is not something anybody should translate in their
	// head. It says who holds the order, so no second row has to repeat it.
	const when = kefiya.execution_sentence(row);

	const state = kefiya.outbox_state
		? kefiya.outbox_state(row)
		: { label: row.status || "", fg: "", bg: "" };
	const stateHtml = "<span class='kef-badge' style='color:" + state.fg
		+ ";background:" + state.bg + "'>" + esc(state.label) + "</span>"
		+ (row.blocked
			? " <span class='text-muted'>" + esc(row.blocked) + "</span>" : "");

	// --- the payments -------------------------------------------------------
	// One slip per payment. A collective order is several transfers on one
	// message, and showing them as rows of a table hides that each has its own
	// recipient, its own IBAN and its own purpose.
	const payments = (row.items || []).map(function (it) {
		return "<div class='kef-slip'>"
			+ box(__("Recipient"), esc(it.recipient_name || "—"))
			+ "<div class='kef-two'>"
			+ box(__("IBAN"), "<span class='kef-mono'>"
				+ esc(kefiya.iban_pretty(it.recipient_iban)) + "</span>")
			+ box(__("Amount"), "<b>" + money(it.amount) + "</b>", "kef-r")
			+ "</div>"
			+ box(__("Reference"), esc(it.purpose || ""))
			+ "</div>";
	}).join("");

	// --- receipts -----------------------------------------------------------
	let receipts;
	if (files === null) {
		receipts = "<div class='text-muted'>"
			+ __("The attachments could not be read.") + "</div>";
	} else if (files === undefined) {
		receipts = "<div class='text-muted'>" + __("Loading …") + "</div>";
	} else if (!files.length) {
		const named = kefiya.transfer_referenced_documents(row)
			.map(function (entry) { return entry.name; });
		receipts = "<div class='text-muted'>"
			+ (named.length
				? __("No receipt is attached, neither here nor on {0}.",
					[named.join(", ")])
				: __("No receipt is attached to this order.")) + "</div>";
	} else {
		// Shown, not listed. A filename is not a receipt -- checking that the
		// invoice in front of you is the one being paid means looking at it,
		// and a link that opens a new tab is a step people skip.
		receipts = files.map(function (fl) {
			const size = fl.file_size
				? " <span class='text-muted small'>("
					+ Math.round(fl.file_size / 1024) + " KB)</span>" : "";
			// Where it hangs, when that is not this order: the receipt lives
			// on the document that produced it, and saying so is what makes
			// the connection checkable rather than magic.
			const from = fl.attached_to_name && fl.attached_to_name !== row.name
				? " <span class='text-muted small'>" + __("from {0} {1}",
					[esc(fl.attached_to_doctype || ""),
						esc(fl.attached_to_name)]) + "</span>"
				: "";
			return "<div class='kef-doc'>"
				+ "<div class='kef-doc-h'><a href='" + esc(fl.file_url)
				+ "' target='_blank'>" + esc(fl.file_name || fl.name)
				+ "</a>" + size + from + "</div>"
				+ kefiya.receipt_preview(fl) + "</div>";
		}).join("");
	}

	// The documents this order names, whether or not anything hangs on them.
	// This is the half that answers "change the connected data": the travel
	// expense, the invoice, the trip -- they are where the amounts come from,
	// and an order that only shows its own fields makes them unreachable.
	const named = kefiya.transfer_referenced_documents(row);
	let connected = "";
	if (named.length) {
		connected = "<div class='kef-h'>" + __("Connected documents")
			+ "</div><div class='kef-slip'>"
			+ named.map(function (entry) {
				const n = entry.name;
				// A name is not an address. Where the doctype is known -- the
				// server resolves it from the receipt that hangs on the
				// document -- this links straight to the form. Where it is
				// not, it says the name and stops: a link to /app/search is a
				// link to "could not find what you were looking for", and
				// that page teaches the reader nothing they did not know.
				if (!entry.doctype) {
					return "<div>" + esc(n)
						+ " <span class='text-muted small'>"
						+ __("not found in this system") + "</span></div>";
				}
				return "<div><a href='/app/"
					+ esc(frappe.router.slug(entry.doctype)) + "/"
					+ encodeURIComponent(n) + "'>" + esc(n) + "</a>"
					+ " <span class='text-muted small'>"
					+ esc(__(entry.doctype)) + "</span></div>";
			}).join("")
			+ "<div class='text-muted small' style='margin-top:6px'>"
			+ __("Named in the reference of this order. Amounts and receipts"
				+ " are changed there, not here.") + "</div></div>";
	}

	// --- why it cannot be changed -------------------------------------------
	// The "Correct" button is simply absent once an order is approved, and an
	// absent button explains nothing. Said out loud instead.
	let locked = "";
	if (row.docstatus === 1) {
		locked = "<div class='kef-note'>"
			+ (row.status === "Sent" || row.status === "Scheduled at Bank"
				? __("This order is with the bank. Nothing about it can be"
					+ " changed here any more.")
				: __("Approved orders are locked: amounts and recipients"
					+ " stand. To change something, cancel the order and enter"
					+ " a new one."))
			+ "</div>";
	}

	return "<div class='kef-form'>"
		+ "<div class='kef-h'>" + __("Ordering party") + "</div>"
		+ "<div class='kef-slip'>"
		+ box(__("Paying account"), esc(row.bank_account || ""))
		+ "<div class='kef-two'>"
		+ box(__("Company"), esc(row.company || ""))
		+ box(__("Order state"), stateHtml)
		+ "</div></div>"

		+ "<div class='kef-h'>" + __("Payments") + "</div>" + payments
		+ ((row.items || []).length > 1
			? "<div class='kef-total'>" + __("Total") + ": <b>"
				+ money(row.total_amount) + "</b></div>" : "")

		+ "<div class='kef-h'>" + __("Execution") + "</div>"
		+ "<div class='kef-slip'><div class='kef-two'>"
		+ box(__("When"), esc(when))
		+ box(__("Transfer type"), esc(kefiya.transfer_kind(row)))
		+ "</div>"
		+ (row.vop_pending
			? box(__("Payee verification"), __("still open")) : "")
		+ (row.bank_reference
			? box(__("Bank response"), esc(row.bank_reference)) : "")
		+ "</div>"
		+ locked

		+ "<div class='kef-h'>" + __("Receipts") + "</div>" + receipts
		+ connected
		+ "<div class='kef-order'>" + __("Order") + " "
		+ "<a href='/app/kefiya-transfer/" + encodeURIComponent(row.name)
		+ "'>" + esc(row.name) + "</a></div>"
		+ "</div>"

		+ "<style>"
		+ ".kef-form .kef-h{margin:16px 0 6px;font-weight:600;font-size:12px;"
		+ "text-transform:uppercase;letter-spacing:.04em;"
		+ "color:var(--text-muted)}"
		+ ".kef-form .kef-h:first-child{margin-top:0}"
		+ ".kef-slip{border:1px solid var(--border-color);border-radius:6px;"
		+ "padding:10px;margin-bottom:8px;background:var(--card-bg)}"
		+ ".kef-two{display:flex;gap:10px}.kef-two>.kef-f{flex:1}"
		+ ".kef-f{margin-bottom:8px}.kef-f:last-child{margin-bottom:0}"
		+ ".kef-fl{font-size:11px;color:var(--text-muted);margin-bottom:2px}"
		+ ".kef-fv{border-bottom:1px solid var(--border-color);"
		+ "padding-bottom:3px;min-height:20px;word-break:break-word}"
		+ ".kef-f.kef-r .kef-fv{text-align:right;white-space:nowrap}"
		+ ".kef-mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}"
		+ ".kef-badge{display:inline-block;padding:2px 8px;border-radius:9px;"
		+ "font-size:11px;font-weight:600}"
		+ ".kef-total{text-align:right;padding:2px 4px 6px}"
		+ ".kef-note{background:var(--fg-hover-color);border-radius:6px;"
		+ "padding:8px 10px;font-size:12px;color:var(--text-muted)}"
		+ ".kef-doc{border:1px solid var(--border-color);border-radius:6px;"
		+ "margin-bottom:8px;overflow:hidden;background:var(--card-bg)}"
		+ ".kef-doc-h{padding:6px 8px;font-size:12px;"
		+ "border-bottom:1px solid var(--border-color)}"
		+ ".kef-doc img{display:block;max-width:100%;height:auto}"
		+ ".kef-doc embed{display:block;width:100%;height:420px;border:0}"
		+ ".kef-doc .kef-noprev{padding:8px;font-size:11px;"
		+ "color:var(--text-muted)}"
		+ ".kef-order{margin-top:14px;font-size:11px;color:var(--text-muted)}"
		+ "</style>";
};
