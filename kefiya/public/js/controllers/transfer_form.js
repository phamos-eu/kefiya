// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

// Entering an outgoing transfer, laid out the way a transfer form is.
//
// The dialog this replaces asked for account, recipient, IBAN, amount, purpose
// and a bare date field -- and that was all. The document has carried three
// more decisions the whole time and none of them could be made here:
//
//   execution_date    when it should happen
//   manage_due_date   who holds it until then, we or the bank
//   instant_payment   whether it goes as a SEPA instant payment
//
// A date field alone cannot say "as soon as possible" versus "on the 24th",
// and it certainly cannot say who keeps the order in the meantime. So the
// execution is asked as what it is: a choice, then its details.
//
// The recipient is a free field. It suggests known payees, but it does not
// require one -- the whole point of this document is a transfer that has no
// invoice behind it. The IBAN carries the safeguard instead: the document
// checks its checksum before anything is stored.

frappe.provide("kefiya");

//: How an order is executed. Kept as data because three places read it: the
//: form builds the choice from it, the dialog decides which fields to show,
//: and the detail view says it back in the same words.
kefiya.EXECUTION_MODES = [
	{
		value: "asap",
		label: __("As soon as possible"),
		hint: __("The order goes out with the next send."),
	},
	{
		value: "bank",
		label: __("On a date — the bank holds the order"),
		hint: __("Sent now, executed by the bank on the day. The bank must"
			+ " support dated transfers for this account."),
	},
	{
		value: "here",
		label: __("On a date — held here until then"),
		hint: __("Stays in the outbox and is offered for sending on the day."
			+ " Use this where the bank takes no dated order."),
	},
];

//: Instant payment is not one of the three modes -- it is a property of an
//: order that goes out now -- but it is explained in the same place, so the
//: two forms cannot describe it differently.
kefiya.EXECUTION_MODES.instant_hint = __("Arrives within seconds. The bank"
	+ " must offer it for this account, and it is usually capped.");

//: The one wording for an execution option, wherever it is offered. The
//: document form and the outbox dialog ask the same question in different
//: widgets; describing it in two places is how they drift apart.
kefiya.execution_hint = function (mode) {
	if (mode === "instant") return kefiya.EXECUTION_MODES.instant_hint;
	const found = kefiya.EXECUTION_MODES.find(function (m) {
		return m.value === mode;
	});
	return found ? found.hint : "";
};

kefiya.execution_mode_of = function (row) {
	if (!row || !row.execution_date) return "asap";
	return row.manage_due_date ? "here" : "bank";
};

