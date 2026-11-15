// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

// Fields of the login dialog, in this order; a section starts at each label
const KEFIYA_LOGIN_DIALOG_FIELDS = [
	[__("Bank connection"), ["login_name", "fints_url", "blz", "fints_login", "fints_password"]],
	[null, ["enable_tan_authentication"]],
	[__("ERPNext"), ["company", "erpnext_account", "bank_account", "allowed_sync_days_in_past"]],
];

kefiya.hub.register_view(
	new (class ConnectView {
		constructor() {
			this.key = "connect";
			this.label = __("Bank logins");
			// one FinTS session per login, so its socket handlers are reused
			this.sessions = {};
		}

		actions(page, hub) {
			page.set_primary_action(__("New login"), () => this.login_dialog(hub), "add");
		}

		bind($container, hub) {
			const login = (e) => $(e.currentTarget).closest("[data-login]").attr("data-login");
			$container.on("click", "[data-action]", (e) => {
				e.preventDefault();
				const name = login(e);
				({
					load: () => this.load_accounts(name, hub),
					reset: () => this.reset_connection(name, hub),
					import: () => kefiya.hub.views.imports.import_dialog(hub, name),
					new: () => this.login_dialog(hub),
				})[$(e.currentTarget).attr("data-action")]();
			});
			// the full form opens in another tab; show its changes on coming back
			$(window).on("focus", () => {
				if (hub.current === this.key && frappe.get_route()[0] === "kefiya-hub") hub.refresh();
			});
		}

		render($container, hub) {
			if (!hub.data) return;
			const logins = hub.data.logins;
			$container.html(
				logins.length
					? `<div class="kefiya-login-cards">${logins.map((l) => this.card(l)).join("")}</div>`
					: `<div class="frappe-card kefiya-section kefiya-empty">
						<p class="text-muted">${__("No bank connection yet.")}</p>
						<button class="btn btn-primary btn-sm" data-action="new">${__("New login")}</button>
					</div>`
			);
		}

		card(l) {
			const esc = frappe.utils.escape_html;
			const status = kefiya.hub.LOGIN_INDICATOR[l.status];
			const logo = l.bank_logo
				? `<img class="kefiya-bank-logo" src="${encodeURI(l.bank_logo)}" alt="">`
				: `<span class="kefiya-bank-logo kefiya-bank-initial">${esc(l.name.charAt(0))}</span>`;
			const row = (label, value) => `<div class="kefiya-card-row">
				<span class="text-muted">${label}</span><span>${value}</span>
			</div>`;
			const schedule = l.schedule
				? `${__(l.schedule.import_frequency)}${l.schedule.hour ? " " + l.schedule.hour + ":00" : ""}${
						l.schedule.active ? "" : ` (${__("paused")})`
				  }`
				: __("Manual");
			const btn = (action, label, primary) =>
				`<button class="btn btn-xs ${primary ? "btn-primary" : "btn-default"}" data-action="${action}">${label}</button>`;

			return `<div class="frappe-card kefiya-login-card" data-login="${esc(l.name)}">
				<div class="kefiya-login-head">
					${logo}
					<div class="kefiya-login-title">
						<div class="kefiya-section-title">${esc(l.name)}</div>
						<div class="text-muted small">${esc(l.account_iban || __("No account chosen"))}</div>
					</div>
					<span class="indicator-pill ${status.color}">${status.label}</span>
				</div>
				${
					l.bank_account
						? ""
						: `<div class="kefiya-hint">${__("Not linked to an ERPNext Bank Account, so nothing can be imported.")}</div>`
				}
				${l.failed_connection ? `<div class="kefiya-hint kefiya-hint-error">${__("{0} failed connection attempts. Check the login data, then load the accounts again.", [l.failed_connection])}</div>` : ""}
				<div class="kefiya-card-rows">
					${row(__("Company"), esc(l.company || ""))}
					${row(__("Bank Account"), esc(l.bank_account || "–"))}
					${row(__("Imported until"), l.last_import ? frappe.datetime.str_to_user(l.last_import.to_date) : __("Never"))}
					${row(__("Schedule"), schedule)}
					${row(__("TAN"), l.enable_tan_authentication ? __("On") : __("Off"))}
				</div>
				<div class="kefiya-card-actions">
					${btn("load", __("Load accounts"), !l.account_iban)}
					${btn("import", __("Import"), !!l.account_iban)}
					${l.has_client_state ? btn("reset", __("Reset connection")) : ""}
					<a class="btn btn-xs btn-link" href="/app/kefiya-login/${encodeURIComponent(l.name)}" target="_blank" rel="noopener">${__("Open full form")}</a>
				</div>
			</div>`;
		}

		session(name, hub) {
			if (!this.sessions[name]) {
				this.sessions[name] = new kefiya.hub.FintsSession({
					scope: name,
					kefiya_login: name,
					// after a TAN the server answers with the accounts
					on_tan_resolved: (r) => r.accounts && this.choose_account(name, r.accounts, hub),
				});
			}
			return this.sessions[name];
		}

		/** Same steps as "Load Accounts" in the Kefiya Login form. */
		async load_accounts(name, hub) {
			const { ok, message } = await this.session(name, hub).run({
				method: "kefiya.utils.client.get_accounts",
				args: { kefiya_login: name, user_scope: name },
			});
			if (!ok) {
				await frappe.call({
					method: "kefiya.kefiya.page.kefiya_hub.kefiya_hub.record_failed_connection",
					args: { kefiya_login: name },
				});
				hub.refresh();
				return;
			}
			// nothing back: the bank asked for a TAN, the prompt comes by socket
			if (message && message.accounts) this.choose_account(name, message.accounts, hub);
		}

		choose_account(name, accounts, hub) {
			// new controller: dicts with iban; legacy: tuples; or plain strings
			const ibans = accounts
				.map((x) => (x && x.iban) || (Array.isArray(x) ? x[0] : x))
				.filter(Boolean);
			if (!ibans.length) {
				frappe.msgprint(__("The bank returned no accounts for this login."));
				return;
			}
			const current = (hub.data.logins.find((l) => l.name === name) || {}).account_iban;
			const dialog = new frappe.ui.Dialog({
				title: __("Choose Bank Account"),
				fields: [
					{
						fieldtype: "Select",
						fieldname: "account_iban",
						label: __("Account IBAN"),
						options: ibans,
						default: ibans.includes(current) ? current : ibans[0],
						reqd: 1,
					},
				],
				primary_action_label: __("Save"),
				primary_action: async (values) => {
					await frappe.db.set_value("Kefiya Login", name, {
						account_iban: values.account_iban,
						iban_list: JSON.stringify(ibans),
					});
					dialog.hide();
					hub.refresh();
				},
			});
			dialog.show();
		}

		reset_connection(name, hub) {
			frappe.confirm(
				__("Reset the stored connection of {0}? The bank will ask for a TAN again.", [
					frappe.utils.escape_html(name),
				]),
				async () => {
					await frappe.call({
						method: "run_doc_method",
						args: { dt: "Kefiya Login", dn: name, method: "reset_connection" },
					});
					frappe.show_alert({ message: __("Connection reset"), indicator: "green" });
					hub.refresh();
				}
			);
		}

		/** New login without leaving the page; existing ones open in the full form. */
		async login_dialog(hub) {
			await frappe.model.with_doctype("Kefiya Login");

			const fields = [];
			KEFIYA_LOGIN_DIALOG_FIELDS.forEach(([section, fieldnames]) => {
				fields.push({ fieldtype: "Section Break", label: section });
				fieldnames.forEach((fieldname) => {
					const df = { ...frappe.meta.get_docfield("Kefiya Login", fieldname) };
					delete df.depends_on;
					fields.push(df);
				});
			});

			const dialog = new frappe.ui.Dialog({
				title: __("New Kefiya Login"),
				size: "large",
				fields,
				primary_action_label: __("Save"),
				primary_action: async (values) => {
					await frappe.call({
						method: "frappe.client.insert",
						args: { doc: { doctype: "Kefiya Login", ...values } },
						freeze: true,
					});
					dialog.hide();
					frappe.show_alert({ message: __("Saved"), indicator: "green" });
					hub.refresh();
				},
			});

			const company = () => dialog.get_value("company");
			dialog.fields_dict.erpnext_account.get_query = () => ({
				filters: { account_type: "Bank", company: company(), is_group: 0 },
			});
			dialog.fields_dict.bank_account.get_query = () => ({
				filters: { company: company(), is_company_account: 1 },
			});

			dialog.set_value("company", hub.company);
			dialog.show();
		}
	})()
);
