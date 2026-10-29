# Loan Operations release

The new workspace is `/operations/`, linked in the application sidebar. It extends the existing Django application; it does not replace its database or require a new frontend framework.

## Scope delivered

| Requested feature | Entry point / behavior |
| --- | --- |
| EMI reminders | Due/overdue and document expiry reminders; in-app, configurable email/SMS, delivery history and capped retries |
| Verified payments and reconciliation | Receipts require a distinct reviewer; bank CSV/XLSX matching checks reference, amount and date |
| Partial EMI | Interest-first allocations, multiple receipts per instalment, remaining amount shown in schedule |
| Corrections | Requested/approved reversals, original receipts preserved, balance restored; reverse newest receipts first |
| Receipts and closure documents | PDF receipts, settlement statement, NOC after settlement; reversed receipts visibly marked |
| Applications | Draft/edit, upload identity/income evidence, submit, review, approve/reject; approval creates an unfunded sanctioned loan |
| Roles and approvals | Loan Officer, Approver, Accountant, Collection Agent; independent approval for payments, reversals, disbursements, terms, waivers and payouts |
| Account recovery/security | Password-reset email, email verification, optional email OTP sign-in, OTP expiry/use/attempt limits, login throttling and session revocation |
| Charges | Effective-date product policies, fixed and percentage charges, charge receipts and independent waiver approval |
| Rate changes | Approved effective-date revisions, preserved old/new rate, EMI and schedule |
| Restructuring and holidays | New remaining periods; approved deferred due date; disclosed simple daily holiday interest capitalized at the previous rate |
| Collections | Ageing buckets, agent assignment, next follow-up, promise date/amount, notes and resolution |
| Imports | UTF-8 CSV or XLSX, downloadable headers, preview, row errors, duplicate detection, atomic commit |
| Documents | Review/comments, expiry reminders, linked immutable previous versions, private downloads; manual KYC review |
| Audit | Actor, old/new values, reason and timestamp for financial/operational changes; read-only audit UI |
| Multi-loan planning | Avalanche/snowball allocation and freed-instalment budget rollover; estimated debt-free months and savings |
| Transfer comparison | Existing/proposed monthly EMI, fees, lifetime saving and fee break-even |
| Payment provider and mandates | Configurable adapter contract, signed callbacks, amount/currency checks, event deduplication, consent/expiry/limits; disabled until configured |
| Lender portfolio | Verified funds/identity onboarding, available-fund reservation, investment exposure, repayment allocations and independently approved payout records |
| Co-borrowers/guarantors/collateral | Related party evidence/consent, valuation, requested/approved release after closure |
| Companies and branches | Explicit memberships, branch-scoped operations and reports, consolidated superuser view |
| PWA | Manifest, icons, service worker and offline message; sensitive pages/files are never cached |

## Existing gaps corrected

- The old `process_auto_debits` command now delegates to operations processing. It cannot create a paid EMI without payment confirmation. Recurring provider collection is separately opt-in.
- Removed stale runtime access to the deleted accrued-interest model and processing methods, including loan/disbursement edit/detail flows.
- Frequency-aware total-payable and closure-date calculations; comparison template supplied.
- Loan-closure notifications occur only on settlement, not every EMI/prepayment.
- Fixed undefined late-interest variables on the existing Payments page.
- New disbursements establish the outstanding funded balance. Unfunded sanctions are not shown as paid/overdue.
- Existing payment/prepayment buttons lead to receipt submission rather than recording money as received.
- Marketplace uses Decimal, locks funding updates, checks available funds and rejects self-investment/late investment after disbursement.
- User-editable credit scores removed from onboarding; stored score is explicitly unverified, not a bureau result.
- Protected document URLs replace direct public media links; blanket DEBUG media serving removed.
- Existing broad staff pages are superuser-wide; operational staff use membership-scoped Operations pages.

## Local review and verification

Use the project's existing interpreter from the `loan_manager` directory:

```bash
../venv/bin/python manage_local.py migrate
../venv/bin/python manage_local.py createsuperuser
../venv/bin/python manage_local.py runserver 127.0.0.1:8029
```

`manage_local.py` forces `loan_manager.local_settings`: a separate `operations-demo.sqlite3`, separate media directory, console email, and disabled payment/SMS adapters. It never uses `.env`'s database URL. `db.sqlite3` and `local.sqlite3` were not migrated by this implementation.

