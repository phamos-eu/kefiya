// Copyright (c) 2019, jHetzer and contributors
// For license information, please see license.txt
frappe.provide("kefiya.tools");

frappe.pages["bank-transaction-wiz"].on_page_load = function (wrapper) {
	kefiya.tools.assignWizardObj = new kefiya.tools.assignWizard(
		wrapper
	);
};

kefiya.tools.assignWizard = class assignWizard {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Bank Transaction Wizard"),
			single_column: true,
		});
		this.parent = wrapper;
		this.page = this.parent.page;
		this.remove_page_buttons();
		this.make();
		this.add_custom()
	}
	remove_page_buttons(){
		// $('.custom-actions').remove()
		$('.page-form').remove();	
		$('.menu-btn-group').remove()
	}

	async fetchKefiyaSettings() {
		const response = await frappe.call({
			method: "frappe.client.get",
			args: {
				doctype: "Kefiya Settings",
				name: "Kefiya Settings",
			},
		});
		return response.message;
	}

	async make() {
		const me = this;
		me.page.hide_icon_group();
		me.clear_page_content();
		let result = await this.fetchKefiyaSettings()
		me.make_assignWizard_tool(result);
		// me.add_actions();
	}
	
	async add_custom() {
		const me = this;
		const settings = await me.fetchKefiyaSettings();
		const assign_against = settings.assign_against; 

		const tab_container = $('<div class="btn-group" role="group" aria-label="Match Against Tabs" style="margin: 10px 10px;"></div>')
			.appendTo(this.page.main);
	
		const tabs = [
			{ label: 'Sales Invoice', value: 'Sales Invoice' },
			{ label: 'Purchase Invoice', value: 'Purchase Invoice' },
			{ label: 'Journal Entry', value: 'Journal Entry' },
		];
	
		tabs.forEach((tab, index) => {
			const isActive = (tab.value === assign_against) ? 'active' : '';
			const button_margin = index < tabs.length - 1 ? 'mr-2' : ''; 
	
			const tab_element = $(`<button type="button" class="btn btn-default btn-xs center-block ${isActive} ${button_margin}">${tab.label}</button>`)
				.appendTo(tab_container);
			
			tab_element.on('click', () => this.change_match_against(tab.value));
		});
	}
			
	
	change_match_against(selected_match) {
		const me = this;
		$(me.page.main).find('.btn-group .btn').removeClass('active');
		$(me.page.main).find(`.btn:contains('${selected_match}')`).addClass('active');
	
		frappe.call({
			method: "kefiya.utils.client.change_match_against",
			args: {
				selected_match: selected_match
			},
			callback: function (r) {
				me.make();
			}
		});
	}	

	add_actions() {
		const me = this;

		me.page.show_menu();
		me.page.add_menu_item(
			__("Auto assign"),
			function () {
				frappe.call({
					method: "kefiya.utils.client.auto_assign_payments",
					args: {},
					callback: function (r) {
						if (r.message.success === true) {
							var result = [];
							if (
								Array.isArray(r.message.payments) &&
								r.message.payments.length
							) {
								result.push(__("Assingment was successfull"));
								r.message.payments.forEach(function (item) {
									result.push(
										__("Payment") +
											" " +
											get_doc_link("Payment Entry", item.PaymentName) +
											" " +
											__("from customer") +
											" " +
											get_doc_link("Customer", item.CustomerName) +
											" " +
											__("has been assinged to sale invoice") +
											" " +
											get_doc_link("Sales Invoice", item.SaleName)
									);
								});
							} else {
								result.push(__("No payments were found for assignment"));
							}
							kefiya.tools.assignWizardList.refresh();
							frappe.msgprint(result);
						} else {
							frappe.msgprint(__("Failed to assign payments"));
						}
					},
				});
			},
			true
		);
	}

	clear_page_content() {
		const me = this;
		me.page.clear_fields();
		$(me.page.body).find(".frappe-list").remove();
	}

	make_assignWizard_tool(kefiyaSettings) {
		const me = this;
		// ensure that the metadata for the "Sales Invoice" DocType is loaded before proceeding with the wizard setup(AssignWizardTool).
		if (kefiyaSettings.assign_against==='Sales Invoice'){
			frappe.model.with_doctype("Sales Invoice", () => {
				kefiya.tools.assignWizardList =
					new kefiya.tools.AssignWizardTool({
						parent: me.parent,
						doctype: "Sales Invoice",
						page_title: __(me.page.title),
						kefiyaSettings: kefiyaSettings
					});
				frappe.pages["bank-transaction-wiz"].refresh =
					function (/* wrapper */) {
						window.location.reload(false);
					};
			});
		} else if (kefiyaSettings.assign_against==='Purchase Invoice'){
			frappe.model.with_doctype("Purchase Invoice", () => {
				kefiya.tools.assignWizardList =
					new kefiya.tools.AssignWizardTool({
						parent: me.parent,
						doctype: "Purchase Invoice",
						page_title: __(me.page.title),
						kefiyaSettings: kefiyaSettings
					});
				frappe.pages["bank-transaction-wiz"].refresh =
					function (/* wrapper */) {
						window.location.reload(false);
					};
			});
		} else if (kefiyaSettings.assign_against==='Journal Entry'){
			frappe.model.with_doctype("Bank Transaction", () => {
				kefiya.tools.assignWizardList =
					new kefiya.tools.AssignWizardTool({
						parent: me.parent,
						doctype: "Bank Transaction",
						page_title: __(me.page.title),
						kefiyaSettings: kefiyaSettings
					});
				frappe.pages["bank-transaction-wiz"].refresh =
					function (/* wrapper */) {
						window.location.reload(false);
					};
			});
		}
	}
};

