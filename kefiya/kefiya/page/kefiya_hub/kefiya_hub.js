// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
frappe.provide("kefiya.hub");

// The views, the FinTS session and the statement wizard come in
// kefiya_hub.bundle.js (kefiya/public/js), loaded when the page opens.

// Sidebar: section title (none for the first) and its view keys
const KEFIYA_HUB_SIDEBAR = [
	[null, ["overview"]],
	[__("Connect"), ["connect", "bank_account_wizard"]],
	[__("Import"), ["imports", "schedule"]],
	[__("Reconcile"), ["bank_transaction_wizard", "assign_payments"]],
	[__("Settings"), ["settings"]],
];

frappe.pages["kefiya-hub"].on_page_load = function (wrapper) {
	frappe.require("kefiya_hub.bundle.js", () => {
		kefiya.hub.instance = new kefiya.hub.KefiyaHub(wrapper);
		// The user may have left the page while the bundle loaded
		if (frappe.get_route()[0] === "kefiya-hub") kefiya.hub.show();
	});
};

// The first show comes before the bundle has loaded; the load shows it then
frappe.pages["kefiya-hub"].on_page_show = function () {
	if (kefiya.hub.instance) kefiya.hub.show();
};

kefiya.hub.show = () => {
	kefiya.hub.instance.show_route();
	kefiya.hub.instance.refresh();
};

kefiya.hub.KefiyaHub = class KefiyaHub {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Kefiya"),
			single_column: false,
		});
		$(wrapper).addClass("kefiya-hub-page");
		this.$body = $('<div class="kefiya-hub"></div>').appendTo(this.page.main);
		this.$views = {};
		this.make_sidebar();
		this.make_filters();
		this.bind_events();

		// Moving between views stays on this page, so Frappe sends no "show"
		frappe.router.on("change", () => {
			if (frappe.get_route()[0] === "kefiya-hub") this.show_route();
		});
	}

	make_sidebar() {
		const item = (key) => {
			const view = kefiya.hub.views[key];
			return `<a class="kefiya-sidebar-item" href="/app/kefiya-hub/${key}" data-sidebar-view="${key}">
				<span class="kefiya-sidebar-label">${view.label}</span>
				<span class="kefiya-sidebar-count hidden" data-count="${key}"></span>
			</a>`;
		};
		this.$sidebar = $(`<div class="kefiya-sidebar">
			${KEFIYA_HUB_SIDEBAR.map(
				([title, keys]) => `<div class="kefiya-sidebar-section">
					${title ? `<div class="kefiya-sidebar-title">${title}</div>` : ""}
					${keys.map(item).join("")}
				</div>`
			).join("")}
		</div>`).appendTo(this.page.sidebar);
	}

	update_sidebar() {
		const d = this.data;
		const failed = d.logins.filter((l) => l.failed_connection).length;
		const counts = {
			connect: failed && { value: failed, color: "red", title: __("Failing logins") },
			bank_transaction_wizard: d.steps.open_count && {
				value: d.steps.open_count,
				color: "orange",
				title: __("Open transactions"),
			},
		};
		this.$sidebar.find("[data-count]").each((_, el) => {
			const count = counts[$(el).attr("data-count")];
			$(el)
				.attr("class", `kefiya-sidebar-count ${count ? count.color : "hidden"}`)
				.attr("title", count ? count.title : "")
				.text(count ? count.value : "");
		});
	}

	make_filters() {
		this.company_field = this.page.add_field({
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Select",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.refresh(),
		});
		this.companies = frappe.db
			.get_list("Company", { pluck: "name", order_by: "name asc", limit: 0 })
			.then((companies) => {
				this.company_field.df.options = companies;
				this.company_field.refresh();
				const current = this.company_field.get_value();
				this.company_field.set_value(companies.includes(current) ? current : companies[0]);
			});
	}

	bind_events() {
		frappe.ui.keys.add_shortcut({
			shortcut: "ctrl+i",
			action: () => kefiya.hub.views.imports.import_dialog(this),
			page: this.page,
			description: __("Import bank transactions"),
		});

		this.$body.on("click", "[data-view]", (e) => {
			e.preventDefault();
			frappe.set_route("kefiya-hub", $(e.currentTarget).attr("data-view"));
		});
		this.$body.on("click", "[data-route]", (e) => {
			e.preventDefault();
			frappe.set_route(...$(e.currentTarget).attr("data-route").split("|"));
		});
	}

	get company() {
		return this.company_field.get_value();
	}

	/** Show the view named in the route, /app/kefiya-hub/<view>. */
	show_route() {
		const key = frappe.get_route()[1];
		this.show_view(kefiya.hub.views[key] ? key : "overview");
	}

	show_view(key) {
		if (key === this.current) return;
		this.current = key;
		const view = kefiya.hub.views[key];

		this.page.set_title(key === "overview" ? __("Kefiya") : view.label);
		this.$sidebar
			.find(".kefiya-sidebar-item")
			.removeClass("selected")
			.filter(`[data-sidebar-view="${key}"]`)
			.addClass("selected");

		this.page.clear_primary_action();
		this.page.clear_secondary_action();
		this.page.clear_menu();
		this.page.clear_inner_toolbar();
		this.page.set_secondary_action(__("Refresh"), () => this.refresh(), "refresh");
		view.actions && view.actions(this.page, this);

		// each view keeps its own container, so its event handlers bind once
		Object.values(this.$views).forEach(($v) => $v.addClass("hidden"));
		if (!this.$views[key]) {
			this.$views[key] = $(`<div class="kefiya-view" data-view-key="${key}"></div>`).appendTo(
				this.$body
			);
			view.bind && view.bind(this.$views[key], this);
		}
		this.$views[key].removeClass("hidden");
		this.render_current();
	}

	render_current() {
		const view = kefiya.hub.views[this.current];
		// views that need the overview data wait for it
		if (!this.data && view.key === "overview") return;
		view.render(this.$views[this.current], this);
	}

	async refresh() {
		// the first refresh waits for the company options and default
		await this.companies;
		return frappe.call({
			method: "kefiya.kefiya.page.kefiya_hub.kefiya_hub.get_overview",
			args: { company: this.company },
			freeze: !this.data,
			callback: (r) => {
				this.data = r.message;
				this.update_sidebar();
				this.render_current();
			},
		});
	}
};
