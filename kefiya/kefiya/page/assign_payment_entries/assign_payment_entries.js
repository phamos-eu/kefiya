// Copyright (c) 2019, jHetzer and contributors
// For license information, please see license.txt
frappe.provide("kefiya.tools");

frappe.pages["assign_payment_entries"].on_page_load = function (wrapper) {
	kefiya.tools.assignWizardObj = new kefiya.tools.assignWizard(
		wrapper
	);
};

kefiya.tools.assignWizard = class assignWizard {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Assign Payment Wizard"),
			single_column: true,
		});
		this.parent = wrapper;
		this.page = this.parent.page;
		this.make();
	}

	make() {
		const me = this;

		me.clear_page_content();
		me.make_assignWizard_tool();
		me.add_actions();
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

	make_assignWizard_tool() {
		const me = this;
		// ensure that the metadata for the "Sales Invoice" DocType is loaded before proceeding with the wizard setup(AssignWizardTool).

		frappe.model.with_doctype("Sales Invoice", () => {
			kefiya.tools.assignWizardList =
				new kefiya.tools.AssignWizardTool({
					parent: me.parent,
					doctype: "Sales Invoice",
					page_title: __(me.page.title),
				});
			frappe.pages["assign_payment_entries"].refresh =
				function (/* wrapper */) {
					window.location.reload(false);
				};
		});
	}
};

kefiya.tools.AssignWizardTool = class AssignWizardTool extends (
	frappe.views.BaseList
) {
	constructor(opts) {
		super(opts);
		this.show();
	}

	// It establishes default settings for various aspects of the assignment wizard tool's data display and behavior
	setup_defaults() {
		super.setup_defaults();
		this.page_length = 100;
		this.sort_by = "customer";
		this.sort_order = "asc";
		this.fields = [
			"name",
			"customer",
			"outstanding_amount",
			"posting_date",
			"due_date",
			"currency",
			"paid_amount",
		];
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

		return Object.assign({}, args, {
			...args.filters.push(
				["Sales Invoice", "docstatus", "=", 1],
				["Sales Invoice", "outstanding_amount", ">", 0]
			),
		});
	}

	get_row_call_args(customer, optionValue) {
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
				party: customer,
			};
			order_by = "posting_date";
		} else if (optionValue == "Bank Transaction") {
			doctype = "Bank Transaction";
			fields = ["name", "party", "date", "unallocated_amount", "description"];
			filters = {
				docstatus: 1,
				party: customer,
				unallocated_amount: [">", 0],
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
		const selectedOption = await frappe.call({
			method: "frappe.client.get_value",
			args: {
				doctype: "Kefiya Settings",
				filters: {},
				fieldname: "show_entries_in_payment_assignment_wizard",
			},
		});

		// Extract the selected value from "Kefiya Settings" doctype
		const optionValue =
			selectedOption.message.show_entries_in_payment_assignment_wizard;

		const me = this;
		this.$result.find(".list-row-container").remove();
		$('[data-fieldname="name"]').remove();
		$('[data-fieldname="status"]').remove();
		$('[data-fieldname="title"]').remove();
		let index = 0;
		let rowHTML;
		// me.data - list of sales invoice. the below code fetchs all payment entries associated with single sales invoice

		for (const value of me.data) {
			rowHTML =
				index % 2 !== 0
					? '<div class="list-row-container" style="background-color: #F3F3F3">'
					: '<div class="list-row-container">';

			const r = await frappe.call(
				this.get_row_call_args(value.customer, optionValue)
			);

			//r.message - list of all payment entries
			if (Array.isArray(r.message) && r.message.length) {
				const row = $(rowHTML).data("data", value).appendTo(me.$result).get(0);

				new kefiya.tools.AssignWizardRow(
					row,
					value,
					r.message,
					optionValue
				);
			}
			index += 1;
		}
	}

	render_header() {
		const me = this;
		if ($(this.wrapper).find(".payment-assign-wizard-header").length === 0) {
			me.$result.append(frappe.render_template("sale_invoice_header"));
		}
	}
};

kefiya.tools.AssignWizardRow = class AssignWizardRow {
	constructor(row, data, payments, optionValue) {
		this.data = data;
		this.data.payments = payments;
		this.data.optionValue = optionValue;
		this.row = row;
		this.make();
		this.bind_events();
	}

	make() {
		$(this.row).append(frappe.render_template("sale_invoice_row", this.data));
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
			const sales_invoice_name = me.data.name;
			const bank_transaction_name = $(this).attr("data-name");
			
			frappe.call({
				method: "kefiya.utils.client.add_sales_invoice_payment",
				args: {
					bank_transaction_name: bank_transaction_name,
					sales_invoice_name: sales_invoice_name,
				},
				callback: function (r) {
					let vouchers = [];

					const paid_amount = r.message;

					vouchers.push({
						payment_doctype: "Sales Invoice",
						payment_name: sales_invoice_name,
						// Raw number, not format_currency(): reconcile_vouchers
						// runs flt() over this, and a localised string like
						// "1.234,56 €" reduces to a nonsense allocation.
						amount: paid_amount,
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
	}
};

function get_doc_link(doctype, name) {
	return '<a href="#Form/' + doctype + "/" + name + '"><b>' + name + `</b></a>`;
}
