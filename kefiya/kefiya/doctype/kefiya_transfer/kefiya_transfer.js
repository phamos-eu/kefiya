// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

{% include "kefiya/public/js/controllers/fints_progress_log.js" %}
{% include "kefiya/public/js/controllers/fints_interactive.js" %}
{% include "kefiya/public/js/controllers/fints_transfer_flow.js" %}

frappe.ui.form.on("Kefiya Transfer", {
	onload: function (frm) {
		kefiya.interactive.progressbar(frm);
		// An IBAN printed in one block is not checkable against a letter from
		// the bank; the eye slides off twenty-two characters. In fours it is.
		// Display only -- what is stored stays unbroken, because a space
		// inside an IBAN is a rejected order.
		const iban_column = frm.fields_dict.items
			&& frm.fields_dict.items.grid
			&& frm.fields_dict.items.grid.get_docfield("recipient_iban");
		if (iban_column) {
			iban_column.formatter = function (value) {
				return frappe.utils.escape_html(kefiya.iban_pretty(value));
			};
		}

		// The company comes first on the form and narrows what follows: with
		// one chosen, only its own accounts are offered. Without one, all of
		// them are -- picking the account is the other way round, and it sets
		// the company itself.
		frm.set_query("kefiya_login", function () {
			return frm.doc.company
				? { filters: { company: frm.doc.company } }
				: {};
		});

		// Only the company's own accounts are offered, and never the one the
		// money is drawn from: a transfer from an account to itself is not a
		// transfer, and the bank only says so after a TAN was spent on it.
		frm.set_query("own_account", "items", function () {
			return {
				query: "kefiya.utils.own_transfer.own_account_query",
				filters: {
					kefiya_login: frm.doc.kefiya_login,
					company: frm.doc.company,
				},
			};
		});
	},

	kefiya_login: function (frm) {
		kefiya_apply_capabilities(frm);
		kefiya_describe_paying_account(frm);
	},

	// The two fields point at each other, and the account is the stronger of
	// the two: it holds the money. So changing the company only ever removes
	// an account that no longer belongs to it -- it never rewrites one.
	//
	// Said out loud rather than done quietly: an account disappearing from a
	// money form without a word is how somebody sends the next order from
	// whatever happened to be selected afterwards.
	company: function (frm) {
		if (!frm.doc.company || !frm.doc.kefiya_login) return;
		frappe.db.get_value("Kefiya Login", frm.doc.kefiya_login, "company")
			.then(function (r) {
				const owner = (r && r.message && r.message.company) || null;
				if (!owner || owner === frm.doc.company) return;
				const dropped = frm.doc.kefiya_login;
				frm.set_value("kefiya_login", null);
				frappe.show_alert({
					message: __("{0} belongs to {1}, not to {2} — the paying"
						+ " account was cleared.",
						[dropped, owner, frm.doc.company]),
					indicator: "orange",
				}, 8);
			});
	},

	instant_payment: function (frm) {
		kefiya_apply_capabilities(frm);
	},

	manage_due_date: function (frm) {
		kefiya_apply_capabilities(frm);
		kefiya_reconcile_instant_with_the_date(frm);
	},

	execution_date: function (frm) {
		kefiya_reconcile_instant_with_the_date(frm);
	},

	refresh: function (frm) {
		// What the bank allows on this account decides which of the options
		// below are offered at all. Applied asynchronously: the answer comes
		// from the server, and the form must not wait for it to render.
		kefiya_apply_capabilities(frm);
		kefiya_describe_paying_account(frm);

		// Sending is deliberately separate from submitting: approving a
		// transfer must never move money as a side effect.
		if (frm.doc.docstatus === 1 && frm.doc.status !== "Sent") {
			if (!frm.doc.on_hold) {
				frm.add_custom_button(__("Send to bank"), function () {
					kefiya_confirm_and_send(frm);
				}).addClass("btn-primary");
			}

			// Holding back is allowed after approval because it changes only
			// when the order goes out, not what it says.
			frm.add_custom_button(
				frm.doc.on_hold ? __("Release") : __("Hold back"),
				function () {
					frm.call("set_hold", { on_hold: frm.doc.on_hold ? 0 : 1 })
						.then(() => frm.reload_doc());
				}
			);
		}

		if (frm.doc.on_hold && frm.doc.status !== "Sent") {
			frm.dashboard.set_headline_alert(
				__("Held back — this order stays in the outbox and is skipped by a collective send."),
				"orange"
			);
		}

		if (frm.doc.status === "Sent") {
			frm.dashboard.set_headline_alert(
				__("Sent to the bank. Recall it with your bank if needed — it cannot be withdrawn here."),
				"green"
			);
		} else if (frm.doc.vop_pending) {
			// Not "release it": the parked challenge belongs to the request
			// it was raised in. Sending again is what brings the check back,
			// and it is safe -- the bank did not release the order, so
			// nothing was debited.
			frm.dashboard.set_headline_alert(
				__("The bank is holding this order until the payee check is confirmed. Nothing has been sent. Send it to the bank again — the check then comes back for review and release."),
				"orange"
			);
		}

		kefiya_render_summary(frm);
	},

	items_on_form_rendered: function (frm) {
		kefiya_render_summary(frm);
	},
});

