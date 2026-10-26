# NexusLoan

Django loan workspace with borrower and administrator screens, released-funds accounting, repayment schedules, confirmed-payment recording, documents, support and reports.

## Run the isolated local preview

From this project directory:

```bash
../venv/bin/python manage_local.py migrate
../venv/bin/python manage_local.py seed_demo
../venv/bin/python manage_local.py runserver 127.0.0.1:8011
```

Open http://127.0.0.1:8011/. Newly created sample accounts are `demo` and `demo_admin`, with initial password `NexusDemo!2026`. These are demonstration accounts only. The seed command refuses to run with deployment settings and does not reset existing account credentials.

`manage_local.py` forces `loan_manager.local_settings`, skips `.env`, ignores deployment database configuration, and uses `local.sqlite3` and `local_media`. It rejects `--settings` overrides. Normal `manage.py` continues to use deployment configuration. Never use demo accounts or the development secret in a deployment.

## Payment behavior

1. Create a loan with sanctioned amount, interest, tenure and installment frequency.
2. Record actual disbursements. An undisbursed sanction is not outstanding debt.
3. Select periodic interest (the existing default), or daily reducing balance (Actual/365). Daily interest uses dated released principal and actual repayment/prepayment dates, includes the release day and excludes the installment day, and rounds the total daily interest for each installment to the nearest rupee (half up), displayed with two decimals. Interest is billed on contractual due dates without late fees or interest capitalization. Enter the actual bank installment when known; leaving it blank calculates EMI from the sanctioned terms.
4. Record an installment only after confirming receipt. The action uses a signed, expiring confirmation tied to the user, loan, installment and amount. Repeating that confirmation returns the existing payment.
5. Clear due installments before recording an early principal prepayment. A prepayment must be positive, no greater than released outstanding principal, and not precede the last recorded repayment. Savings shown are estimates under unchanged loan terms.
6. Financial terms and historical disbursements cannot be edited after repayments. Add subsequent releases as new records, dated on or after the latest repayment. Loans with financial history cannot be deleted.
7. Ledger amounts are authoritative. A final installment records the actual amount in every payment amount field. Historical paid rows are displayed using their recorded amount; existing financial data is not rewritten by a schema migration.

Transactions lock the loan while recording payments, prepayments, releases and investments. Unique request keys protect retries. Production concurrency should use PostgreSQL; SQLite does not provide equivalent row-level locks.

## Due-date reminders (previously auto debit)

```bash
../venv/bin/python manage_local.py process_auto_debits
```

The compatibility command now creates due-payment reminders for opted-in loans. It does **not** debit accounts, create paid transactions, or reduce balances. Repeated runs avoid duplicating an existing reminder. Existing scheduler scripts can continue invoking this command. In production, use the deployment interpreter and `manage.py`; cron time follows the scheduler host's configured time zone.

No payment gateway, settlement webhook, bank mandate, or external collection integration is configured. A real gateway requires its provider contract and credentials; do not treat reminders or manually entered records as bank confirmation. Marketplace investments reserve existing available platform funds atomically and do not initiate bank transfers.

## Private files and user isolation

Loan documents are served through owner/staff-authorized download and view endpoints. Media URLs also resolve through an authenticated, ownership-checked view covering documents, profile photos and support attachments. Keep media storage private: do not configure a reverse proxy, object storage bucket, or CDN to serve `/media/` publicly and bypass these checks. Static CSS and JavaScript can be served normally.

“Logout other devices” removes only the selected user's sessions. Account mutation endpoints remain protected by authentication and CSRF checks. Loan notes, repayments and logout use POST requests.

## Design

The public landing page, app shell, overview, loan details and payments page use the new navy/teal visual system. Other screens share the same navigation, typography, cards, forms, table styling and theme tokens in `static/css/workspace.css`. The shared script implements responsive navigation, Escape/focus handling, theme switching, duplicate-submit feedback and horizontally scrollable tables. Mobile settings navigation scrolls horizontally instead of pushing its content below a long menu.

### Appearance studio

Open **Appearance** from the top bar, sidebar footer, or mobile header. Choose green, pink, black, white, red, orange, blue, or purple to apply a coordinated palette and typography across the app. Black automatically uses dark surfaces; display mode and typography can also be selected independently. Preferences are saved to the signed-in account and survive navigation and sign-in on another device. **Use workspace default** restores inheritance.

Staff accounts can expand **Admin · Workspace defaults** to set the default palette/font and allow or disable personal choices. When personal choices are disabled, ordinary users see the workspace theme; their saved preferences return if customization is re-enabled. Staff retain personal controls. Anonymous pages use the workspace default. No navigation menu names were changed.

The overview includes a keyboard-accessible monthly repayment chart with 3/6/12-month ranges and search within the eight recent loans shown. The chart uses settled installments and prepayments dated in the selected period; undated and future transactions are excluded. Use View all for the complete portfolio.

