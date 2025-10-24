// Copyright (c) 2019, jHetzer and contributors
// For license information, please see license.txt
frappe.provide("kefiya.tools");

{% include "kefiya/public/js/controllers/iban_tools.js" %}

frappe.pages['bank_account_wizard'].on_page_load = function(wrapper) {
	kefiya.tools.bankWizardObj = new kefiya.tools.bankWizard(
		wrapper);
};

kefiya.tools.bankWizard = class BankWizard {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Bank Account Wizard"),
			single_column: true
		});
		this.parent = wrapper;
		this.page = this.parent.page;
		this.make();
	}

	make() {
		const me = this;
		me.clear_page_content();
		me.make_bankwizard_tool();
		me.add_actions();
	}

	add_actions() {
		const me = this;

		me.page.show_menu();

		me.page.add_menu_item(__("Create All"), function() {
			function createAllBankAccount(data){
				kefiya.iban_tools.setPartyBankAccount({
					doc: data[0],
				}, function(e) {
					if (e.message.status == true) {
						kefiya.tools.bankWizardList.ref_items.splice(0,1);
						kefiya.tools.bankWizardList.render();
						setTimeout(
							function(){
								frappe.hide_msgprint();
								if(data.length > 0){
									createAllBankAccount(data);
								}
							}, 700
						);
					}
				});
			}
			createAllBankAccount(kefiya.tools.bankWizardList.ref_items);

		}, true);
	}

	clear_page_content() {
		const me = this;
		me.page.clear_fields();
		$(me.page.body).find('.frappe-list').remove();
	}

	make_bankwizard_tool() {
		const me = this;
		frappe.call({
			method: "kefiya.utils.client.get_missing_bank_accounts",
			args: {},
			callback(r) {
				frappe.model.with_doctype("Bank Transaction", () => {
					kefiya.tools.bankWizardList = new kefiya.tools.bankWizardTool({
						parent: me.parent,
						doctype: "Bank Transaction",
						page_title: __(me.page.title),
						ref_items: r.message
					});
					frappe.pages['bank_account_wizard'].refresh = 
					function(/* wrapper */) {
						window.location.reload(false);
					};
				});
			}
		});
	}
};


kefiya.tools.bankWizardTool = class BankWizardTool extends frappe.views.BaseList {
	constructor(opts) {
		super(opts);
		this.show();
	}

	setup_defaults() {
		super.setup_defaults();
		this.fields = ['date', 'deposit', 'withdrawal', 'description', 'bank_party_iban'];
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

	before_refresh() {
		frappe.model.with_doctype("Bank Transaction", () => {
			frappe.call({
				method: "kefiya.utils.client.get_missing_bank_accounts",
				args: {},
				callback(r) {
					this.ref_items = r.message;
				}
			});
		});
	}

	freeze() {
		this.$result.find('.list-count').html(
			`<span>${__('Refreshing')}...</span>`);
	}
	render() {
		const me = this;
		me.data = me.ref_items;
		me.page.btn_secondary.click(function(/* e */) {
			window.location.reload(false);
		});
		this.$result.find('.list-row-contain').remove();
		$('[data-fieldname="name"]').remove();
		$('[data-fieldname="payment_type"]').remove();
		$('[data-fieldname="title"]').remove();

		me.data.map((value) => {
			if (me.ref_items.filter(item => (item.name === value.name)).length >
				0) {
				const row = $('<div class="list-row-contain"></div>').data("data", value).appendTo(me.$result).get(0);
				new kefiya.tools.bankWizardRow(row, value);
			}
		});
	}

	render_header() {
		const me = this;
		if ($(this.wrapper).find('.bank-account-wizard-header').length === 0) {
			me.$result.append(frappe.render_template("bank_account_wizard_header"));
		}
	}
};
kefiya.tools.bankWizardRow = class BankWizardRow {
	constructor(row, data) {
		this.data = data;
		this.row = row;
		this.make();
		this.bind_events();
	}

	make() {
		this.data.deposit = format_currency(this.data.deposit, this.data.currency);
		this.data.withdrawal = format_currency(this.data.withdrawal, this.data.currency);
		$(this.row).append(frappe.render_template("bank_account_wizard_row",
			this.data));
	}

	bind_events() {
		const me = this;
		$(me.row).on('click', '.new-bank-account', function() {
			kefiya.iban_tools.setPartyBankAccount({
				doc: me.data
			}, function(e) {
				if (e.message.status == true) {
					var index = kefiya.tools.bankWizardList
						.ref_items.findIndex(x => x.name === me.data.name);
					if(index >= 0){
						kefiya.tools.bankWizardList
							.ref_items.splice(index,1);
					}
					me.row.remove();
				}
			});
		});
	}
};
