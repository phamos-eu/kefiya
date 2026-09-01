// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

// What our own payment history says about a recipient, shown where it helps.
//
// The bank's Verification of Payee happens at the bank, on submission -- in
// FinTS the check is part of the transfer message, so asking it means sending
// the order. That is too late where one person enters an order and another
// sends it. This is the question that CAN be asked at entry: have we paid
// this IBAN before, and did it belong to this name?
//
// It is shown three times on purpose, because three different people look:
// while entering, on the order itself, and in the box the managing director
// confirms before the money leaves.

frappe.provide("kefiya");

kefiya.PAYEE_VERDICTS = {
	known: {
		colour: "green",
		short: __("Payee known"),
		long: __("This IBAN was paid before under this name."),
	},
	// The loudest of the four. Right letterhead, right name, swapped IBAN --
	// and the bank's own check would not flag it, because the swapped IBAN
	// really does belong to somebody with a plausible name.
	other_iban: {
		colour: "red",
		short: __("Known payee, new IBAN"),
		long: __("We have paid this payee before, always to a different IBAN."
			+ " Check the invoice against an earlier one before releasing"
			+ " this."),
	},
	name_differs: {
		colour: "red",
		short: __("IBAN known under another name"),
		long: __("This IBAN was paid before, but to somebody else."),
	},
	// Not an alarm. Every payee is new once.
	new: {
		colour: "orange",
		short: __("New payee"),
		long: __("First payment to this IBAN. Worth a second pair of eyes."),
	},
};

kefiya.payee_verdict = function (verdict) {
	return kefiya.PAYEE_VERDICTS[verdict] || null;
};

//: Does this verdict need a human to look before the money leaves?
kefiya.payee_needs_a_look = function (verdict) {
	return verdict === "other_iban" || verdict === "name_differs";
};

kefiya.payee_check = function (name, iban) {
	if (!iban) return Promise.resolve(null);
	return frappe.call({
		method: "kefiya.utils.payee_check.check_payee",
		args: { name: name || "", iban: iban },
		silent: true,
	}).then(function (r) {
		return (r && r.message) || null;
	}).catch(function () {
		// Not knowing is not the same as being fine, and the caller renders
		// nothing rather than a reassuring tick.
		return null;
	});
};

//: One line about a recipient, for a dialog. Detail included, because "known
//: payee, new IBAN" without the old IBAN is an alarm nobody can act on.
kefiya.payee_check_html = function (answer) {
	if (!answer) return "";
	const info = kefiya.payee_verdict(answer.verdict);
	if (!info) return "";
	const esc = frappe.utils.escape_html;
	const tone = { green: "var(--green-600,#1a7f37)",
		orange: "var(--orange-600,#b26a00)",
		red: "var(--red-600,#c0392b)" }[info.colour];

	let detail = info.long;
	if (answer.verdict === "other_iban" && (answer.other_ibans || []).length) {
		detail += " " + __("So far: {0}",
			[answer.other_ibans.map(kefiya.iban_pretty).join(", ")]);
	}
	if (answer.verdict === "name_differs" && (answer.known_as || []).length) {
		detail += " " + __("So far: {0}", [answer.known_as.join(", ")]);
	}
	return "<div style='color:" + tone + ";font-size:12px'><b>"
		+ esc(info.short) + "</b> — " + esc(detail) + "</div>";
};

// --- suggestions while typing ------------------------------------------------
//
// The form has always advertised this: "Known payees are suggested while you
// type". They were not. The field called
//
//     input.autocomplete({ source: names, minLength: 2 })
//
// which is the jQuery UI widget. Frappe does not ship jQuery UI, so the call
// landed on undefined, threw nothing, and did nothing.
//
// The first replacement reached for window.Awesomplete, on the grounds that
// Frappe uses awesomplete itself. It does -- through `import Awesomplete from
// "awesomplete"` inside its own controls, which esbuild scopes to those
// modules. Nothing puts it on window. That fix would have been the same silent
// nothing as the one it replaced, with a different reason, and the form would
// have gone on promising a list nobody sees.
//
// So: <datalist>. It is part of the HTML the browser already implements, it
// needs no library, it cannot be scoped away by a bundler, and free text is
// its default rather than a workaround -- which matters here, because the
// transfer with no invoice behind it is what this document exists for.

