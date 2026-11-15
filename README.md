<p align="center">
  <img src="kefiya/public/images/kefiya-logo.svg" alt="Kefiya" width="96">
</p>

<h1 align="center">Kefiya</h1>

<p align="center">
  <strong>Your German bank accounts in ERPNext: transactions arrive by themselves, payments match their invoices.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: AGPL-3.0" src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg"></a>
  <img alt="Frappe / ERPNext 15 and 16" src="https://img.shields.io/badge/ERPNext-v15%20%7C%20v16-0089ff">
</p>

---

Kefiya connects ERPNext to your bank through **FinTS** (formerly HBCI), the online banking standard of German banks. It brings your account transactions into ERPNext as Bank Transactions, helps you match them with your invoices, and keeps doing so on a schedule. For banks or payment services without FinTS, you upload the statement file instead.

Kefiya is made for small and medium-sized companies in Germany that keep their books in ERPNext and are tired of typing in bank statements.

## What you get

**Bank connection**
- Connect any bank that offers FinTS with PIN/TAN, which most German banks do.
- TAN confirmation for PSD2, in the browser or in your banking app.
- One page, **Kefiya**, shows every bank connection, when it last imported, and what needs your attention.

**Import**
- Import at the click of a button, or let Kefiya import automatically: daily, weekly or monthly, at the hour you choose, per bank connection.
- Transactions that are already in ERPNext are recognised and not imported twice.
- The data the bank sent is kept with every import, so you can always trace where a booking came from.

**Bank statement files**
- Upload a CSV statement when a bank or service has no FinTS, for example PayPal or a credit card.
- Built-in templates for DKB current accounts, DKB VISA cards and PayPal.
- Any other CSV file can be mapped column by column; save the mapping as a template for next time.
- Import the rows as Bank Transactions, to reconcile later, or as Journal Entries, booked right away.

**Matching and reconciliation**
- Kefiya suggests the Sales Invoice, Purchase Invoice or Journal Entry a payment belongs to, and links them so the invoice shows as paid.
- Book a transaction straight from ERPNext's bank reconciliation, with the party and expense account filled in.

**Payments**
- Create a SEPA credit transfer file (pain.001) from an outgoing Payment Request, to download or send by e-mail.

## How it works

1. **Connect your bank.** Create a Kefiya Login with your bank's FinTS address and your online banking user. Kefiya loads your accounts; confirm with a TAN if the bank asks for one.
2. **Link your accounts.** Choose which ERPNext Bank Account each bank account belongs to.
3. **Import.** Import the last weeks once, then set up a schedule in Kefiya Schedule to keep everything up to date.
4. **Match and reconcile.** Assign the new transactions to their invoices and reconcile them.

The **Kefiya** page in the desk guides you through these steps and shows where each one stands.

## Installation

### On Frappe Cloud

Install **Kefiya** from the Frappe Cloud Marketplace on any site that runs ERPNext. Then open the **Kefiya** page from the apps screen.

### On your own server

Kefiya runs on Frappe and ERPNext version 15 and 16.

```bash
cd ~/frappe-bench
bench get-app kefiya https://github.com/phamos-eu/kefiya --branch version-16
bench --site your-site install-app kefiya
bench --site your-site enable-scheduler
```

Use `--branch version-15` for ERPNext 15. The scheduler is only needed for automatic imports.

## Your data and security

- **Your bank data stays in your ERPNext.** Kefiya talks to your bank directly and sends your transactions to no one else. The only other request it makes is for your bank's logo, from the bank's own website.
- **Passwords are stored encrypted**, using Frappe's password storage, and are never shown again after saving.
- **Only what bookkeeping needs is imported from statement files.** From a PayPal export, for example, Kefiya takes the buyer's name and leaves e-mail address, delivery address and phone number in the file. Upload statement files as private files.
- **Access follows ERPNext's roles.** Who may open the Kefiya page, and see bank logins and transactions, is set with roles like everything else in ERPNext.

## Frequently asked questions

**Does my bank work with Kefiya?**
If your bank offers FinTS (HBCI) with PIN/TAN, it should. Most German banks do. You find your bank's FinTS address on its website or in the bank list of the Deutsche Kreditwirtschaft.

**Why does the bank ask for a TAN again?**
Under PSD2, banks ask you to confirm access with a TAN again from time to time; how often is up to the bank. Kefiya shows when a connection needs a new TAN.

**How far back can I import?**
That depends on your bank. Many banks only hand out the last 90 days without an extra TAN.

**My bank or payment service has no FinTS. What now?**
Export the statement as a CSV file and upload it on the Kefiya page under *Import → Upload statement*. If your file cannot be mapped, [contact us](https://phamos.eu/contact) and we will look at it.

## Support

Kefiya is developed and maintained by [phamos GmbH](https://phamos.eu), an ERPNext partner in Germany.

- Questions, implementation and paid support: [phamos.eu/contact](https://phamos.eu/contact)
- Bugs and feature requests: [GitHub issues](https://github.com/phamos-eu/kefiya/issues)

## Contributing

Pull requests are welcome. Development happens at [git.phamos.eu/phamos/kefiya](https://git.phamos.eu/phamos/kefiya); issues and pull requests opened on GitHub are forwarded there automatically, and we answer on GitHub. By contributing, you agree that your contribution is published under the license of the project, AGPL-3.0.

To run the tests on a development site:

```bash
bench --site your-test-site run-tests --app kefiya
```

## License

Kefiya is free software under the [GNU Affero General Public License, version 3](LICENSE) (AGPL-3.0-only). You may use it, change it and pass it on. If you pass on a changed version, or let others use a changed version over a network, for example on a hosted ERPNext, you must publish your changes under the same license.

The releases up to v16.0.1 (for ERPNext 16) and v0.6.5 (for ERPNext 15) were published under the MIT License and stay available under it; see [NOTICE](NOTICE).

It builds on [python-fints](https://github.com/raphaelm/python-fints) (LGPL-3.0) and [mt-940](https://github.com/wolph/mt940) (BSD), which are installed as separate packages.