Migration `loans.0010` adds appearance preference fields and the workspace configuration table. It was applied only to the isolated local database. Apply it before starting the updated app in another environment, then collect static files and restart as described below.

## Verification

```bash
../venv/bin/python manage_local.py check
../venv/bin/python manage_local.py makemigrations --check --dry-run
../venv/bin/python manage_local.py test loans accounts payments dashboard marketplace
```

Tests use an in-memory database and temporary upload storage. They cover populated borrower/admin routes, all report export formats, session isolation, document ownership, CSRF, payment retries, final installments, quarterly interest, staged releases, prepayments, investment recording and protected financial history.

## Deployment handoff

New schema migrations: `loans.0008`, `loans.0009`, `loans.0010`, and `payments.0003`. These add nullable unique request keys and change the default/help text for the reminder preference; they do not correct or delete historical financial records.

Before deployment, take a database backup, review `python manage.py migrate --plan`, apply the schema migrations against the explicitly selected environment, run `collectstatic`, and restart the application. Verify that media requests reach Django. Review any pre-existing balance discrepancies against actual receipts before making separately authorized financial corrections. No production migrations or data repairs are performed by the local preview workflow.

## July 2026 in-memory test scenario

Run a fresh, disposable localhost workspace:

```bash
cd /home/ubuntu/Documents/ZIPS/Product/Loan/loan_manager
../venv/bin/python -u run_loan_scenario.py
```

Open http://127.0.0.1:8012/accounts/login/. Borrower `july_borrower` (user ID 1),
admin `july_admin` (user ID 2), both with test password `JulyLoan!2026`.
Django admin is at `/admin/`; the loan is `/loans/1/`.
The runner migrates and seeds a named, shared **RAM-only SQLite database** in one
process, with a keeper connection and no reloader. Stopping the process loses
all database changes; rerunning recreates the original scenario. Temporary media
is separate. Existing disk databases are not migrated or seeded by this runner.
Migration `loans.0011` is required for other environments; it has only been applied
to disposable test/scenario databases in this workflow.

The profile, address, contact details, KYC flag and bank account are fictional
fixtures. The loan creation/business date is 18 July 2026; `created_at` retains the
actual fixture insertion timestamp. Terms: sanction Rs 20,00,000, 20 years, 7.30%,
monthly EMI **Rs 15,869 as supplied by the user**, first due 10 September 2026.

| Release date | Purpose | Amount |
| --- | --- | ---: |
| 30 July 2026 | Insurance | 29,000.00 |
| 30 July 2026 | Builder | 1,35,742.00 |
| 31 August 2026 | Builder (assumed purpose) | 3,59,842.00 |
| Total | Released principal | 5,24,584.00 |

No interest is charged from creation on 18 July through 29 July, or on the
undisbursed Rs 14,75,416. Daily convention is an explicit simulation assumption,
not verification of the bank's day-count/rounding policy:

- 30 July–31 August: 32 days × 1,64,742 × 7.30% / 365 = 1,054.3488.
- 31 August–10 September: 10 days × 5,24,584 × 7.30% / 365 = 1,049.1680.
- First interest 2,103.5168 rounded once to the nearest rupee: **2,104.00**.

| Due/payment date | EMI | Interest | Principal | Closing principal | Fixture state |
| --- | ---: | ---: | ---: | ---: | --- |
| 10 September 2026 | 15,869.00 | 2,104.00 | 13,765.00 | 5,10,819.00 | Simulated received payment |
| 10 October 2026 | 15,869.00 | 3,065.00 | 12,804.00 | 4,98,015.00 | Projection |

Subsequent dates stay on the 10th. Since only part of the sanction has been
released, the projection pays off that released balance earlier than 20 years
unless further releases are added. Future installments are not marked paid.
No bank connection or automatic debit is configured; due-date reminders do not
move money. The record-payment button becomes available when the installment is
due. As of 9 October, October's installment is correctly pending.

Check borrower Overview → My loans → loan details → disbursements, full schedule
and ledger. Payments shows the September receipt; Settings has the populated
profile and mock bank account. Support includes a resolved example conversation.
Admin can inspect the same loan, user profile, bank listing, activity and reports.
Documents upload/download and report exports are covered by the regression suite.
Prepayment savings and general comparison/foreclosure calculators remain
estimates; they are not bank settlement quotes. Full daily-interest prepayment
between installment dates is rejected to avoid dropping unbilled interest;
clear the installment and fully prepay on that due date, or obtain a settlement
quote for a different date.

```bash
../venv/bin/python manage_local.py test loans accounts payments dashboard marketplace --noinput
../venv/bin/python manage_local.py makemigrations --check --dry-run
```

The focused `loans.test_daily_scenario` tests independently assert the above
amounts, next due dates, retries, late principal effects, partial prepayments,
same-day releases, pending/future releases, bank EMI input, financial-term locks,
owner isolation, populated borrower/admin pages and report exports.