Validation completed: 58 regression tests passed; Django system checks and migration-drift check passed. The Operations dashboard and receipt form were inspected in a real browser, including a 390px mobile viewport. Preview cookies are isolated from other localhost Django applications.

Tests run on an in-memory database, not either existing database:

```bash
../venv/bin/python manage.py check --settings=loan_manager.test_settings
../venv/bin/python manage.py test --settings=loan_manager.test_settings --noinput
../venv/bin/python manage.py makemigrations --check --dry-run --settings=loan_manager.test_settings
```

For the existing deployment, back up the database and media, review/apply the new migrations, configure delivery/provider settings, collect static assets, then restart the application. Historical balances/receipts are not silently rewritten or marked verified. Existing records without a company remain personal/superuser-visible until deliberately assigned.

## Operating workflow

1. A superuser adds companies/branches and assigns team members in **Companies, branches & team**. At least two authorised people are needed for maker/checker actions.
2. A borrower creates an application, uploads identity/income evidence and submits it. An approver checks the documents/checklist and approves. Alternatively, a user can import their own unfunded loan records.
3. Request disbursement, then have a separate approver release it. Outstanding principal follows released amounts.
4. A borrower submits a dated receipt with reference/proof. An accountant verifies it directly or reconciles it against a bank-statement row. Only then is the instalment credited.
5. Use **Reversals** to correct the latest receipt. After reversal, submit corrected evidence as a new receipt. Earlier receipts require reversing later receipts first; revisions or existing payout requests block automatic reversal.
6. Use **Revisions** for rate/term/holiday changes. Resolve pending receipts and partial instalments before application of a revision. Run the operations command to apply approved future-effective revisions.
7. Use a charge policy to post an explicit fee, submit a charge receipt to pay it, or request/approve a waiver. Fees are not silently inferred from regulatory rules.
8. Identity/lender fund deposits can be submitted without owning a loan. A distinct superuser manually verifies evidence. No bureau/KYC-provider verification is claimed. Lender payouts record independently approved, externally completed bank transfers; the payout screen itself does not transfer money.

## Delivery and scheduler configuration

Schedule the following command using the deployment's normal scheduler:

```bash
python manage.py process_operations
```

It applies approved revisions, optionally queues mandate collections, queues reminders and retries failed deliveries (maximum five attempts). It does not install cron or alter the machine's scheduler automatically. Reminder delivery is at-least-once; SMS adapters receive a stable idempotency key. A `sent` status means accepted by the configured backend, not confirmed inbox/handset delivery.

Environment variables:

- `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`.
- `SMS_ADAPTER`: dotted callable accepting `destination`, `message`, `idempotency_key` and raising on failure.
- `PAYMENT_ADAPTER`, `PAYMENT_SERVICE_USER_ID`, `PAYMENT_CHECKOUT_HOSTS`, `PAYMENT_RECURRING_ENABLED` (default false).

The default email backend is console-only. Real password-reset/OTP/reminder delivery needs SMTP configuration. SMS and a specific payment provider were intentionally left unconfigured at the user's request. See `PROVIDER_CONTRACT.md`.

## Accounting and operational boundaries

- One EMI receipt may cover part/all of the current instalment. Excess is submitted separately as principal prepayment. A partial instalment must be completed before principal prepayment or term revision.
- Schedule revisions are prospective; no backdated rate rewrites. Holiday interest uses actual deferred days / 365 at the previous annual rate, then capitalizes into outstanding principal. These are explicit product rules, not a claim about every lender's contractual treatment.
- Reversal restores a stored snapshot only when later activity has not invalidated it. Reversed prepayments remain in history but are excluded from paid totals.
- Loan imports intentionally do not infer historical disbursements/payments. Bank imports identify statement entries; reviewers still approve matched receipts.
- Lender allocation uses the investment share of sanctioned principal. Fractions below one paisa are rounded down and retained by the originator. Allocation is distinct from bank payout.
- Planning tools normalize repayments to monthly equivalents, exclude fees/future rate changes, and are estimates rather than loan contract schedules.
- Historical auto-debit records remain historical records; this release does not assert that a bank transfer occurred for them.
- Audit protection is at application/UI level, not a tamper-proof external audit archive.
- SQLite checks do not prove production PostgreSQL concurrency behavior. Verify real provider sandbox/live callbacks, SMTP/SMS delivery and concurrent workers in the deployment environment before enabling live collection.
- PWA installation requires HTTPS in deployment (localhost works for review). Sensitive data remains online-only. Browser push subscriptions are not implemented; reminders use in-app/email/SMS channels.