frappe.ui.form.on("Kefiya Transfer Item", {
	amount: function (frm) {
		kefiya_recalculate(frm);
	},
	own_account: function (frm, cdt, cdn) {
		// A Kontouebertrag: the recipient is one of our own accounts. Name and
		// IBAN come from there so nobody types an IBAN they already own --
		// that is where transposed digits come from, and a transposed digit
		// pays a stranger just as reliably here as anywhere else.
		const row = locals[cdt][cdn];
		if (!row.own_account) {
			return;
		}
		frappe.db.get_value("Bank Account", row.own_account,
			["account_name", "iban"]).then(function (r) {
			const a = (r && r.message) || {};
			if (a.iban) {
				frappe.model.set_value(cdt, cdn, "recipient_iban",
					a.iban.replace(/[\s-]/g, "").toUpperCase());
			}
			if (a.account_name && !row.recipient_name) {
				frappe.model.set_value(cdt, cdn, "recipient_name",
					a.account_name);
			}
		});
	},
	items_remove: function (frm) {
		kefiya_recalculate(frm);
	},
	recipient_iban: function (frm, cdt, cdn) {
		// Immediate feedback on a mistyped IBAN. With free recipient entry
		// there is no invoice to check against, so the checksum is the only
		// automatic defence -- and catching it here beats a server error.
		const row = locals[cdt][cdn];
		if (!row.recipient_iban) {
			return;
		}
		const iban = row.recipient_iban.replace(/[\s-]/g, "").toUpperCase();
		frappe.model.set_value(cdt, cdn, "recipient_iban", iban);
		if (!kefiya_iban_is_valid(iban)) {
			frappe.msgprint({
				title: __("Invalid IBAN"),
				indicator: "red",
				message: __("{0} is not a valid IBAN — the checksum does not match. Please re-check the recipient's account.", [frappe.utils.escape_html(iban)]),
			});
		}
	},
});

/**
 * Say which account the money leaves, in full, under the field that names it.
 *
 * The name of an access is not enough to tell two accounts of one bank apart,
 * and it says nothing at all about whose money it is. A colleague preparing a
 * transfer saw "Brilu KG Mietkonto" above "axessio Hausverwaltung GmbH" and
 * asked whether that could be right. It could not -- and the form had shown
 * both without a word about the contradiction.
 *
 * The company is now taken from the account and cannot diverge. This puts the
 * evidence for it on the screen: the IBAN in fours, and the company it
 * belongs to.
 */