// One dialog for entering and for correcting. They ask for the same things,
// and two dialogs would have drifted apart the first time one of them changed.
kefiya.transfer_form = function (options) {
	options = options || {};
	const payers = options.payers || [];
	const existing = options.row || null;
	const isEdit = !!existing;

	if (!payers.length && !isEdit) {
		frappe.msgprint({
			title: __("No account available"),
			indicator: "orange",
			message: __("No bank access is set up that a transfer can start"
				+ " from. A loan, a guarantee or a credit card cannot send"
				+ " money."),
		});
		return;
	}

	const payerLabels = payers.map(function (p) {
		return p.bank_account + (p.company ? " — " + p.company : "");
	});
	const payerAt = function (label) {
		return payers[payerLabels.indexOf(label)] || null;
	};
	const item = (existing && (existing.items || [])[0]) || {};

	const fields = [
		{
			fieldtype: "Select", fieldname: "payer", reqd: 1,
			label: __("Paying account"),
			options: payerLabels.join("\n"),
			default: payerLabels[0],
			read_only: isEdit ? 1 : 0,
			description: isEdit
				? __("The paying account cannot be changed on an existing"
					+ " order. Delete it and enter a new one instead.")
				: "",
		},
		{ fieldtype: "HTML", fieldname: "standing" },
		{ fieldtype: "Section Break", label: __("Recipient") },
		{
			// Autocomplete, not Data with a <datalist> hung on it: Frappe
			// sets autocomplete="off" on its inputs, and Chrome then shows
			// no datalist at all. The list was fetched, attached, and never
			// seen.
			//
			// ignore_validation is not optional. The control's validate()
			// answers "" for any value not in its list once the list has
			// entries -- the very refusal the old comment feared, and the
			// one flag that switches it off. A transfer with no invoice
			// behind it is what this document exists for.
			fieldtype: "Autocomplete", fieldname: "recipient_name", reqd: 1,
			ignore_validation: 1,
			label: __("Recipient"), default: item.recipient_name || "",
			description: __("Free text. Known payees are suggested while you"
				+ " type, but any name may be entered."),
		},
		{ fieldtype: "Column Break" },
		{
			fieldtype: "Autocomplete", fieldname: "recipient_iban", reqd: 1,
			ignore_validation: 1,
			label: __("IBAN"), default: item.recipient_iban || "",
			description: __("Checked against its checksum before it is stored."),
		},
		{ fieldtype: "HTML", fieldname: "known_ibans" },
		{ fieldtype: "HTML", fieldname: "payee_verdict" },
		{ fieldtype: "Section Break" },
		{
			fieldtype: "Currency", fieldname: "amount", reqd: 1,
			label: __("Amount"), default: item.amount || null,
		},
		{ fieldtype: "Column Break" },
		{
			fieldtype: "Small Text", fieldname: "purpose",
			label: __("Reference"), default: item.purpose || "",
		},
		{ fieldtype: "Section Break", label: __("Execution") },
		{
			fieldtype: "Select", fieldname: "execution_mode", reqd: 1,
			label: __("When"),
			options: kefiya.EXECUTION_MODES.map(function (m) {
				return m.label;
			}).join("\n"),
			default: (kefiya.EXECUTION_MODES.find(function (m) {
				return m.value === kefiya.execution_mode_of(existing);
			}) || kefiya.EXECUTION_MODES[0]).label,
		},
		{
			fieldtype: "Date", fieldname: "execution_date",
			label: __("Execution date"),
			default: (existing && existing.execution_date) || null,
			depends_on: "eval:doc.execution_mode && doc.execution_mode.indexOf('"
				+ __("As soon as possible") + "') !== 0",
			mandatory_depends_on: "eval:doc.execution_mode && doc.execution_mode"
				+ ".indexOf('" + __("As soon as possible") + "') !== 0",
		},
		{ fieldtype: "Column Break" },
		{
			fieldtype: "Check", fieldname: "instant_payment",
			label: __("Instant payment"),
			// On unless this order already says otherwise. Money that arrives
			// in seconds is what people expect of a transfer now; the reasons
			// not to are the exceptions, and each of them turns it off with a
			// reason of its own.
			default: existing ? (existing.instant_payment ? 1 : 0) : 1,
			description: kefiya.execution_hint("instant"),
		},
		{ fieldtype: "Section Break" },
		{ fieldtype: "HTML", fieldname: "hint" },
	];

	const dialog = new frappe.ui.Dialog({
		title: isEdit ? __("Correct transfer") : __("New transfer"),
		size: "large",
		fields: fields,
		primary_action_label: isEdit ? __("Save") : __("Enter"),
		primary_action: function (values) {
			kefiya.transfer_form_submit(dialog, values, payers, existing,
				options.onSaved);
		},
	});

	// The chosen mode explains itself, so nobody has to know what "the bank
	// holds the order" means for an account whose bank takes no dated orders.
	const showHint = function () {
		const label = dialog.get_value("execution_mode");
		const mode = kefiya.EXECUTION_MODES.find(function (m) {
			return m.label === label;
		}) || kefiya.EXECUTION_MODES[0];
		// A mode the bank refuses says so here, where the choice was made,
		// rather than at the bank after a TAN has been spent on it.
		// A bank-held date and an instant payment cannot go together: the
		// order would go out now carrying a future date, and no bank offers
		// that. Held here they go together perfectly -- the order is sent ON
		// the day, and that moment is an ordinary immediate transfer.
		const bank_holds_it = mode.value === "bank";
		dialog.set_df_property("instant_payment", "read_only",
			bank_holds_it || dialog.instant_refused ? 1 : 0);
		if (bank_holds_it && dialog.get_value("instant_payment")) {
			dialog.set_value("instant_payment", 0);
		}
		if (bank_holds_it) {
			dialog.set_df_property("instant_payment", "description",
				__("The bank cannot hold an instant payment until a future"
					+ " date. Choose \"held here\" to send it as an instant"
					+ " payment on the day."));
		} else if (!dialog.instant_refused) {
			dialog.set_df_property("instant_payment", "description",
				kefiya.execution_hint("instant"));
		}

		const refused = dialog.refused_modes && dialog.refused_modes[label];
		dialog.fields_dict.hint.$wrapper.html(
			"<div class='text-muted small'>"
			+ frappe.utils.escape_html(mode.hint) + "</div>"
			+ (refused
				? "<div class='small' style='color:var(--red-500,#c0392b);"
					+ "margin-top:4px'>"
					+ frappe.utils.escape_html(refused) + "</div>"
				: ""));
	};
	dialog.fields_dict.execution_mode.df.onchange = showHint;

	// What is on the account, and what the bank lets it go below. Shown where
	// the account is chosen, because that is the moment it decides anything --
	// a balance on another page is a balance nobody looks up.
	const showStanding = function () {
		const p = payerAt(dialog.get_value("payer"));
		dialog.fields_dict.standing.$wrapper.html(
			kefiya.account_standing_html(p));
		applyCapabilities(p);
	};
	dialog.fields_dict.payer.df.onchange = showStanding;

	// What the bank allows on the chosen account, asked here too.
	//
	// The document form has asked this all along; this dialog did not, so it
	// offered a dated order to an account whose bank takes none and an instant
	// payment to one that does not do them -- and the refusal arrived from the
	// bank, after a TAN had been spent on it.
	//
	// Refused options are marked, not removed. A vanished option teaches
	// nobody anything; one that says why is an answer.
	const applyCapabilities = function (payer) {
		dialog.refused_modes = {};
		if (!payer || !payer.login || !kefiya.capabilities) return;

		kefiya.capabilities.load(payer.login).then(function (info) {
			kefiya.EXECUTION_MODES.forEach(function (m) {
				// Only the bank-held date needs anything from the bank. "As
				// soon as possible" is an ordinary transfer, and holding the
				// date here is our own doing -- neither can be refused.
				if (m.value !== "bank") return;
				const wanted = kefiya.capabilities.required(1, true, false);
				dialog.refused_modes[m.label] =
					kefiya.capabilities.refuses(info, wanted)
						? kefiya.capabilities.refusal_reason(wanted, 1) : null;
			});
			showHint();

			const instant = kefiya.capabilities.required(1, false, true);
			const instant_refused = kefiya.capabilities.refuses(info, instant);
			dialog.instant_refused = instant_refused
				? kefiya.capabilities.refusal_reason(instant, 1) : null;
			dialog.set_df_property("instant_payment", "read_only",
				instant_refused ? 1 : 0);
			dialog.set_df_property("instant_payment", "description",
				instant_refused ? dialog.instant_refused
					: kefiya.execution_hint("instant"));
			if (instant_refused) dialog.set_value("instant_payment", 0);
		});
	};

	// Asked as soon as both halves of the recipient are there. Not on submit:
	// the answer is meant to change what gets typed, and after the save it is
	// only a reproach.
	let asked = 0;
	const checkPayee = function () {
		const iban = kefiya.iban_plain(dialog.get_value("recipient_iban"));
		const who = dialog.get_value("recipient_name");
		if (!iban || iban.length < 15) {
			dialog.payee_answer = null;
			dialog.fields_dict.payee_verdict.$wrapper.html("");
			return;
		}
		// Which question this answer belongs to. Two checks can be in flight
		// -- the name changes, then the IBAN is filled from it -- and the
		// slower one must not paint its verdict over the newer one.
		const mine = ++asked;
		kefiya.payee_check(who, iban).then(function (answer) {
			if (mine !== asked) return;
			dialog.payee_answer = answer;
			dialog.fields_dict.payee_verdict.$wrapper.html(
				kefiya.payee_check_html(answer));
		});
	};
	dialog.fields_dict.recipient_iban.df.onchange = checkPayee;

	// Suggestions without a requirement: the name stays free text, because a
	// transfer with no invoice behind it is what this document is for. Picking
	// a known payee offers the IBANs we have paid them at -- which is the half
	// that saves the typing, and the half that was never there.
	dialog.fields_dict.recipient_name.df.onchange = function () {
		// When the pick fills the IBAN, set_value has already fired that
		// field's onchange -- which IS checkPayee. Asking again here would be
		// a second round trip for one click.
		if (!offerTheirIbans(dialog.get_value("recipient_name"))) checkPayee();
	};

	// Their IBANs, offered but never forced: an IBAN already typed stands,
	// because it is the one taken off the invoice in front of the person.
	//
	// Where there are several, they are offered as buttons rather than by
	// opening a dropdown. A dropdown on an empty field shows nothing -- the
	// browser has no prefix to filter by -- so "we know two IBANs for them"
	// would have been said by opening an empty box. 32 payees here have more
	// than one.
	let payeeList = [];
	let fillIbans = null;
	//: :return: true when it filled the IBAN itself -- the caller then leaves
	//: the payee check to that field's own change handler.
	const offerTheirIbans = function (name) {
		const payee = kefiya.payee_named(payeeList, name);
		const ibans = (payee && payee.ibans) || [];
		const box = dialog.fields_dict.known_ibans.$wrapper;
		box.empty();
		if (!ibans.length) return false;

		if (fillIbans) {
			fillIbans(ibans.map(function (i) {
				return { label: kefiya.iban_pretty(i.iban), value: i.iban };
			}));
		}

		if (kefiya.iban_plain(dialog.get_value("recipient_iban"))) return false;
		if (ibans.length === 1) {
			dialog.set_value("recipient_iban", ibans[0].iban);
			return true;
		}

		box.html("<div class='text-muted small' style='margin-bottom:4px'>"
			+ __("We have paid this payee at these IBANs:") + "</div>"
			+ ibans.map(function (i) {
				return "<button type='button' class='btn btn-xs btn-default'"
					+ " style='margin:0 4px 4px 0;font-family:ui-monospace,"
					+ "Menlo,monospace' data-iban='"
					+ frappe.utils.escape_html(i.iban) + "'>"
					+ frappe.utils.escape_html(kefiya.iban_pretty(i.iban))
					+ "</button>";
			}).join(""));
		box.find("button").on("click", function () {
			dialog.set_value("recipient_iban", $(this).attr("data-iban"));
			box.empty();
		});
		return false;
	};

	kefiya.known_payees().then(function (payees) {
		if (!payees.length || !dialog.$wrapper) return;
		payeeList = payees;

		// set_data is the Autocomplete control's own list. The IBAN field
		// gets its list per payee, from offerTheirIbans.
		dialog.fields_dict.recipient_name.set_data(
			payees.map(function (p) { return p.name; }));
		fillIbans = function (values) {
			dialog.fields_dict.recipient_iban.set_data(values || []);
		};

		// A name already in the field -- correcting an order -- gets its
		// IBAN list too, without touching what is stored.
		const current = dialog.get_value("recipient_name");
		if (current) offerTheirIbans(current);
	});

	dialog.show();
	showHint();
	showStanding();
	return dialog;
};

