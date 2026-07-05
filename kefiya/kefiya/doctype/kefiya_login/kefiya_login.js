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

		if (frm.doc.account_iban) {
			// Primary action: pull current transactions + everything else the
			// bank offers. The button greys out while the fetch runs so it
			// cannot be triggered twice (a second FinTS dialog / TAN in
			// parallel).
			const fetch_btn = frm.add_custom_button(__("Aktuelle Umsätze abrufen"), function() {
				const $btn = fetch_btn;
				$btn.prop("disabled", true).addClass("disabled");
				frappe.call({
					method: "kefiya.utils.client.fetch_all",
					args: { kefiya_login: frm.doc.name, user_scope: frm.doc.name },
					freeze: true,
					freeze_message: __("Rufe Umsätze und alle weiteren Bankdaten ab ..."),
					callback: function(r) {
						const s = (r && r.message) || {};
						const t = (s.transactions && s.transactions.new_count) || 0;
						const parts = [__("New transactions: {0}", [t])];
						if (s.planned) {
							parts.push(__("Forecast: {0} new / {1} updated / {2} cancelled",
								[s.planned.created || 0, s.planned.updated || 0, s.planned.cancelled || 0]));
						}
						if (s.statements) parts.push(__("Documents: {0}", [s.statements.count || 0]));
						if (s.credit_card) parts.push(__("Credit-card txns: {0}", [s.credit_card.count || 0]));
						if (s.errors && s.errors.length) {
							parts.push(__("Not available from this bank: {0}", [s.errors.join(", ")]));
						}
						frappe.msgprint({
							title: __("Abruf abgeschlossen"),
							indicator: (s.errors && s.errors.length) ? "orange" : "green",
							message: parts.join("<br>")
						});
						frm.reload_doc();
					},
					always: function() {
						// Re-enable even on error/TAN-abort so the user can retry.
						$btn.prop("disabled", false).removeClass("disabled");
					}
				});
			});

			frm.add_custom_button(__("Kontoauszüge / Dokumente"), function() {
				frappe.call({
					method: "kefiya.utils.fints_controller.get_statements",
					args: { kefiya_login: frm.doc.name },
					freeze: true,
					freeze_message: __("Fetching document list via FinTS ..."),
					callback: function(r) {
						const items = (r && r.message) || [];
						let body;
						if (!items.length) {
							body = `<p>${__("No documents / statements available for this account.")}</p>`;
						} else {
							const keys = Object.keys(items[0] || {});
							const head = keys.map(k => `<th>${frappe.utils.escape_html(k)}</th>`).join("");
							const rows = items.map(it => {
								const tds = keys.map(k => {
									const v = it[k] == null ? "" : String(it[k]);
									return `<td>${frappe.utils.escape_html(v)}</td>`;
								}).join("");
								return `<tr>${tds}</tr>`;
							}).join("");
							body = `<div style="overflow-x:auto"><table class="table table-bordered">`
								+ `<thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
						}
						new frappe.ui.Dialog({
							title: __("Kontoauszüge / Dokumente"),
							size: "large",
							fields: [{ fieldtype: "HTML", fieldname: "doc_list", options: body }],
						}).show();
					}
				});
			}, __("FinTS"));
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
	call_get_login_accounts: function(frm){
		frappe.call({
			method:"kefiya.utils.client.get_accounts",
			args: {
				'kefiya_login': frm.doc.name,
				'user_scope': frm.doc.name
			},
			callback: function(r) {
				frm.set_value("account_iban","");

				if (!r || !r.message || !r.message.accounts) {
					// if no qualified data is returned, the request was delayed and will be repeated later.
					return;
				}

				frm.toggle_display("account_iban",true);

				frm.set_value("account_iban","");
				frm.set_value("failed_connection",0);

				// Support both legacy (array) and new (object) formats:
				const ibanList = r.message.accounts.map(x => {
					// new controller sends dict
					if (x && x.iban) return x.iban;
					// legacy controller sends arrays / tuples
					if (Array.isArray(x) && x.length > 0) return x[0];
					// fallback if it's just a string
					return x;
				});

				frm.set_df_property("account_iban","options",ibanList);
				frm.set_value("account_iban", ibanList[0] || "");
				frm.set_value("iban_list", JSON.stringify(ibanList));
				frm.toggle_reqd("account_iban",true);
			},
			error: function(/* r */) {
				frappe.hide_progress();
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
