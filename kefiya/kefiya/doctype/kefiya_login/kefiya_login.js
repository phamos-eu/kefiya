// Copyright (c) 2019, jHetzer and contributors
// For license information, please see license.txt

{% include "kefiya/public/js/controllers/fints_interactive.js" %}

frappe.ui.form.on('Kefiya Login', {
	onload: function(frm) {
		kefiya.interactive.progressbar(frm);
		if(frm.doc.account_iban){
			frm.toggle_display("transaction_settings_section",true);
		}else{
			frm.toggle_display("transaction_settings_section",false);
		}
	},
	setup: function(frm) {
		frm.set_query("erpnext_account", function() {
			return {
				filters: {
					'account_type': 'Bank',
					'company': frm.doc.company,
					'is_group': 0
				}
			};
		});
	},
	refresh: function(frm) {
		// If iban_list exists, it's stored as JSON list of IBAN strings.
		if(frm.doc.iban_list){
			try {
				const opts = JSON.parse(frm.doc.iban_list);
				frm.set_df_property("account_iban","options", opts);
				frm.toggle_display("account_iban", true);
			} catch (e) {
				// fallback: hide field if something is really wrong
				frm.toggle_display("account_iban", false);
			}
		} else if(!frm.doc.account_iban){
			frm.toggle_display("account_iban",false);
		}

		if (frm.doc.stored_client_state) {
			frm.add_custom_button(__("Reset Connection"), function() {
				frm.call('reset_connection').then(() => {
					frm.reload_doc();
				});
			});
		}

		// TODO
		// if (frm.doc.stored_tan_state) {
		// 	frm.add_custom_button(__("Solve TAN Challenge"), function() {
		// 		frm.call('solve_tan_challenge');
		// 	});
		// }

		/*
		if(!frm.doc.__unsaved && frm.doc.account_nr){
			frm.toggle_display("transaction_settings_section",true)
		}else{
			frm.toggle_display("transaction_settings_section",false)
		}
		*/
	},
	get_accounts: function(frm) {
		if (frm.doc.__unsaved){
			frm.save().then(() => {
				frm.events.call_get_login_accounts(frm);
			});
		}else{
			frm.events.call_get_login_accounts(frm);
			frappe.hide_progress();
		}
	},
	/**
	 * Let the user pick one of the loaded accounts in a dialog and save it on
	 * the login. Used after Load Accounts and after a TAN (fints_interactive).
	 */
	choose_account: function(frm, accounts) {
		// Support both legacy (array) and new (object) formats:
		const ibanList = accounts.map(x => {
			// new controller sends dict
			if (x && x.iban) return x.iban;
			// legacy controller sends arrays / tuples
			if (Array.isArray(x) && x.length > 0) return x[0];
			// fallback if it's just a string
			return x;
		}).filter(Boolean);

		if (!ibanList.length) {
			frappe.msgprint(__("The bank returned no accounts for this login."));
			return;
		}

		const dialog = new frappe.ui.Dialog({
			title: __("Choose Bank Account"),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "account_iban",
					label: __("Account IBAN"),
					options: ibanList,
					default: ibanList.includes(frm.doc.account_iban) ? frm.doc.account_iban : ibanList[0],
					reqd: 1,
				},
			],
			primary_action_label: __("Save"),
			primary_action: async (values) => {
				// The server saved the login while loading (connection state),
				// so save on the server and reload, instead of saving the
				// now outdated form.
				await frappe.db.set_value("Kefiya Login", frm.doc.name, {
					account_iban: values.account_iban,
					iban_list: JSON.stringify(ibanList),
				});
				// only now: if saving fails, the dialog and the choice stay
				dialog.hide();
				await frm.reload_doc();
				frm.toggle_display("transaction_settings_section", true);
			},
		});
		dialog.show();
	},
	call_get_login_accounts: function(frm){
		kefiya.interactive.start_progress(frm);
		frappe.call({
			method:"kefiya.utils.client.get_accounts",
			args: {
				'kefiya_login': frm.doc.name,
				'user_scope': frm.doc.name
			},
			always: () => kefiya.interactive.finish_progress(frm),
			callback: function(r) {
				if (!r || !r.message || !r.message.accounts) {
					// if no qualified data is returned, the request was delayed and will be repeated later.
					return;
				}

				frm.events.choose_account(frm, r.message.accounts);
			},
			error: function(/* r */) {
				frm.set_df_property("account_iban","options","");
				frm.toggle_display("account_iban",false);

				frappe.run_serially([
					() => frm.set_value("account_iban",""),
					() => frm.set_value("failed_connection",frm.doc.failed_connection + 1),
					() => frm.save(),
				]);
			}
		});
	}
});