function kefiya_describe_paying_account(frm) {
	if (!frm.doc.kefiya_login) {
		frm.set_df_property("kefiya_login", "description",
			__("The account the money is paid from."));
		return;
	}
	frappe.db.get_value("Kefiya Login", frm.doc.kefiya_login,
		["account_iban", "bank_account", "company"]).then(function (r) {
		const info = (r && r.message) || {};
		const parts = [];
		if (info.account_iban) parts.push(kefiya.iban_pretty(info.account_iban));
		if (info.company) {
			parts.push(info.company);
			// The account decides whose money this is. Picking one is the
			// answer to the company question, not a second question.
			if (frm.doc.docstatus === 0 && frm.doc.company !== info.company) {
				frm.set_value("company", info.company);
			}
		}
		frm.set_df_property("kefiya_login", "description",
			parts.length
				? __("The account the money is paid from.") + " · "
					+ parts.join(" · ")
				: __("The account the money is paid from."));
		frm.refresh_field("kefiya_login");

		if (info.bank_account) {
			kefiya_show_account_standing(frm, info.bank_account);
		}
	});
}

/**
 * What is on the account and what the bank lets it go below.
 *
 * The overdraft line is as much a part of the answer as the balance and is the
 * half people forget: 664.028,54 EUR on an account with a line of 250.000,00
 * means 914.028,54 can leave it. Both are stated, and so is the sum.
 *
 * With the date, because these figures come from a fetch and their age is the
 * difference between a fact and a guess.
 */
function kefiya_show_account_standing(frm, bank_account) {
	// account_kind.account_standing, not a query of our own. This asked
	// "Bank Account" for account_currency -- a field it does not have; a Bank
	// Account names the ledger Account it books to and the currency belongs
	// to that. The server refuses a field that is not on the doctype, so
	// opening any transfer produced
	//
	//     Feld in der Abfrage nicht erlaubt: account_currency
	//
	// before the form had finished loading. The same figures already have one
	// reader on the server, which resolves the currency properly and applies
	// the "a zero nobody fetched is not a balance" rule; a second reader here
	// was a second thing to keep right, and it was not right.
	frappe.call({
		method: "kefiya.utils.account_kind.account_standing_for",
		args: { bank_account: bank_account },
	}).then(function (r) {
		const a = (r && r.message) || {};
		const balance = a.balance;
		if (balance === null || balance === undefined) {
			frm.dashboard.clear_headline();
			return;
		}
		const line = a.credit_line || 0;
		const money = function (v) {
			return format_currency(v || 0, a.currency || undefined);
		};
		let text = __("Balance {0}", [money(balance)]);
		if (line) {
			text += " · " + __("overdraft {0}", [money(line)])
				+ " · " + __("available {0}", [money(balance + line)]);
		}
		if (a.as_of) {
			text += " · " + __("as at {0}",
				[frappe.datetime.str_to_user(a.as_of)]);
		}
		frm.dashboard.add_comment(text, balance < 0 ? "orange" : "blue", true);
	});
}

/**
 * Instant payment and a date only clash when the BANK holds the date.
 *
 * Then the order goes out now carrying a future execution date, and no bank
 * offers an instant payment that way. Held here it is no clash at all: the
 * order waits in the outbox and is sent ON the day, as an ordinary immediate
 * transfer that may well be an instant one.
 *
 * Instant is on by default, so this is the case that would otherwise greet the
 * user with a validation error at save. It is turned off here instead, at the
 * moment the incompatible choice is made, and with the reason -- rather than
 * quietly, which would leave somebody wondering later why the payment took a
 * day.
 */
function kefiya_reconcile_instant_with_the_date(frm) {
	if (frm.doc.docstatus !== 0 || !frm.doc.instant_payment) return;
	if (!frm.doc.execution_date || frm.doc.manage_due_date) return;
	if (frappe.datetime.get_diff(frm.doc.execution_date,
		frappe.datetime.get_today()) <= 0) return;

	frm.set_value("instant_payment", 0);
	frappe.show_alert({
		message: __("The bank cannot hold an instant payment until a future"
			+ " date, so it was switched off. Tick \"keep the date here\" to"
			+ " send it as an instant payment on the day."),
		indicator: "orange",
	}, 10);
}