//: Everyone we have paid, asked once per page. The list is a few hundred
//: names; fetching it per dialog would be a request for every order entered.
kefiya.known_payees = function () {
	if (!kefiya._known_payees) {
		kefiya._known_payees = frappe.call({
			method: "kefiya.utils.payee_check.known_payees",
			silent: true,
		}).then(function (r) {
			return (r && r.message) || [];
		}).catch(function () {
			// No suggestions is a worse form, not a broken one -- but the
			// cache is dropped, because a connection blip while the first
			// dialog of the day opens must not silence the list until the
			// next full page load.
			kefiya._known_payees = null;
			return [];
		});
	}
	return kefiya._known_payees;
};

//: Attach a suggestion list to a plain input.
//:
//: :param items: strings, or {label, value} where the value lands in the
//:     field and the label is what the browser shows beside it
//: :return: a function that replaces the list, or null
kefiya.suggest = function (input, items) {
	const el = (input && input.get) ? input.get(0) : input;
	if (!el || !el.parentNode) return null;

	let list = el._kefiya_suggest;
	if (!list) {
		list = document.createElement("datalist");
		list.id = "kef-suggest-" + (kefiya._suggest_id = (
			kefiya._suggest_id || 0) + 1);
		el.parentNode.appendChild(list);
		el.setAttribute("list", list.id);
		el._kefiya_suggest = list;
	}

	const fill = function (values) {
		list.innerHTML = "";
		(values || []).forEach(function (item) {
			const option = document.createElement("option");
			if (typeof item === "string") {
				option.value = item;
			} else {
				option.value = item.value;
				if (item.label) option.label = item.label;
			}
			list.appendChild(option);
		});
	};
	fill(items);
	return fill;
};

//: The payee whose name this is, out of what known_payees() returned.
//:
//: Matched by normalise_payee_name, which is the rule the check itself uses.
//: A plain lowercased comparison was not: the history holds "Sofienstraße
//: GmbH & Co. KG", this invoice says "Sofienstrasse GmbH", and the two are
//: the same payee -- check_payee says so, and a suggestion list that
//: disagreed would offer no IBAN for a payee it had just called known.
kefiya.payee_named = function (payees, name) {
	const wanted = kefiya.normalise_payee_name(name);
	if (!wanted.length) return null;
	return (payees || []).find(function (p) {
		return kefiya.same_payee_name(
			wanted, kefiya.normalise_payee_name(p.name));
	}) || null;
};

//: Legal forms and decorations that say nothing about identity. The same set
//: as kefiya.utils.payee_check.NOISE, and a test compares the two -- two
//: lists mean the browser and the server disagree about who a payee is.
kefiya.PAYEE_NOISE = [
	"GMBH", "AG", "KG", "CO", "OHG", "GBR", "UG", "EK", "EV", "SE", "MBH",
	"COKG", "GMBHCOKG", "HAFTUNGSBESCHRAENKT", "UND", "AND", "THE",
	"HERR", "FRAU", "DR", "PROF", "DIPLING",
];

//: A name reduced to what identifies it. Umlauts stay as they are, because
//: "Müller" and "Mueller" are a real question and folding them together here
//: would answer it silently in the wrong direction.
kefiya.normalise_payee_name = function (value) {
	const text = String(value || "").toUpperCase()
		.replace(/[^A-Z0-9ÄÖÜß]+/g, " ");
	return text.split(" ").filter(function (word) {
		return word && kefiya.PAYEE_NOISE.indexOf(word) < 0;
	});
};

//: Exact or a subset, the same two cases names_match() calls "exact" and
//: "close". An overlap of one word out of five is not a match in either.
kefiya.same_payee_name = function (left, right) {
	if (!left.length || !right.length) return false;
	const a = left.slice().sort().join(" ");
	const b = right.slice().sort().join(" ");
	if (a === b) return true;
	const subset = function (small, big) {
		return small.every(function (w) { return big.indexOf(w) >= 0; });
	};
	return subset(left, right) || subset(right, left);
};
