# NexusLoan / Loan Manager

Django application for recording loans, released disbursements, EMI payments,
prepayments, documents, support tickets and internal lending investments.

**This is a loan accounting/demo application.** “Pay EMI”, “Auto Debit” and
“Invest” record internal transactions. There is no bank/payment-gateway integration,
mandate validation, settlement confirmation or payout engine.

## Start safely on this checkout

From `loan_manager/`:

```bash
../venv/bin/python manage_local.py migrate
../venv/bin/python manage_local.py seed_local_demo
../venv/bin/python manage_local.py runserver 127.0.0.1:8011 --noreload
```

Open http://127.0.0.1:8011/. Credentials for the fictional `localadmin`, `localuser`
and `locallender` accounts are generated in `.local-demo-credentials.txt` (mode 0600,
ignored by Git). The seed command does not reset existing demo users or passwords.
Admin login: `/admin/`; normal login: `/accounts/login/`.

`manage_local.py` forces `loan_manager.local_settings`, skips `.env`, ignores the
normal `DATABASE_URL`, and uses only `loan_manager/local.sqlite3` and `local_media/`.
It rejects `--settings` overrides. Do not import `data.json` or existing database
backups into the demo. Local settings are intentionally unsuitable for deployment.

For a fresh environment with Python 3.10+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage_local.py migrate
.venv/bin/python manage_local.py seed_local_demo
.venv/bin/python manage_local.py runserver 127.0.0.1:8011 --noreload
```

The existing sibling `venv` was used for this review. Installing dependencies from
scratch was not part of verification.

## Documentation

- [User manual](docs/USER_MANUAL.md)
- [Admin manual](docs/ADMIN_MANUAL.md)
- [Review: root causes, fixes, test evidence and limits](docs/REVIEW.md)

## Architecture

| Component | Responsibility |
|---|---|
| `accounts` | Django authentication, registration and marketplace Profile/KYC |
| `loans` | Loan/disbursement CRUD, notes, private files, settings, support and reports |
| `payments` | EMI service, prepayment, transaction ledger and scheduled posting command |
| `dashboard` | Borrower summaries, staff portfolio summaries and activity log |
| `marketplace` | Public listings, lender profile and internal funding records |
| `templates`, `static` | Server-rendered pages, CSS and JavaScript |
| `loan_manager/settings.py` | Normal environment-driven configuration |
| `loan_manager/local_settings.py` | Isolated local review/demo configuration |

Request flow: URL → authentication/owner or staff scope → form validation →
transactional write → activity/notification → rendered dashboard/ledger.
Django's built-in User is used; `is_staff` controls portfolio-wide application
access. Marketplace roles are separate from staff permissions. `accounts.Profile`
is the marketplace profile; `loans.UserProfile` holds settings/profile-photo data.

## Accounting rules

1. Creating a loan sets sanctioned principal, frequency and calculated installment.
2. Released disbursements must not exceed sanction. Pending/cancelled entries do not
   count as released funds. Record the initial release before paying EMIs.
3. EMI frequency can be monthly (12/year), quarterly (4), half-yearly (2) or yearly (1).
   The standard reducing-balance EMI formula is rounded to two decimals.
4. Interest per installment = released principal outstanding at its due date ×
   annual rate / periods per year. No daily accrual, late interest or bank-specific
   rounding/calendar rules are implemented. Old additional-accrual tables were
   removed by the existing migration `0007`; they have not been restored.
5. EMI principal = installment − interest, capped at released outstanding. The last
   actual debit may be lower than the nominal EMI. A future EMI cannot be posted early;
   use prepayment for early principal reduction. Manual forms carry an installment
   number so retrying an already-posted installment cannot advance the loan twice.
6. `remaining_balance` retains the existing meaning: sanction minus paid principal
   and paid prepayments. It **includes undisbursed sanction**. Released outstanding
   is a separate amount. Paying off a partial release does not close the whole loan.
7. Prepayments reduce principal, must be positive and within released outstanding,
   cannot predate existing transactions, and require older due EMIs to be recorded
   first. Tenure/interest savings are estimates, not a lender settlement quotation.
8. Terms and existing disbursements cannot be rewritten after payments exist. New
   releases must be dated after recorded payment dates. This prevents historical
   interest from silently changing. Loan closure requires zero remaining principal.
9. Schedules combine actual payments with projections on released principal. Future
   unrecorded disbursements are not assumed. Admin overdue views use these same
   projections, not nonexistent saved “overdue” payments.
10. Marketplace funding is distinct from disbursement. It requires a verified lender,
    sufficient internally recorded available funds, a public active loan and no
    self-investment. Loan/profile updates use Decimal arithmetic and a transaction.

## Verification

```bash
../venv/bin/python manage_local.py check
../venv/bin/python manage_local.py makemigrations --check --dry-run
../venv/bin/python manage_local.py test
```

Tests use a disposable SQLite test database, not production or `local.sqlite3`.
The suite covers core pages for both roles, permissions, disbursement CRUD, payment
arithmetic, repeated requests, schedule projection, reports/exports, documents,
support replies, sessions and marketplace validation. See the review for the exact
verified result and limits. No schema migration was added for these fixes.

## Scheduled posting

For a **local-only** catch-up run:

```bash
../venv/bin/python manage_local.py process_auto_debits
# Or: bash scripts/run_local.sh process_auto_debits
```

Only active loans with Auto Debit enabled are selected. Due installments are posted
until current; a repeat run does not repost the same installments. Failed loans are
reported and cause a nonzero command exit. The command does not withdraw funds from
a bank. No scheduler/cron job was installed or started during the review.

Existing `scripts/scheduler.py` and platform wrappers invoke normal `manage.py` and
may load production configuration. They were not executed. Cron uses the host's
configured timezone; a `17:00` entry is not inherently 17:00 IST. Django currently
uses UTC. Use explicit local command paths for demos.

## Deployment boundary

Normal `manage.py` loads `.env` with `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` and
`DATABASE_URL`. Review those values and infrastructure before any deployment.
Never deploy local demo settings or credentials. Uploaded loan documents must be
served through authenticated view/download routes; do not expose `MEDIA_ROOT`
publicly through a web server or storage bucket. SQLite is for local review; verify
transaction contention on the intended production database before concurrent use.

This review did not connect to or migrate production, modify its data, install a
scheduler, commit or push. Existing historical accounting errors are not automatically
repaired. Read `docs/REVIEW.md` before treating reports as production-certified.