kefiya.tools.AssignWizardTool = class AssignWizardTool extends (
	frappe.views.BaseList
) {
	constructor(opts) {
		super(opts);
		this.kefiyaSettings = opts.kefiyaSettings
		this.show();
	}

	// It establishes default settings for various aspects of the assignment wizard tool's data display and behavior
	setup_defaults() {
		super.setup_defaults();
		this.page_length = 20;
		this.sort_order = "asc";
		if (this.kefiyaSettings.assign_against === 'Sales Invoice'){
			this.sort_by = "customer";
			this.fields = [
				"name",
				"customer",
				"customer_name",
				"outstanding_amount",
				"posting_date",
				"due_date",
				"currency",
				"paid_amount",
			];
		} else if (this.kefiyaSettings.assign_against === 'Purchase Invoice'){
			this.sort_by = "supplier";
			this.fields = [
				"name",
				"supplier",
				"supplier_name",
				"outstanding_amount",
				"posting_date",
				"due_date",
				"currency",
				"paid_amount",
			];
		} else if (this.kefiyaSettings.assign_against === 'Journal Entry'){
			this.fields = [
				"name",
				"description",
				"party",
				"party_type",
				"unallocated_amount",
				"deposit",
				"withdrawal",
				"date",
				"company",
				"currency",
				"bank_account",
				"bank_party_name",
			];
		}
	}

	setup_view() {
		this.render_header();
	}

	setup_side_bar() {
		//
	}

	set_breadcrumbs() {
		frappe.breadcrumbs.add("Kefiya");
	}

	make_standard_filters() {
		//
	}

	freeze() {
		// this.$result.find('.list-count').html(`<span>${__('Refreshing')}...</span>`);
	}

	get_args() {
		const args = super.get_args();
		
		if (this.kefiyaSettings.assign_against === 'Sales Invoice'){
			return Object.assign({}, args, {
				...args.filters.push(
					["Sales Invoice", "docstatus", "=", 1],
					["Sales Invoice", "outstanding_amount", ">", 0]
				),
			});
		} else if(this.kefiyaSettings.assign_against === 'Purchase Invoice'){
			return Object.assign({}, args, {
				...args.filters.push(
					["Purchase Invoice", "docstatus", "=", 1],
					["Purchase Invoice", "outstanding_amount", ">", 0]
				),
			});
		} else if(this.kefiyaSettings.assign_against === 'Journal Entry'){
			return Object.assign({}, args, {
				...args.filters.push(
					["Bank Transaction", "docstatus", "=", 1],
					["Bank Transaction", "status", "in", ["Unreconciled", "Settled"]],
					["Bank Transaction", "unallocated_amount", ">", 0]
				),
			});
		}

	}

	get_row_call_args(party, optionValue, matchAgainst) {
		let doctype, fields, filters, order_by;
		
		if (optionValue == "Payment Entry") {
			doctype = "Payment Entry";
			fields = [
				"name",
				"party",
				"posting_date",
				"unallocated_amount",
				"remarks",
			];
			filters = {
				docstatus: 0,
				party: party,
			};
			order_by = "posting_date";
		} else if (optionValue == "Bank Transaction") {
			doctype = "Bank Transaction";
			fields = [
				"name", 
				"party",
				"party_type",
				"date", 
				"unallocated_amount", 
				"description"
			];
			
			filters = {
				docstatus: 1,
				unallocated_amount: [">", 0],
				...(matchAgainst === "Sales Invoice" ? { party, deposit: [">", 0] } : {}),
				...(matchAgainst === "Purchase Invoice" ? { party, withdrawal: [">", 0] } : {})
			};
			order_by = "date";
		} else {
			// code if both are selected
		}
		return {
			method: "frappe.client.get_list",
			args: {
				doctype,
				fields,
				filters,
				order_by,
			},
			freeze: this.freeze_on_refresh || false,
			freeze_message: this.freeze_message || __("Loading") + "...",
		};
	}

	async render() {
		// Extract the selected value from "Kefiya Settings" doctype
		const optionValue = this.kefiyaSettings.show_entries_in_payment_assignment_wizard
		const matchAgainst = this.kefiyaSettings.assign_against

		const me = this;
		this.$result.find(".list-row-contain").remove();
		$('[data-fieldname="name"]').remove();
		$('[data-fieldname="status"]').remove();
		$('[data-fieldname="title"]').remove();
		$('[data-original-title="Refresh"]').remove();
		$('.custom-btn-group').remove()
		
		let rowHTML;
		let party_value;
		rowHTML = '<div class="list-row-contain"></div>';
		
		if (matchAgainst=="Journal Entry"){
			for (const value of me.data) {
				const row = $(rowHTML).data("data", value).appendTo(me.$result).get(0);
				new kefiya.tools.AssignWizardRow(
					row,
					value,
					null,
					optionValue,
					matchAgainst
				);
			}
		} else {
			// me.data - list of sales invoice/purchase invoice. the below code fetchs all payment entries(Payment Entry and Bank Transaction) associated with single sales invoice
			for (const value of me.data) {
					
				if (matchAgainst==="Sales Invoice"){
					party_value = value.customer
				} else if (matchAgainst === "Purchase Invoice"){
					party_value = value.supplier
				}
	
				const r = await frappe.call(
					this.get_row_call_args(party_value, optionValue, matchAgainst)
				);
	
				// r.message - list of all payment entries
				if (Array.isArray(r.message) && r.message.length) {
					const row = $(rowHTML).data("data", value).appendTo(me.$result).get(0);
	
					new kefiya.tools.AssignWizardRow(
						row,
						value,
						r.message,
						optionValue,
						matchAgainst
					);
				}
			}
		}
	}

	render_header() {
		const me = this;
		if ($(this.wrapper).find(".payment-assign-wizard-header").length === 0) {
			me.$result.append(frappe.render_template("bank_transaction_header"));
		}
	}
};

