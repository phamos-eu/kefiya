// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// What the bank allows on an account, for the forms that offer it.
//
// The server reads the list from the bank at every fetch (HIUPD) and keeps it
// on the Bank Account; this is only the reader. Three states come back per
// business transaction -- "allowed", "refused", "unknown" -- and the rule the
// forms follow is: hide only what is REFUSED.
//
// That asymmetry is the whole point. An account nobody has fetched yet knows
// nothing about itself, and hiding on "unknown" would empty the screen for
// every account before its first fetch -- turning a missing answer into a
// missing button, which looks exactly like a broken app.

frappe.provide("kefiya.capabilities");

kefiya.capabilities = {
	// One answer per login, kept for the life of the page. The list changes
	// when the bank is asked again, and asking the bank means a page-long
	// fetch -- so a cache that lives as long as the page is the right length.
	_cache: {},

	/**
	 * @param {string} kefiya_login
	 * @returns {Promise<Object>} {bank_account, checked_on, capabilities}
	 */
	load: function (kefiya_login) {
		if (!kefiya_login) {
			return Promise.resolve({ capabilities: {} });
		}
		if (this._cache[kefiya_login]) {
			return this._cache[kefiya_login];
		}
		const promise = frappe
			.call({
				method: "kefiya.utils.account_capabilities.get_capabilities",
				args: { kefiya_login: kefiya_login },
			})
			.then((r) => (r && r.message) || { capabilities: {} })
			// A failure here must not take a form down with it: not knowing
			// what the bank allows is the state every account starts in.
			.catch(() => ({ capabilities: {} }));
		this._cache[kefiya_login] = promise;
		return promise;
	},

	forget: function (kefiya_login) {
		if (kefiya_login) {
			delete this._cache[kefiya_login];
		} else {
			this._cache = {};
		}
	},

	/** Did the bank say this account cannot do it? */
	refuses: function (info, capability) {
		return !!info && info.capabilities
			&& info.capabilities[capability] === "refused";
	},

	/** Anything but an explicit refusal. */
	allows: function (info, capability) {
		return !this.refuses(info, capability);
	},

	/** Which business transaction an order needs -- mirrors the server. */
	required: function (payment_count, scheduled, instant) {
		if (instant) {
			return payment_count > 1
				? "instant_transfer_collective"
				: "instant_transfer";
		}
		if (scheduled) {
			return payment_count > 1
				? "scheduled_transfer_collective"
				: "scheduled_transfer";
		}
		return payment_count > 1 ? "transfer_collective" : "transfer";
	},

	/**
	 * Why an option is not offered, in words that name the account's answer.
	 *
	 * A greyed-out box with no reason is only marginally better than a hidden
	 * one. This says who refused and what -- the bank, this business
	 * transaction, on this account -- so the reader knows it is neither a
	 * missing right nor a broken form.
	 *
	 * @param {string} capability
	 * @param {number} count payments in the order; several make it collective,
	 *   which is a different business transaction the bank rules on separately
	 */
	refusal_reason: function (capability, count) {
		const what = this.label(capability);
		return count > 1
			? __("The bank does not allow \"{0}\" on this account. Several"
				+ " payments go out as one collective order, which the bank"
				+ " rules on separately from a single one.", [what])
			: __("The bank does not allow \"{0}\" on this account.", [what]);
	},

	label: function (capability) {
		const labels = {
			transfer: __("Transfer"),
			transfer_collective: __("Collective transfer"),
			scheduled_transfer: __("Scheduled transfer"),
			scheduled_transfer_collective: __("Scheduled collective transfer"),
			instant_transfer: __("Instant payment"),
			instant_transfer_collective: __("Collective instant payment"),
			standing_order_read: __("Read standing orders"),
			standing_order_create: __("Create standing order"),
			standing_order_change: __("Change standing order"),
			standing_order_delete: __("Delete standing order"),
			direct_debit: __("Direct debit"),
			statements: __("Electronic statements"),
			holdings: __("Portfolio"),
		};
		return labels[capability] || capability;
	},
};