/** ISO 7064 mod-97 check, mirroring the server-side validation. */
function kefiya_iban_is_valid(iban) {
	if (!iban || iban.length < 15 || iban.length > 34) {
		return false;
	}
	if (!/^[A-Z]{2}[0-9]{2}[A-Z0-9]+$/.test(iban)) {
		return false;
	}
	const rearranged = iban.slice(4) + iban.slice(0, 4);
	let digits = "";
	for (const ch of rearranged) {
		digits += /[0-9]/.test(ch) ? ch : (ch.charCodeAt(0) - 55).toString();
	}
	// The number exceeds Number.MAX_SAFE_INTEGER, so reduce piecewise.
	let remainder = 0;
	for (const ch of digits) {
		remainder = (remainder * 10 + parseInt(ch, 10)) % 97;
	}
	return remainder === 1;
}

function kefiya_recalculate(frm) {
	let total = 0;
	(frm.doc.items || []).forEach((row) => {
		total += flt(row.amount);
	});
	frm.set_value("total_amount", total);
	frm.set_value("payment_count", (frm.doc.items || []).length);
	kefiya_render_summary(frm);
	// One payment goes out as HKCCS, several as the collective HKCCM -- a
	// different business transaction, which a bank may allow on this account
	// or not. So the answer changes with the number of rows.
	kefiya_apply_capabilities(frm);
}

/**
 * Offer only what the bank allows on this account.
 *
 * Hides what was REFUSED and leaves everything else alone. An account that has
 * never been fetched says "unknown" to every question, and unknown is not a
 * reason to take a button away -- see account_capabilities.js.
 */
function kefiya_apply_capabilities(frm) {
	if (!frm.doc.kefiya_login) {
		return;
	}
	kefiya.capabilities.load(frm.doc.kefiya_login).then(function (info) {
		if (!info || !info.capabilities) {
			return;
		}

		const count = (frm.doc.items || []).length || 1;

		// An instant payment is its own transaction (HKIPZ/HKIPM). Where the
		// bank does not offer it here, the tick box is not just useless, it
		// is a trap: it changes what is sent and the order fails at the bank.
		const instant = kefiya.capabilities.required(count, false, true);
		const refused = kefiya.capabilities.refuses(info, instant);
		// Shown and disabled, not hidden. A box that vanishes teaches nobody
		// anything -- the reader is left wondering whether the option exists
		// at all, whether they lost a right, or whether the form is broken.
		// Standing there greyed out with its reason, it answers all three.
		frm.set_df_property("instant_payment", "read_only", refused ? 1 : 0);
		frm.set_df_property("instant_payment", "description", refused
			? kefiya.capabilities.refusal_reason(instant, count)
			: kefiya.execution_hint("instant"));
		if (refused && frm.doc.instant_payment && frm.doc.docstatus === 0) {
			frm.set_value("instant_payment", 0);
		}

		// Letting the bank hold the date is HKCSE/HKCME. Where it is refused,
		// the date can still be managed here -- that is our own doing and
		// needs nothing from the bank -- so the field stays and only the
		// choice to hand the date over goes.
		const dated = kefiya.capabilities.required(count, true, false);
		const dated_refused = kefiya.capabilities.refuses(info, dated);
		frm.set_df_property("manage_due_date", "description", dated_refused
			? kefiya.capabilities.refusal_reason(dated, count) + " "
				+ kefiya.execution_hint("here")
			: kefiya.execution_hint("bank") + " / "
				+ kefiya.execution_hint("here"));
		if (dated_refused && frm.doc.docstatus === 0) {
			frm.set_df_property("manage_due_date", "read_only", 1);
			if (!frm.doc.manage_due_date) {
				frm.set_value("manage_due_date", 1);
			}
		} else {
			frm.set_df_property("manage_due_date", "read_only", 0);
		}

		// And the order itself. Saying so here rather than at the bank is the
		// difference between a sentence and a spent TAN.
		const needed = kefiya.capabilities.required(
			count,
			!frm.doc.manage_due_date && !!frm.doc.execution_date,
			!!frm.doc.instant_payment
		);
		if (kefiya.capabilities.refuses(info, needed)) {
			frm.remove_custom_button(__("Send to bank"));
			frm.dashboard.set_headline_alert(
				__("The bank does not allow \"{0}\" on this account. Sending would be refused after the TAN, so it is not offered here.", [
					kefiya.capabilities.label(needed),
				]),
				"red"
			);
		}
	});
}