// The account's standing, in one line: what is there, what may be gone below,
// and the sum of the two -- which is the number the person entering an order
// is actually asking about.
//
// The date is part of it. These figures are written by a fetch, so their age
// is the difference between a fact and a guess. An account nobody has fetched
// says so rather than showing a confident zero.
kefiya.account_standing_html = function (payer) {
	if (!payer) return "";
	const esc = frappe.utils.escape_html;
	const money = function (v) {
		return format_currency(v || 0, payer.currency || undefined);
	};

	if (payer.balance === null || payer.balance === undefined) {
		return "<div class='text-muted small'>"
			+ __("No balance has been fetched for this account yet.")
			+ "</div>";
	}

	const parts = [__("Balance {0}", [money(payer.balance)])];
	if (payer.credit_line) {
		parts.push(__("overdraft {0}", [money(payer.credit_line)]));
		parts.push("<b>" + __("available {0}", [money(payer.available)])
			+ "</b>");
	}
	let line = "<div class='small'>" + parts.join(" · ") + "</div>";
	if (payer.as_of) {
		line += "<div class='text-muted small'>"
			+ __("as at {0}", [esc(frappe.datetime.str_to_user(payer.as_of))])
			+ "</div>";
	}
	return line;
};

kefiya.transfer_form_submit = function (dialog, values, payers, existing, onSaved) {
	const mode = kefiya.EXECUTION_MODES.find(function (m) {
		return m.label === values.execution_mode;
	}) || kefiya.EXECUTION_MODES[0];

	// The bank's own answer, checked before the order is written rather than
	// after a TAN was spent on it.
	const refusal = dialog.refused_modes && dialog.refused_modes[mode.label];
	if (refusal) {
		frappe.msgprint({
			title: __("Not offered on this account"),
			indicator: "orange", message: refusal,
		});
		return;
	}
	if (values.instant_payment && dialog.instant_refused) {
		frappe.msgprint({
			title: __("Not offered on this account"),
			indicator: "orange", message: dialog.instant_refused,
		});
		return;
	}

	if (mode.value !== "asap" && !values.execution_date) {
		frappe.msgprint({
			title: __("Date missing"),
			indicator: "orange",
			message: __("A dated transfer needs a date."),
		});
		return;
	}

	const payerIndex = payers.map(function (p) {
		return p.bank_account + (p.company ? " — " + p.company : "");
	}).indexOf(values.payer);

	const doc = {
		execution_date: mode.value === "asap" ? null : values.execution_date,
		manage_due_date: mode.value === "here" ? 1 : 0,
		instant_payment: values.instant_payment ? 1 : 0,
	};

	const item = {
		doctype: "Kefiya Transfer Item",
		recipient_name: (values.recipient_name || "").trim(),
		recipient_iban: (values.recipient_iban || "").replace(/\s+/g, "")
			.toUpperCase(),
		amount: values.amount,
		purpose: (values.purpose || "").trim(),
	};

	dialog.disable_primary_action();
	let call;
	if (existing) {
		// The paying account stays as it was: it is read-only above, and
		// changing it would make this a different order.
		call = frappe.call({
			method: "frappe.client.set_value",
			args: {
				doctype: "Kefiya Transfer", name: existing.name,
				fieldname: Object.assign({}, doc, { items: [item] }),
			},
		});
	} else {
		if (payerIndex < 0) {
			dialog.enable_primary_action();
			frappe.msgprint(__("Please choose a paying account."));
			return;
		}
		call = frappe.call({
			method: "frappe.client.insert",
			args: {
				doc: Object.assign({
					doctype: "Kefiya Transfer",
					kefiya_login: payers[payerIndex].login,
					items: [item],
				}, doc),
			},
		});
	}

	call.then(function () {
		dialog.hide();
		frappe.show_alert({
			message: existing ? __("Transfer saved.") : __("Transfer entered."),
			indicator: "green",
		}, 4);
		if (onSaved) onSaved();
	}).catch(function (r) {
		dialog.enable_primary_action();
		// The document's own validation is the safeguard here -- the IBAN
		// checksum above all -- so its message is what the user needs to see,
		// not a generic failure.
		frappe.msgprint({
			title: __("Not saved"),
			indicator: "red",
			message: kefiya.server_message(r)
				|| __("The transfer could not be saved."),
		});
	});
};

kefiya.server_message = function (r) {
	try {
		return JSON.parse((r && r._server_messages) || "[]").map(function (m) {
			try { return JSON.parse(m).message; } catch (e) { return m; }
		}).join(" ");
	} catch (e) {
		return "";
	}
};
