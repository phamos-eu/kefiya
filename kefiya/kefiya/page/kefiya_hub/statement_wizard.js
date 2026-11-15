// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

/**
 * Import a bank statement file in three steps, all in one dialog:
 * 1. Map columns: which column of the file goes to which field (a template fits most files)
 * 2. Check result: every row as it will be imported, and Bank Transactions or Journal Entries
 * 3. Import: runs in the background; progress and the outcome are shown here
 *
 * Everything from the file is third-party text (payer names, purposes), so it is escaped.
 */
// Attributes that identify a control of the wizard across redraws
const FOCUS_KEYS = ["data-map-target", "data-description", "data-fee", "data-setting", "name"];

kefiya.hub.StatementWizard = class StatementWizard {
	constructor({ docname, company, on_done }) {
		this.docname = docname;
		this.company = company;
		this.on_done = on_done;
		this.step = 1;
		this.import_as = "Bank Transaction";
		this.row_accounts = {};
		this.started = false;
		this.make_dialog();
		this.load_preview({});
	}

	// ---- dialog and steps ----

	make_dialog() {
		this.dialog = new frappe.ui.Dialog({
			title: __("Import bank statement"),
			size: "extra-large",
			fields: [{ fieldtype: "HTML", fieldname: "body" }],
			primary_action: () => this.next(),
			secondary_action_label: __("Back"),
			secondary_action: () => this.back(),
		});
		this.$body = this.dialog.fields_dict.body.$wrapper.addClass("kefiya-wizard");
		this.dialog.onhide = () => this.cleanup();
		this.bind();
		this.dialog.show();
	}

	set_buttons() {
		const labels = {
			1: __("Check result"),
			2: this.import_label(),
			3: __("Close"),
		};
		this.dialog.set_primary_action(labels[this.step], () => this.next());
		this.dialog.get_secondary_btn().toggleClass("hidden", this.step !== 2);
	}

	import_label() {
		const count = (this.preview && this.preview.totals && this.preview.totals.New) || 0;
		return this.import_as === "Journal Entry"
			? __("Create {0} Journal Entries", [count])
			: __("Create {0} Bank Transactions", [count]);
	}

	next() {
		if (this.step === 1) {
			if (!this.can_continue()) return;
			this.step = 2;
		} else if (this.step === 2) {
			return this.start_import();
		} else {
			return this.dialog.hide();
		}
		this.render();
	}

	back() {
		if (this.step === 2) {
			this.step = 1;
			this.render();
		}
	}

	can_continue() {
		const p = this.preview;
		if (!p || p.mapping_error) {
			frappe.show_alert({ message: __("Fix the column mapping first."), indicator: "orange" });
			return false;
		}
		return true;
	}

	stepper() {
		const steps = [__("Map columns"), __("Check result"), __("Import")];
		return `<div class="kefiya-stepper kefiya-wizard-stepper">${steps
			.map((label, i) => {
				const n = i + 1;
				const cls = n < this.step ? "kefiya-step-done" : "";
				return `${i ? '<span class="kefiya-step-line"></span>' : ""}
					<span class="kefiya-step" ${n === this.step ? 'aria-current="step"' : ""}>
						<span class="kefiya-step-number ${cls}">${n}</span>
						<span class="kefiya-step-label">${label}</span>
					</span>`;
			})
			.join("")}</div>`;
	}

	render() {
		// a live refresh redraws the step; keyboard focus stays on the same control
		const focused = document.activeElement && this.$body[0].contains(document.activeElement)
			? FOCUS_KEYS.map((key) => [key, document.activeElement.getAttribute(key)]).find(([, v]) => v !== null)
			: null;
		const views = { 1: () => this.render_mapping(), 2: () => this.render_check(), 3: () => this.render_import() };
		this.$body.html(this.stepper() + `<div class="kefiya-wizard-step">${views[this.step]()}</div>`);
		this.after_render(focused);
		this.set_buttons();
	}

	// ---- server ----

	call(method, args) {
		return frappe
			.call({
				method: "run_doc_method",
				args: { dt: "Kefiya Bank Statement Import", dn: this.docname, method, args },
			})
			.then((r) => r.message);
	}

	async load_preview(args) {
		this.loading = true;
		let preview;
		try {
			preview = await this.call("get_preview", { ...args, import_as: this.import_as });
		} catch (e) {
			preview = null;
		}
		this.loading = false;
		if (!preview) {
			// Frappe has shown the server's message; the step says the file could not be read
			this.preview_failed = !this.preview;
			if (this.step !== 3) this.render();
			return;
		}
		this.preview_failed = false;
		this.preview = preview;
		this.mapping = preview.mapping;
		this.template = preview.template;
		// overrides belong to the rows of one mapping
		const ids = new Set(preview.rows.map((r) => r.transaction_id));
		Object.keys(this.row_accounts).forEach((id) => ids.has(id) || delete this.row_accounts[id]);
		if (this.step !== 3) this.render();
	}

	refresh_preview() {
		clearTimeout(this.refresh_timer);
		this.refresh_timer = setTimeout(() => {
			this.load_preview({ mapping: this.mapping_from_controls(), template: this.template });
		}, 300);
	}

	// ---- step 1: map columns ----

	render_mapping() {
		const p = this.preview;
		if (!p) {
			return this.preview_failed
				? `<div class="kefiya-hint" role="alert">${__("The file could not be read.")}</div>`
				: `<div class="text-muted">${__("Reading the file…")}</div>`;
		}
		const m = this.mapping;
		const esc = frappe.utils.escape_html;
		const header = p.file.header;
		const columns = (selected) =>
			`<option value="">${__("Not imported")}</option>` +
			header
				.map((c) => `<option value="${esc(c)}" ${c === selected ? "selected" : ""}>${esc(c)}</option>`)
				.join("");
		const select = (name, options, label) =>
			`<label class="kefiya-wizard-setting"><span class="text-muted">${label}</span>
				<select class="form-control input-xs" data-setting="${name}">${options}</select></label>`;
		const option = (value, label, selected) =>
			`<option value="${esc(String(value))}" ${String(value) === String(selected) ? "selected" : ""}>${esc(label)}</option>`;

		const templates =
			`<option value="">${__("None")}</option>` +
			p.templates
				.map((t) => option(t.key, t.builtin ? __("{0} (built-in)", [t.label]) : t.label, this.template))
				.join("");
		const delimiters = [
			[",", __("Comma")],
			[";", __("Semicolon")],
			["\t", __("Tab")],
			["|", __("Pipe")],
		];
		const settings = `<div class="kefiya-wizard-settings">
			${select("template", templates, __("Template"))}
			${select("delimiter", delimiters.map(([v, l]) => option(v, l, p.file.delimiter)).join(""), __("Separator"))}
			<label class="kefiya-wizard-setting"><span class="text-muted">${__("Header row")}</span>
				<input type="number" min="1" max="51" class="form-control input-xs" data-setting="header_row"
					value="${p.file.header_row + 1}"></label>
			${select(
				"number_format",
				Object.entries(p.options.number_formats).map(([v, l]) => option(v, l, m.number_format)).join(""),
				__("Numbers")
			)}
			${select(
				"date_format",
				p.options.date_formats.map((f) => option(f, this.date_example(f), m.date_format)).join(""),
				__("Dates")
			)}
		</div>`;

		const target_row = (target, label, selected) => `<tr>
			<td>${esc(__(label))}${["date", "amount"].includes(target) ? ' <span class="text-danger">*</span>' : ""}</td>
			<td><select class="form-control input-xs" data-map-target="${target}" aria-label="${esc(__(label))}">${columns(selected)}</select></td>
			<td class="text-muted kefiya-wizard-sample">${this.samples(selected)}</td>
		</tr>`;
		const description = m.description.columns.concat(["", ""]).slice(0, Math.max(3, m.description.columns.length + 1));
		const fee = m.fee || {};
		const mapping_rows =
			Object.entries(p.options.targets)
				.map(([target, label]) => target_row(target, label, m.columns[target]))
				.join("") +
			description
				.map(
					(c, i) => `<tr>
				<td>${i ? __("Description, continued") : __("Description")}</td>
				<td><select class="form-control input-xs" data-description="${i}">${columns(c)}</select></td>
				<td class="text-muted kefiya-wizard-sample">${this.samples(c)}</td></tr>`
				)
				.join("") +
			`<tr><td>${__("Fee (own transaction)")}</td>
				<td><select class="form-control input-xs" data-fee="column">${columns(fee.column)}</select></td>
				<td class="text-muted kefiya-wizard-sample">${this.samples(fee.column)}</td></tr>
			<tr class="${fee.column ? "" : "hidden"}" data-fee-texts><td></td><td colspan="2">
				<div class="kefiya-wizard-settings">
					<label class="kefiya-wizard-setting"><span class="text-muted">${__("Fee party name")}</span>
						<input class="form-control input-xs" data-fee="party_name" maxlength="140" value="${esc(fee.party_name || "")}"></label>
					<label class="kefiya-wizard-setting"><span class="text-muted">${__("Fee description ({id} = transaction ID)")}</span>
						<input class="form-control input-xs" data-fee="description" maxlength="140" value="${esc(fee.description || "Fee {id}")}"></label>
				</div></td></tr>`;

		const filters = (m.filters || []).length
			? `<p class="text-muted small">${__("Rows left out")}: ${m.filters
					.map((f) =>
						f.op === "equals"
							? __("{0} is {1}", [esc(f.column), esc(f.value)])
							: __("{0} is not {1}", [esc(f.column), esc(f.value)])
					)
					.join("; ")}</p>`
			: "";

		return `${settings}
			${this.status_line()}
			<div class="kefiya-wizard-columns">
				<table class="table table-sm kefiya-table kefiya-wizard-mapping">
					<thead><tr><th>${__("Field")}</th><th>${__("Column in the file")}</th><th>${__("First values")}</th></tr></thead>
					<tbody>${mapping_rows}</tbody>
				</table>
				${filters}
				<div class="kefiya-wizard-file-title text-muted small">${__("The file ({0} rows below the header)", [p.file.row_count])}</div>
				${this.file_table()}
			</div>`;
	}

	date_example(format) {
		// 31 December 2026 in this format, so the choice needs no strftime knowledge
		const parts = { "%d": "31", "%m": "12", "%Y": "2026", "%y": "26" };
		return format.replace(/%[dmYy]/g, (t) => parts[t]);
	}

	samples(column) {
		if (!column || !this.preview) return "";
		const i = this.preview.file.header.indexOf(column);
		if (i < 0) return `<span class="text-danger">${__("not in this file")}</span>`;
		return this.preview.file.sample
			.map((r) => (r.cells[i] || "").trim())
			.filter(Boolean)
			.slice(0, 3)
			.map((v) => frappe.utils.escape_html(v.length > 40 ? v.slice(0, 40) + "…" : v))
			.join(" · ");
	}

	file_table() {
		const esc = frappe.utils.escape_html;
		const { header, sample } = this.preview.file;
		const cell = (v) => {
			const text = (v || "").trim();
			return `<td title="${esc(text)}">${esc(text.length > 30 ? text.slice(0, 30) + "…" : text)}</td>`;
		};
		return `<div class="kefiya-wizard-scroll"><table class="table table-sm table-bordered kefiya-wizard-file">
			<thead><tr><th>${__("Line")}</th>${header.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
			<tbody>${sample
				.slice(0, 8)
				.map((r) => `<tr><td class="text-muted">${r.line}</td>${header.map((_, i) => cell(r.cells[i])).join("")}</tr>`)
				.join("")}</tbody>
		</table></div>`;
	}

	status_line() {
		const p = this.preview;
		if (p.mapping_error) {
			return `<div class="kefiya-hint" role="alert">${frappe.utils.escape_html(p.mapping_error)}</div>`;
		}
		const t = p.totals;
		const parts = [__("{0} new", [t.New])];
		if (t.Duplicate) parts.push(__("{0} imported before", [t.Duplicate]));
		if (t.Error) parts.push(`<span class="text-danger">${__("{0} cannot be read", [t.Error])}</span>`);
		if (t.Skipped) parts.push(__("{0} with amount zero", [t.Skipped]));
		return `<p class="kefiya-wizard-status">${parts.join(" · ")}</p>`;
	}

	mapping_from_controls() {
		const $b = this.$body;
		const value = (sel) => $b.find(sel).val();
		const columns = {};
		$b.find("[data-map-target]").each((_, el) => {
			if (el.value) columns[$(el).attr("data-map-target")] = el.value;
		});
		const description_columns = $b
			.find("[data-description]")
			.map((_, el) => el.value)
			.get()
			.filter(Boolean);
		const old = this.mapping;
		// a built-in text format only fits the columns it was made for
		const same = JSON.stringify(description_columns) === JSON.stringify(old.description.columns);
		const mapping = {
			...old,
			delimiter: value('[data-setting="delimiter"]'),
			header_row: Math.max(0, parseInt(value('[data-setting="header_row"]'), 10) - 1 || 0),
			number_format: value('[data-setting="number_format"]'),
			date_format: value('[data-setting="date_format"]'),
			columns,
			description: { columns: description_columns, formatter: same ? old.description.formatter : "joined" },
		};
		const fee_column = value('[data-fee="column"]');
		if (fee_column) {
			mapping.fee = {
				column: fee_column,
				party_name: value('[data-fee="party_name"]') || "",
				description: value('[data-fee="description"]') || "Fee {id}",
				reference: (old.fee && old.fee.reference) || "{id}",
			};
		} else {
			delete mapping.fee;
		}
		return mapping;
	}

	// ---- step 2: check result ----

	render_check() {
		const p = this.preview;
		const t = p.totals;
		const je = this.import_as === "Journal Entry";
		const date = (d) => (d ? frappe.datetime.str_to_user(d) : "");
		const money = (v) => format_currency(v, this.currency());
		const warning = t.other_transactions_in_period
			? `<div class="kefiya-hint">${__(
					"The bank account has {0} other Bank Transactions between {1} and {2} that no row of this file matches, for example from a FinTS import. Check that these rows are not booked twice.",
					[t.other_transactions_in_period, date(t.from_date), date(t.to_date)]
			  )}</div>`
			: "";
		return `${warning}
			<div class="kefiya-wizard-target" role="radiogroup" aria-label="${__("Create")}">
				<span class="text-muted">${__("Create")}</span>
				${[
					["Bank Transaction", __("Bank Transactions"), __("to reconcile against invoices and payments later")],
					["Journal Entry", __("Journal Entries"), __("booked against a counter account right away")],
				]
					.map(
						([value, label, hint]) => `<label class="kefiya-wizard-choice">
						<input type="radio" name="kefiya_import_as" value="${value}" ${this.import_as === value ? "checked" : ""}>
						<span><b>${label}</b> <span class="text-muted">${hint}</span></span></label>`
					)
					.join("")}
			</div>
			<div class="kefiya-wizard-accounts ${je ? "" : "hidden"}">
				<div data-control="counter_account"></div>
				<div data-control="fee_account"></div>
			</div>
			<div class="kefiya-wizard-totals">
				${this.status_line()}
				<span>${__("In: {0}", [money(t.deposits)])} · ${__("Out: {0}", [money(t.withdrawals)])}</span>
				<span class="text-muted">${date(t.from_date)} – ${date(t.to_date)}</span>
				<button class="btn btn-xs btn-default" data-save-template>${__("Save mapping as template")}</button>
			</div>
			<div class="kefiya-wizard-scroll kefiya-wizard-rows">${this.rows_table()}</div>
			${p.rows.length < t.rows ? `<p class="text-muted small">${__("Showing the first {0} of {1} rows.", [p.rows.length, t.rows])}</p>` : ""}`;
	}

	currency() {
		const row = this.preview.rows.find((r) => r.currency);
		return row ? row.currency : frappe.defaults.get_default("currency");
	}

	rows_table() {
		const esc = frappe.utils.escape_html;
		const je = this.import_as === "Journal Entry";
		const color = { New: "blue", Duplicate: "gray", Error: "red", Skipped: "gray" };
		const rows = this.preview.rows
			.map((r) => {
				const account = je && r.status === "New" ? this.row_account_cell(r) : "";
				return `<tr class="${r.status === "New" ? "" : "text-muted"}">
					<td><span class="indicator-pill ${color[r.status]}" title="${esc(r.message || "")}">${__(r.status)}</span></td>
					<td class="text-muted">${r.line}</td>
					<td>${r.date ? frappe.datetime.str_to_user(r.date) : ""}</td>
					<td class="text-right ${r.amount < 0 ? "text-danger" : ""}">${
						r.amount !== undefined ? format_currency(r.amount, r.currency || this.currency()) : ""
					}</td>
					<td>${esc(r.party_name || "")}</td>
					<td class="kefiya-wizard-description">${esc(r.status === "Error" ? r.message : r.description || "")}</td>
					<td class="text-muted">${esc(r.reference_number || "")}</td>
					${je ? `<td>${account}</td>` : ""}
				</tr>`;
			})
			.join("");
		return `<table class="table table-sm kefiya-table">
			<thead><tr>
				<th>${__("Status")}</th><th>${__("Line")}</th><th>${__("Date")}</th><th class="text-right">${__("Amount")}</th>
				<th>${__("Party")}</th><th>${__("Description")}</th><th>${__("Reference")}</th>
				${je ? `<th>${__("Counter Account")}</th>` : ""}
			</tr></thead>
			<tbody>${rows}</tbody></table>`;
	}

	row_account_cell(r) {
		const esc = frappe.utils.escape_html;
		const own = this.row_accounts[r.transaction_id];
		const is_fee = r.transaction_id.endsWith("-fee");
		const fallback = (is_fee && this.fee_account) || this.counter_account || __("Choose above");
		return `<button class="btn btn-link btn-xs kefiya-wizard-account ${own ? "" : "text-muted"}"
			data-row-account="${esc(r.transaction_id)}" title="${__("Change the account of this row")}">
			${esc(own || fallback)}</button>`;
	}

	account_filters() {
		return {
			company: this.company,
			is_group: 0,
			disabled: 0,
			account_type: ["not in", ["Receivable", "Payable", "Stock"]],
		};
	}

	make_account_controls() {
		const make = (fieldname, label, description) => {
			const control = frappe.ui.form.make_control({
				parent: this.$body.find(`[data-control="${fieldname}"]`),
				df: {
					fieldtype: "Link",
					fieldname,
					label,
					options: "Account",
					description,
					get_query: () => ({ filters: this.account_filters() }),
					change: () => {
						this[fieldname] = control.get_value();
						this.$body.find(".kefiya-wizard-rows").html(this.rows_table());
					},
				},
				render_input: true,
			});
			control.set_value(this[fieldname] || "");
			return control;
		};
		make("counter_account", __("Counter Account"), __("For every row without an account of its own"));
		make("fee_account", __("Fee Account"), __("For fee rows; empty: the counter account"));
	}

	change_row_account(transaction_id) {
		const dialog = new frappe.ui.Dialog({
			title: __("Account for this row"),
			fields: [
				{
					fieldtype: "Link",
					fieldname: "account",
					label: __("Account"),
					options: "Account",
					default: this.row_accounts[transaction_id] || "",
					description: __("Leave empty to use the counter account."),
					get_query: () => ({ filters: this.account_filters() }),
				},
			],
			primary_action_label: __("Set"),
			primary_action: ({ account }) => {
				if (account) this.row_accounts[transaction_id] = account;
				else delete this.row_accounts[transaction_id];
				dialog.hide();
				this.$body.find(".kefiya-wizard-rows").html(this.rows_table());
			},
		});
		dialog.show();
	}

	save_template() {
		frappe.prompt(
			{
				fieldtype: "Data",
				fieldname: "template_name",
				label: __("Template Name"),
				reqd: 1,
				default: this.template && !this.template.startsWith("builtin:") ? this.template : "",
			},
			async ({ template_name }) => {
				const r = await frappe.call({
					method: "kefiya.kefiya.doctype.kefiya_bank_statement_import.kefiya_bank_statement_import.save_import_template",
					args: {
						template_name,
						mapping: this.mapping,
						header_columns: this.preview.file.header,
					},
				});
				this.template = r.message;
				frappe.show_alert({ message: __("Template {0} saved", [r.message]), indicator: "green" });
			},
			__("Save mapping as template"),
			__("Save")
		);
	}

	// ---- step 3: import ----

	async start_import() {
		if (this.import_as === "Journal Entry" && !this.counter_account) {
			frappe.show_alert({ message: __("Choose the counter account."), indicator: "orange" });
			return;
		}
		if (!(this.preview.totals.New > 0)) {
			frappe.show_alert({ message: __("There is nothing new to import."), indicator: "orange" });
			return;
		}
		this.listen();
		this.started = true;
		this.step = 3;
		this.progress = { current: 0, total: this.preview.totals.rows };
		this.render();
		try {
			await this.call("start_import", {
				mapping: this.mapping,
				template: this.template,
				import_as: this.import_as,
				counter_account: this.counter_account || null,
				fee_account: this.fee_account || null,
				row_accounts: this.import_as === "Journal Entry" ? this.row_accounts : null,
			});
		} catch (e) {
			// the server has shown why; back to the check step
			this.started = false;
			this.step = 2;
			this.render();
			return;
		}
		this.load_result();
	}

	listen() {
		if (this.listening) return;
		this.listening = true;
		this.on_progress = (data) => {
			if (data.docname !== this.docname) return;
			this.progress = data;
			this.$body.find(".progress-bar").css("width", `${Math.round((100 * data.current) / data.total)}%`);
			this.$body.find("[data-progress-text]").text(__("{0} of {1} rows", [data.current, data.total]));
		};
		this.on_status = (data) => {
			if (data.docname === this.docname && ["Success", "Partial Success", "Error"].includes(data.status)) {
				this.load_result();
			}
		};
		frappe.realtime.on("kefiya_statement_import_progress", this.on_progress);
		frappe.realtime.on("update_import_status", this.on_status);
		// Socket events can be lost (no connection, reconnect); the status is also looked up now and then
		this.poll_timer = setInterval(() => this.step === 3 && !this.result && this.load_result(), 5000);
	}

	async load_result() {
		const doc = await frappe.db.get_doc("Kefiya Bank Statement Import", this.docname);
		if (!["Success", "Partial Success", "Error"].includes(doc.status)) return;
		this.result = doc;
		if (this.step === 3) this.render();
		this.on_done && this.on_done();
	}

	render_import() {
		const doc = this.result;
		if (!doc) {
			const p = this.progress || {};
			const pct = p.total ? Math.round((100 * p.current) / p.total) : 0;
			return `<p>${__("Importing… You can close this window; the import goes on in the background.")}</p>
				<div class="progress"><div class="progress-bar" style="width: ${pct}%"></div></div>
				<p class="text-muted small" data-progress-text></p>`;
		}
		const esc = frappe.utils.escape_html;
		const log = JSON.parse(doc.import_log || "[]");
		const problems = log.filter((r) => r.status !== "Imported");
		const color = { Success: "green", "Partial Success": "orange", Error: "red" };
		const list = doc.import_as === "Journal Entry" ? "journal-entry" : "bank-transaction";
		const filter =
			doc.import_as === "Journal Entry"
				? `kefiya_bank_account=${encodeURIComponent(doc.bank_account)}`
				: `bank_account=${encodeURIComponent(doc.bank_account)}`;
		return `<p><span class="indicator-pill ${color[doc.status]}">${__(doc.status)}</span>
				${__("{0} created, {1} skipped, {2} failed.", [doc.imported_records, doc.skipped_records, doc.failed_records])}
				<a href="/app/${list}?${filter}" target="_blank" rel="noopener">${__("Open the list")}</a></p>
			${
				problems.length
					? `<div class="kefiya-wizard-scroll kefiya-wizard-rows"><table class="table table-sm kefiya-table">
				<thead><tr><th>${__("Line")}</th><th>${__("Status")}</th><th>${__("Amount")}</th><th>${__("Message")}</th></tr></thead>
				<tbody>${problems
					.map(
						(r) => `<tr><td class="text-muted">${r.line || ""}</td><td>${__(r.status)}</td>
							<td>${r.amount !== null && r.amount !== undefined ? format_currency(r.amount, this.currency()) : ""}</td>
							<td>${esc(r.message || "")}</td></tr>`
					)
					.join("")}</tbody></table></div>`
					: ""
			}`;
	}

	// ---- events ----

	bind() {
		this.$body.on("change", "[data-map-target], [data-description], [data-fee], [data-setting]", (e) => {
			const $el = $(e.currentTarget);
			if ($el.attr("data-setting") === "template") {
				this.template = $el.val() || null;
				return this.load_preview(this.template ? { template: this.template } : {});
			}
			if ($el.attr("data-fee") === "column") {
				this.$body.find("[data-fee-texts]").toggleClass("hidden", !$el.val());
			}
			this.refresh_preview();
		});
		this.$body.on("change", 'input[name="kefiya_import_as"]', (e) => {
			this.import_as = e.currentTarget.value;
			this.render();
		});
		this.$body.on("click", "[data-row-account]", (e) =>
			this.change_row_account($(e.currentTarget).attr("data-row-account"))
		);
		this.$body.on("click", "[data-save-template]", () => this.save_template());
	}

	after_render(focused) {
		if (this.step === 2 && this.import_as === "Journal Entry") this.make_account_controls();
		const $restore = focused && this.$body.find(`[${focused[0]}="${CSS.escape(focused[1])}"]`);
		if ($restore && $restore.length) $restore.trigger("focus");
		else if (this.step === 1 && !this.focused_once) {
			this.focused_once = true;
			this.$body.find("select, input").first().trigger("focus");
		}
	}

	cleanup() {
		clearTimeout(this.refresh_timer);
		clearInterval(this.poll_timer);
		if (this.listening) {
			frappe.realtime.off("kefiya_statement_import_progress", this.on_progress);
			frappe.realtime.off("update_import_status", this.on_status);
		}
		// an upload that was never imported leaves nothing behind
		if (!this.started) {
			frappe.call({
				method: "frappe.client.delete",
				args: { doctype: "Kefiya Bank Statement Import", name: this.docname },
			});
		}
		this.on_done && this.on_done();
	}
};