function kefiya_render_summary(frm) {
	const count = (frm.doc.items || []).length;
	if (!count) {
		return;
	}
	const mode = count > 1
		? __("Collective order ({0} payments) — a single TAN authorises all of them.", [count])
		: __("Single transfer.");
	frm.dashboard.clear_comment();
	frm.dashboard.add_comment(mode, "blue", true);
}

function kefiya_confirm_and_send(frm) {
	const count = (frm.doc.items || []).length;
	// The payee check travels into this box, because this is where the person
	// who did NOT see the invoice decides. It was recorded when the order was
	// entered; here it is read.
	const rows = (frm.doc.items || []).map((row) => {
		const info = kefiya.payee_verdict(row.payee_check);
		const note = info
			? "<div style='font-size:11px;color:"
				+ (kefiya.payee_needs_a_look(row.payee_check)
					? "var(--red-600,#c0392b)" : "var(--text-muted)")
				+ "'>" + frappe.utils.escape_html(info.short)
				+ (row.payee_check_detail
					? " — " + frappe.utils.escape_html(row.payee_check_detail)
					: "") + "</div>"
			: "";
		return "<tr><td>" + frappe.utils.escape_html(row.recipient_name || "")
			+ note
			+ "</td><td style='font-family:monospace'>"
			+ frappe.utils.escape_html(kefiya.iban_pretty(row.recipient_iban))
			+ "</td><td style='text-align:right'>"
			+ format_currency(row.amount) + "</td></tr>";
	}).join("");

	// A recipient our own history argues with is named again, above the list,
	// where it cannot be scrolled past.
	const flagged = (frm.doc.items || []).filter(
		(row) => kefiya.payee_needs_a_look(row.payee_check));

	const d = new frappe.ui.Dialog({
		title: count > 1 ? __("Send collective order") : __("Send transfer"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options:
					(flagged.length
						? "<div class='alert alert-danger'><b>"
							+ __("{0} of these recipients need a look",
								[flagged.length])
							+ "</b><div style='margin-top:4px'>"
							+ flagged.map((row) =>
								frappe.utils.escape_html(
									row.recipient_name || "")
								+ ": " + frappe.utils.escape_html(
									row.payee_check_detail || "")).join("<br>")
							+ "</div></div>"
						: "")
					+ "<div class='alert alert-warning'>"
					+ __("You are about to move {0} from {1}. Check every recipient — a transfer cannot be undone here.", [
						"<b>" + format_currency(frm.doc.total_amount) + "</b>",
						frappe.utils.escape_html(frm.doc.kefiya_login || ""),
					])
					+ "</div>"
					+ "<table class='table table-bordered'><thead><tr><th>"
					+ __("Recipient") + "</th><th>" + __("IBAN") + "</th><th style='text-align:right'>"
					+ __("Amount") + "</th></tr></thead><tbody>"
					+ rows + "</tbody></table>"
					+ (frm.doc.instant_payment
						? "<p>" + __("Sent as an instant payment (SEPA Instant).") + "</p>"
						: ""),
			},
			{
				fieldtype: "Check",
				fieldname: "confirmed",
				label: __("I checked every recipient and amount"),
				reqd: 1,
			},
		],
		primary_action_label: __("Send now"),
		primary_action: function (values) {
			if (!values.confirmed) {
				frappe.msgprint(__("Please confirm you checked the recipients."));
				return;
			}
			d.hide();
			frappe.call({
				method: "kefiya.utils.client.submit_kefiya_transfer",
				args: {
					transfer_name: frm.doc.name,
					user_scope: frm.docname,
					confirmed: 1,
				},
				freeze: true,
				freeze_message: __("Sending to bank..."),
				callback: function (r) {
					kefiya_handle_transfer_response(frm, r.message);
				},
			});
		},
	});
	d.show();
}
