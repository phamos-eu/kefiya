# Staging validation — money-moving paths

These two paths create / move money and were only statically verified (no bench
during development). **Validate them on a staging site with a real (or test)
bank access before enabling in production.** Both are gated (default-off settings
/ explicit confirmation), so deploying the code is safe; this checklist is about
turning them on.

Scope:
- **Auto-reconcile after import** — `kefiya/utils/auto_reconcile.py`
- **FinTS outgoing transfer** — `kefiya/utils/client.py` (`submit_payment_request_via_fints`, `send_transfer_tan`), `kefiya/utils/fints_controller.py` (`submit_sepa_transfer`)
- (low risk, validate too) **Securities holdings** — `kefiya/utils/securities.py`

---

## 0. Preflight (read-only)

Run in `bench --site <staging> console` and confirm each line:

```python
import frappe
# ALYF Banking present + auto-reconcile entry point resolvable
frappe.get_attr("banking.klarna_kosma_integration.doctype.bank_reconciliation_tool_beta.bank_reconciliation_tool_beta.auto_reconcile_vouchers")
# core voucher creation resolvable
frappe.get_attr("erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_payment_entry_bts")
# python-fints Holding attribute names match what get_fints_holdings maps
from fints.models import Holding   # confirm: isin, name, pieces, market_value, total_value, value_symbol, valuation_date
# settings exist
frappe.get_meta("Kefiya Settings").get_field("auto_reconcile_after_import")
frappe.get_meta("Kefiya Settings").get_field("auto_create_advance_payments")
```

If `fints.models.Holding` attribute names differ from
`isin/name/pieces/market_value/total_value/value_symbol/valuation_date`, fix the
mapping in `get_fints_holdings` before testing securities.

---

## 1. Securities holdings (low risk)

1. Ensure a Kefiya Login points at an account with a securities account (Depot).
2. Run `kefiya.utils.client.import_fints_holdings(<login>, <login>)`.
3. Check a `Kefiya Securities Account` was created, owned by the login's company.
4. Check `Kefiya Security Holding` rows exist for today's valuation date, with
   sane `price` (per unit) vs `market_value` (position total).
5. Re-run — confirm rows are **updated, not duplicated** (idempotent).

---

## 2. Auto-reconcile after import

Enable in **Kefiya Settings**: `auto_reconcile_after_import = 1`. Leave
`auto_create_advance_payments = 0` for the first pass.

1. Create a few matching vouchers (a submitted Payment Entry / Sales Invoice
   that should match an incoming statement line).
2. Run a normal FinTS import for the account.
3. Confirm matched Bank Transactions were reconciled (allocated) by ALYF's
   `auto_reconcile_vouchers`. Unmatched ones stay open.
4. Confirm the **import still completes** even if reconciliation logs an error
   (check Error Log for "Kefiya auto-reconcile ...").

### Stage 3 — prepayment advances (higher risk)

Enable `auto_create_advance_payments = 1`.

1. Set up a **tenant/customer** whose IBAN is on a `Bank Account` linked to that
   Customer (so `_identify_party` resolves it).
2. Import a statement containing an **incoming** payment from that IBAN with
   **no** open invoice.
3. Confirm exactly **one** on-account Payment Entry (advance) is created for the
   Customer and the Bank Transaction is reconciled to it.
4. Re-import the same window (overlap) — confirm **no duplicate** advance.
5. Create the matching invoice later — confirm the advance allocates against it.
6. Negative checks: ambiguous/unknown IBAN → **no** advance (left for manual);
   supplier-IBAN incoming refund → **no** customer advance.

---

## 3. FinTS outgoing transfer (highest risk — moves money)

Use a **test payee** and a **small amount** on staging.

1. Create + submit an **Outward** Payment Request with a `company_bank_account`
   that has exactly **one** `Kefiya Login`.
2. Open the Payment Request → **"Send via FinTS"** button.
3. Confirm the dialog shows amount + payee, then confirm.
4. Verify:
   - **Confirmation required:** calling the endpoint with `confirmed=0` returns
     an error and does **not** contact the bank.
   - **Amount cap:** the transferred amount equals the PR amount but never more
     than the invoice outstanding (test a partially paid invoice).
   - **TAN:** the TAN dialog appears; entering the TAN completes the transfer
     (`send_transfer_tan`). Wrong TAN → clear error, no money moved.
   - **No-TAN path:** if the bank does **not** request a TAN, an Error Log entry
     "Kefiya SEPA transfer completed without TAN challenge" is written — review
     whether that is acceptable for your bank/policy.
   - **Double-submit:** a second click while one is in progress is rejected
     ("already in progress").
   - **Audit:** every attempt is in the logs (`frappe.logger("kefiya")`).
5. Confirm the transfer actually appears at the bank and, on the next import,
   the resulting Bank Transaction reconciles.

### Known limitations to verify
- TAN uses the existing single-slot resume mechanism — **do not run other FinTS
  operations on the same login while a transfer awaits its TAN**.
- True idempotency uses a cache lock (not a persisted Payment Request status) —
  consider a status field before high-volume production use.
- Decoupled push-TAN: the dialog expects a TAN code; verify the push-TAN
  (confirm-in-app) flow end to end.

---

## 4. Rollback / cleanup after testing

- Cancel/delete test Payment Entries and advances created on staging.
- Reset `Kefiya Settings` toggles to off if staging mirrors production config.
- Remove test `Kefiya Securities Account` / `Kefiya Security Holding` rows.
