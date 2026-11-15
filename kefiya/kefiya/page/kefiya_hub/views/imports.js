// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

const STATEMENT_STATUS_COLOR = {
	Queued: "blue",
	"In Progress": "blue",
	Success: "green",
	"Partial Success": "orange",
	Error: "red",
};

kefiya.hub.register_view(
	new (class ImportsView {
		constructor() {
			this.key = "imports";
			this.label = __("Imports");
			// one FinTS session per import, so its socket handlers are reused
			this.sessions = {};
		}

		actions(page, hub) {
			page.set_primary_action(__("Import now"), () => this.import_dialog(hub), "download");
			if (frappe.model.can_create("Kefiya Bank Statement Import")) {
				page.add_inner_button(__("Upload statement"), () => this.statement_dialog(hub));
			}
		}

		bind($container, hub) {
			$container.on("click", "[data-import-row]", (e) => {
				if ($(e.target).closest("button, a").length) return;
				const row = this.rows[$(e.currentTarget).attr("data-import-row")];
				row && this.detail_dialog(row);
			});
			$container.on("click", "[data-run-import]", (e) => {
				const row = this.rows[$(e.currentTarget).closest("[data-import-row]").attr("data-import-row")];
				this.run_import(hub, row.name, row.kefiya_login);
			});
		}

		render($container, hub) {
			this.$container = $container;
			frappe.call({
				method: "kefiya.kefiya.page.kefiya_hub.kefiya_hub.get_imports",
				args: { company: hub.company },
				callback: (r) => this.render_list(r.message || []),
			});
		}

		render_list(rows) {
			this.rows = {};
			rows.forEach((d) => (this.rows[d.name] = d));
			const esc = frappe.utils.escape_html;
			const date = (d) => (d ? frappe.datetime.str_to_user(d) : "–");

			const row = (d) => {
				const fints = d.source === "FinTS";
				const status = fints
					? d.docstatus
						? `<span class="indicator-pill green">${__("Imported")}</span>`
						: `<span class="indicator-pill gray">${__("Not run")}</span>`
					: `<span class="indicator-pill ${STATEMENT_STATUS_COLOR[d.status] || "gray"}">${__(d.status || "Not Started")}</span>`;
				const detail = fints
					? `${date(d.from_date)} – ${date(d.to_date)}`
					: __("{0} of {1} records", [d.imported_records || 0, d.payload_count || 0]);
				return `<tr class="kefiya-import-row" data-import-row="${esc(d.name)}">
					<td>${fints ? __("FinTS") : __("Bank statement")}</td>
					<td>${esc(fints ? d.kefiya_login : d.bank_account || "")}</td>
					<td>${detail}</td>
					<td>${status}</td>
					<td class="text-muted">${comment_when(d.creation)}</td>
					<td class="text-right">${
						fints && !d.docstatus
							? `<button class="btn btn-xs btn-default" data-run-import>${__("Run")}</button>`
							: ""
					}</td>
				</tr>`;
			};

			this.$container.html(`<div class="frappe-card kefiya-section">
				${
					rows.length
						? `<table class="table kefiya-table">
							<thead><tr>
								<th>${__("Source")}</th><th>${__("Login / Bank Account")}</th>
								<th>${__("Period / Records")}</th><th>${__("Status")}</th><th></th><th></th>
							</tr></thead>
							<tbody>${rows.map(row).join("")}</tbody>
						</table>`
						: `<div class="kefiya-empty text-muted">${__("Nothing imported yet.")}</div>`
				}
			</div>`);
		}

		detail_dialog(d) {
			const esc = frappe.utils.escape_html;
			const date = (v) => (v ? frappe.datetime.str_to_user(v) : "–");
			const fints = d.source === "FinTS";
			const doctype = fints ? "Kefiya Import" : "Kefiya Bank Statement Import";
			const rows = fints
				? [
						[__("Login"), esc(d.kefiya_login)],
						[__("Requested"), `${date(d.from_date)} – ${date(d.to_date)}`],
						[__("Transactions found"), d.docstatus ? `${date(d.start_date)} – ${date(d.end_date)}` : "–"],
						[__("Data from the bank"), d.file_url ? `<a href="${encodeURI(d.file_url)}" target="_blank" rel="noopener">${__("Download JSON")}</a>` : "–"],
				  ]
				: [
						[__("Bank Account"), esc(d.bank_account || "")],
						[__("Status"), __(d.status || "Not Started")],
						[__("Created as"), __(d.import_as || "Bank Transaction")],
						[__("Records"), __("{0} of {1} imported", [d.imported_records || 0, d.payload_count || 0])],
						[__("File"), d.import_file ? `<a href="${encodeURI(d.import_file)}" target="_blank" rel="noopener">${__("Download")}</a>` : "–"],
				  ];
			rows.push([__("Started by"), esc(frappe.user.full_name(d.owner))]);
			rows.push([__("Created"), frappe.datetime.str_to_user(d.creation)]);

			const dialog = new frappe.ui.Dialog({
				title: fints ? __("FinTS import") : __("Bank statement import"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "details",
						options: `<div class="kefiya-card-rows">${rows
							.map(([label, value]) => `<div class="kefiya-card-row"><span class="text-muted">${label}</span><span>${value}</span></div>`)
							.join("")}</div>
							<p class="mt-3 mb-0"><a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(d.name)}" target="_blank" rel="noopener">${__("Open full form")}</a></p>`,
					},
				],
			});
			dialog.show();
		}

		/** Ask for login and dates, then create the Kefiya Import and run it here. */
		import_dialog(hub, kefiya_login) {
			const today = frappe.datetime.get_today();
			const last_import = (login) => {
				const l = hub.data && hub.data.logins.find((x) => x.name === login);
				return l && l.last_import && l.last_import.to_date;
			};
			// continue where the last import ended; Kefiya Import allows 90 days back
			const default_from = (login) => {
				const oldest = frappe.datetime.add_days(today, -90);
				const from = last_import(login) || frappe.datetime.add_days(today, -7);
				return from < oldest ? oldest : from;
			};

			const dialog = new frappe.ui.Dialog({
				title: __("Import bank transactions"),
				fields: [
					{
						fieldname: "kefiya_login",
						label: __("Kefiya Login"),
						fieldtype: "Link",
						options: "Kefiya Login",
						reqd: 1,
						default: kefiya_login,
						get_query: () => ({ filters: hub.company ? { company: hub.company } : {} }),
						onchange: () => {
							const login = dialog.get_value("kefiya_login");
							if (login) dialog.set_value("from_date", default_from(login));
						},
					},
					{ fieldtype: "Column Break" },
					{
						fieldname: "from_date",
						label: __("From Date"),
						fieldtype: "Date",
						reqd: 1,
						default: default_from(kefiya_login),
					},
					{
						fieldname: "to_date",
						label: __("To Date"),
						fieldtype: "Date",
						reqd: 1,
						default: today,
					},
				],
				primary_action_label: __("Import"),
				primary_action: async (values) => {
					const r = await frappe.call({
						method: "frappe.client.insert",
						args: { doc: { doctype: "Kefiya Import", ...values } },
						freeze: true,
					});
					dialog.hide();
					if (hub.current !== this.key) frappe.set_route("kefiya-hub", this.key);
					this.run_import(hub, r.message.name, values.kefiya_login);
				},
			});
			dialog.show();
		}

		async run_import(hub, name, kefiya_login) {
			if (!this.sessions[name]) {
				this.sessions[name] = new kefiya.hub.FintsSession({ scope: name, kefiya_login });
			}
			const { ok, message } = await this.sessions[name].run({
				method: "kefiya.utils.client.import_fints_transactions",
				args: { kefiya_import: name, kefiya_login, user_scope: name },
			});
			if (ok) {
				const payments = (message && message.payments) || [];
				frappe.show_alert({
					message: payments.length
						? __("{0} new transactions imported", [payments.length])
						: __("No new transactions"),
					indicator: "green",
				});
			}
			// a failed import stays as "Not run" and can be run again
			hub.refresh();
		}

		statement_dialog(hub) {
			const dialog = new frappe.ui.Dialog({
				title: __("Upload bank statement"),
				fields: [
					{
						fieldname: "bank_account",
						label: __("Bank Account"),
						fieldtype: "Link",
						options: "Bank Account",
						reqd: 1,
						get_query: () => ({ filters: { company: hub.company, is_company_account: 1 } }),
					},
					{
						fieldname: "import_file",
						label: __("Statement file (CSV)"),
						fieldtype: "Attach",
						reqd: 1,
					},
					{
						fieldname: "submit_after_success",
						label: __("Submit the created documents"),
						fieldtype: "Check",
						default: 1,
					},
				],
				primary_action_label: __("Next"),
				primary_action: async (values) => {
					const r = await frappe.call({
						method: "frappe.client.insert",
						args: {
							doc: {
								doctype: "Kefiya Bank Statement Import",
								company: hub.company,
								...values,
							},
						},
						freeze: true,
					});
					dialog.hide();
					// columns, rows and target are chosen in the wizard; it runs the import
					new kefiya.hub.StatementWizard({
						docname: r.message.name,
						company: hub.company,
						on_done: () => hub.refresh(),
					});
				},
			});
			dialog.show();
		}
	})()
);