kefiya.tools.AssignWizardRow = class AssignWizardRow {
	constructor(row, data, payments, optionValue, matchAgainst) {
        // system default for date
		let sysdefaults = frappe.sys_defaults;
		let date_format = sysdefaults && sysdefaults.date_format ? sysdefaults.date_format : "yyyy-mm-dd";
		date_format = date_format.replace('yyyy', 'YYYY').replace('dd', 'DD').replace('mm', 'MM');

		if (matchAgainst=="Journal Entry"){
			this.data = data
			this.data.payments = payments;
			this.row = row
			this.data.unallocated_amount = format_currency(this.data.unallocated_amount, this.data.currency);
			this.data.optionValue = optionValue;
			this.data.matchAgainst = matchAgainst;
			this.make()
			this.bind_events();
		} else {
			this.data = data;
			// formatting date based on system defaults
			this.data.outstanding_amount = format_currency(this.data.outstanding_amount, this.data.currency); 
			this.data.posting_date = moment(this.data.posting_date).format(date_format);
	
			this.data.payments = payments;
	
			this.data.payments.forEach(payment => {
				payment.date = moment(payment.date).format(date_format);
				payment.unallocated_amount = format_currency(payment.unallocated_amount, this.data.currency);
			});
	
			this.data.optionValue = optionValue;
			this.data.matchAgainst = matchAgainst;
			this.row = row;
			this.make();
			this.bind_events();
		}
	}

	make() {
		$(this.row).append(frappe.render_template("bank_transaction_row", this.data));
	}

	bind_events() {
		const me = this;

		$(me.row).on("click", ".hide_row", function () {
			me.row.remove();
		});
		// assign payment entries to sales invoice
		$(me.row).on("click", ".assign_payment", function () {
			frappe.call({
				method: "kefiya.utils.client.add_payment_reference",
				args: {
					sales_invoice: me.data.name,
					payment_entry: $(this).attr("data-name"),
				},
				callback(/* r */) {
					// Refresh page after asignment
					kefiya.tools.assignWizardList.refresh();
				},
			});
		});

		// reconcile bank transaction against sales invoice
		$(me.row).on("click", ".reconcile_transaction", function () {
			const currency = me.data.currency;
			const invoice_name = me.data.name;
			const match_against = me.data.matchAgainst;
			const bank_transaction_name = $(this).attr("data-name");

			frappe.call({
				method: "kefiya.utils.client.create_payment_entry",
				args: {
					bank_transaction_name: bank_transaction_name,
					invoice_name: invoice_name,
					match_against: match_against
				},
				callback: function (r) {
					let vouchers = [];

					const paid_amount = r.message[0];
					const payment_entry_name = r.message[1];

					vouchers.push({
						payment_doctype: "Payment Entry",
						payment_name: payment_entry_name,
						amount: format_currency(paid_amount, currency),
					});
					
					frappe.call({
						method:
							"erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.reconcile_vouchers",
						args: {
							bank_transaction_name: bank_transaction_name,
							vouchers: vouchers,
						},
						callback(/* r */) {
							// Refresh page after asignment		
							kefiya.tools.assignWizardList.refresh();
						},
					});
				},
			});

		});

		// create journal entry from bank transaction
		$(me.row).on("click", ".create_journal_entry", function () {
			
			let partyType = me.data.party_type ? me.data.party_type:me.data.deposit > 0 ? 'Customer': 'Supplier'

			let dialog = new frappe.ui.Dialog({
				title: __('Create Journal Entry'),
				fields: [
				{
					label: 'Sender',
					fieldname: 'sender',
					fieldtype: 'Data',
					read_only:  me.data.bank_party_name ? 1 : 0,
					default: me.data.bank_party_name,
				}, {
					label: 'Reference Number',
					fieldname: 'reference_number',
					fieldtype: 'Small Text',
					read_only:  me.data.description ? 1 : 0,
					reqd: 1,
					default: me.data.description.substring(0, 140),
				}, {
					label: 'Posting Date',
					fieldname: 'posting_date',
					fieldtype: 'Date',
					default: me.data.date,
					read_only: me.data.date ? 1 : 0,
					reqd: 1,
				}, {
					label: 'Cheque/Reference Date',
					fieldname: 'reference_date',
					fieldtype: 'Date',
					default: me.data.date,
					read_only: me.data.date ? 1 : 0,
					reqd: 1,
				}, {
					label: 'Mode of Payment',
					fieldname: 'mode_of_payment',
					fieldtype: 'Link',
					options: "Mode of Payment",
				}, {
					fieldname: 'col_break1',
					fieldtype: 'Column Break',
				}, {
					label: 'Journal Entry Type',
					fieldname: 'journal_entry_type',
					fieldtype: 'Select',
					options:
					"Journal Entry\nInter Company Journal Entry\nBank Entry\nCash Entry\nCredit Card Entry\nDebit Note\nCredit Note\nContra Entry\nExcise Entry\nWrite Off Entry\nOpening Entry\nDepreciation Entry\nExchange Rate Revaluation\nDeferred Revenue\nDeferred Expense",
					default: "Bank Entry",
					reqd: 1,
				}, {
					label: partyType === 'Supplier' ? 'Expense Account' : 'Income Account',
					fieldname: 'account',
					fieldtype: 'Link',
					options: "Account",
					get_query: () => {
						return {
							filters: {
								is_group: 0,
								company: frappe.defaults.get_default("company"),
								// account_type: partyType === 'Supplier' ? 'Expense Account' : 'Income Account',

							},
						};
					},
					reqd: 1,
					
				}, {
					label: 'Party Account',
					fieldname: 'party_account',
					fieldtype: 'Link',
					options: "Account",
					get_query: () => {
						return {
							filters: {
								is_group: 0,
								company: frappe.defaults.get_default("company"),
								account_type: partyType === 'Supplier' ? 'Payable' : 'Receivable',

							},
						};
					},
					reqd: 1,
					
				}, {
					label: 'Party Type',
					fieldname: 'party_type',
					fieldtype: 'Link',
					options: "Party Type",
					default: partyType,
					read_only: partyType ? 1 : 0,
					onchange: function(){
						dialog.fields_dict['party'].df.options = dialog.get_value('party_type');
						dialog.fields_dict['party'].refresh();
					}
				}, {
					label: 'Party',
					fieldname: 'party',
					fieldtype: 'Link',
					options: partyType,
					default: me.data.party,
					read_only: me.data.party ? 1 : 0,
				}, {
					fieldname: 'accounting_entries_section',
					fieldtype: 'Section Break',
				}, {
					label: 'Accounting Entries',
					fieldname: 'accounting_entries',
					fieldtype: 'Text Editor',
					read_only: 1,
				}
				],
				primary_action_label: __('Submit'),
				primary_action: function(/* values */) {

					const bank_transaction_name = me.data.name

					let ref_num = dialog.get_value("reference_number")
					if (!me.data.description){
						ref_num = ref_num.substring(0, 140)
					}

					const reference_number = ref_num
					const reference_date = dialog.get_value("reference_date")
					const posting_date = dialog.get_value("posting_date")
					const entry_type = dialog.get_value("journal_entry_type")
					const second_account = dialog.get_value("party_account")
					const account = dialog.get_value("account")
					const mode_of_payment = dialog.get_value("mode_of_payment")
					const party_type = dialog.get_value("party_type")
					const party = dialog.get_value("party")

					frappe.call({
						method: "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_journal_entry_bts",
						args: {
							bank_transaction_name,
							reference_number,
							reference_date,
							posting_date,
							entry_type,
							second_account,
							account,
							mode_of_payment,
							party_type,
							party
						},
						callback: function(response) {
							if(response.message) {
								kefiya.tools.assignWizardList.refresh();
								dialog.hide();
							}
						}
					});
				},
			});

			dialog.show();


			async function getCompanyAccount() {
				const response = await frappe.db.get_value('Bank Account', me.data.bank_account, 'account');
				return response.message.account;
			}
			
			async function checkAndSetValues() {
				const account = dialog.get_value('account');
				const party_account = dialog.get_value('party_account');
				
				if (account && party_account) {
					const company_account = await getCompanyAccount();
					const withdrawal = format_currency(me.data.withdrawal, me.data.currency);
					const deposit = format_currency(me.data.deposit, me.data.currency);
			
					dialog.set_value('accounting_entries', `
						<table class="table-bordered table-hover">
							<thead>
								<tr>
									<th class="text-left">Account</th>
									<th class="text-right">Debit</th>
									<th class="text-right">Credit</th>
								</tr>
							</thead>
							<tbody>
								<tr>
									<td>${account}</td>
									<td class="text-right">${withdrawal}</td>
									<td class="text-right">${deposit}</td>
								</tr>
								<tr>
									<td>${party_account}</td>
									<td class="text-right">${deposit}</td>
									<td class="text-right">${withdrawal}</td>
								</tr>
								<tr>
									<td>${party_account}</td>
									<td class="text-right">${withdrawal}</td>
									<td class="text-right">${deposit}</td>
								</tr>
								<tr>
									<td>${company_account}</td>
									<td class="text-right">${deposit}</td>
									<td class="text-right">${withdrawal}</td>
								</tr>
							</tbody>
						</table>
					`);
				} else {
					dialog.set_value('accounting_entries', '');
				}
			}
			
			dialog.fields_dict['account'].df.onchange = checkAndSetValues;
			dialog.fields_dict['party_account'].df.onchange = checkAndSetValues;
			
		});
	}
};

function get_doc_link(doctype, name) {
	return '<a href="#Form/' + doctype + "/" + name + '"><b>' + name + `</b></a>`;
}
