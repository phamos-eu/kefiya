// Copyright (c) 2019, jHetzer and contributors
// For license information, please see license.txt
//
// On desk load: if any Kefiya Login needs re-auth (TAN), open the first one
// so the form can auto-trigger get_accounts and show the Verification required dialog.

frappe.ready(function() {
	frappe.call({
		method: "kefiya.utils.client.get_logins_needing_reauth",
		callback: function(r) {
			if (!r || !r.message || !r.message.length) return;
			const first_login = r.message[0];
			// Store so Kefiya Login form refresh will auto-trigger get_accounts (same as Load Accounts)
			try {
				localStorage.setItem("kefiya_reauth_login", first_login);
			} catch (e) { /* ignore */ }
			frappe.set_route("Form", "Kefiya Login", first_login);
		}
	});
});
