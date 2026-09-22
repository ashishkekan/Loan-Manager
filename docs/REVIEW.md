# Repository review — 22 September 2026

## Verdict

The original repository was not working end to end. Django system checks passed,
but the test suite contained zero tests and several ordinary actions crashed or
produced incorrect balances, reports or notifications. Confirmed issues below have
been corrected in code. The tested local core workflow works with fictional data.
This is **not** a certification that every production scenario is correct.

No production connection, migration, data repair, commit or push was performed.
The demo runs at **http://127.0.0.1:8011/** using `local.sqlite3`; port 8000 was already
occupied and its process was left alone. `.env`, existing `db.sqlite3` and its backup
were not modified. Their checksums were compared around local migration/runtime work.
No existing fixture or backup was loaded.

## How it works

Register/login → create loan → record released disbursement → calculate schedule →
record due EMI or prepayment → update principal/interest ledger → show dashboards,
notifications and exports → close when remaining sanctioned principal reaches zero.

Staff can inspect all users' loans, documents, reports and support. Borrowers see
their own records. Marketplace is a separate internal funding ledger, gated by lender
role, admin KYC and available funds. Staff roles and marketplace roles are independent.

## Confirmed defects and implemented corrections

| Area / impact | Why the error occurred | Correction |
|---|---|---|
| Disbursement create/edit/detail and loan edit crashes | Migration 0007 deleted accrued-interest model/field; views still called its missing relations and generation method | Removed obsolete calls and misleading accrued-interest UI; retained periodic interest service |
| Payments page crashes for both roles | Removed late-interest feature left undefined Python variables in context | Explicit zero for unsupported late-interest values |
| EMI says “fully repaid” after every payment | Closure notification was outside the closed-state condition | Emit only on actual closure |
| Partial prepayment also says “fully repaid” | Same unconditional notification; closure date never set | Conditional notification and saved closing date |
| Final EMI reports overcharge | `amount` was capped but `total_debit_amount` retained full EMI | Store the actual debit consistently |
| Partial release prematurely closes loan | New balance was computed from released balance while the stored loan balance represented sanction | Preserve sanction-minus-paid-principal balance; separately cap repayment at released outstanding |
| EMI without released funds / before due | Service lacked released-principal and next-due checks | Validate before writing; skip pre-release periods; future EMI rejected |
| Pending record skips an EMI number | Number was based on the latest row regardless of payment state | Derive next number from paid rows, update the existing pending installment |
| Duplicate manual requests advance another overdue EMI | Endpoint accepted an unqualified “pay next” request | Form sends expected installment number; service verifies it under loan lock |
| Prepayment zero/date/limit errors | Truthiness check skipped zero; only future dates were checked; sanction could be prepaid before release | Positive amount, released-outstanding cap, chronological checks and older-due-EMI guard |
| Prepayment concurrency | Record and balance updates were not atomic/locked | Loan lock and transaction around validation, record and balance updates |
| Quarterly/yearly totals and savings inflated | Several computations hardcoded 12 or multiplied per-period interest by months | Frequency-aware totals, monthly equivalent and period-based estimate |
| User dashboard overstates amount repaid | Principal paid already included prepayment, then prepayment was added again | Removed duplicate addition |
| Forecast diverges from ledger / closed loans show future debt | Hypothetical full-sanction schedule was generated first, then paid rows overlaid | Project from released funds and actual paid principal; no future rows for closed loans |
| Admin overdue falsely zero | Reports queried stored overdue Payment rows, but ordinary workflow creates only paid rows | Shared read-only schedule projections for admin overdue views, risk panels and reporting |
| Released KPI and performance incorrect | “Disbursed” summed sanction rather than released disbursements | Sum actual released amounts; include prepayments in repaid totals |
| Contract end date one period late | Added N periods to first EMI instead of N−1 | Correct last installment date |
| CSV export never works | URL allowed CSV but export dispatcher did not implement it | Added CSV output, including spreadsheet-formula escaping |
| Excel overdue/performance export crashes | Report titles containing `/` used directly as sheet names | Sanitize Excel sheet titles; force user text cells to text |
| Borrower can transfer loan ownership on edit | UpdateView did not pass request user to form, exposing owner field | Scope form to role; ignore owner input for ordinary users |
| Historical accounting silently changes | Loan terms and old disbursements could be edited after posted payments | Block term/release rewrites and release deletion after payment history |
| Disbursement cap wrong on pending→released | Form subtracted old pending amount from a released-only total | Subtract previous amount only if originally released; revalidate under parent loan lock |
| Disbursement POST delete fails | Custom `delete()` did not match Django's form-valid POST path | Implement deletion in `form_valid()` and preserve accounting guards |
| Upload/delete document routes crash | Dashboard upload omitted required loan argument; delete URL used a different parameter name | Resolve selected loan safely and align route/signature |
| Document privacy/role inconsistency | Direct media URLs bypassed owner checks; staff download contradicted staff loan access | Authenticated document routes, staff/owner scope, no public debug media route; protected profile-photo route |
| Comparison / delete confirmation missing | Views referenced nonexistent templates | Added corresponding templates |
| GET mutates financial/session state | Pay EMI, note deletion and logout accepted ordinary links/GET | POST-only mutations, CSRF forms and anonymous login guards |
| “Logout other devices” logs everyone out | Deleted every non-current session without checking session owner | Decode and delete only sessions belonging to the selected user |
| Open redirect after notification read | Unvalidated POST `next` URL used as redirect destination | Permit same-host safe redirects only |
| Staff cannot handle user support tickets | Support queries always filtered to the current user; replies always marked non-staff | Staff-wide ticket scope, staff reply attribution and proper response timestamp |
| Investment crashes/partial writes/overfunding | float mixed with Decimal; unvalidated amount; no transaction/auth or funds lock | Form-validated Decimal, login/POST, loan/profile locks, funds and ownership validation |
| Lender setup not operable through admin | Profile was not registered; loan publication field absent from admin form | Profile admin and publication control; PAN changes revoke KYC |
| Unsafe raw-admin accounting bypass | Financial balances and disbursements could bypass application services | Financial loan fields and disbursement admin made read-only; use application workflows |
| Scheduler hides partial failure | Command printed errors but exited successfully | Nonzero exit when any loan fails; isolated local command documented |
| No safe local runtime entry point | Normal settings automatically load `.env`/DATABASE_URL | Forced local settings, SQLite, private media and guarded local demo seed |

## Verification evidence

- `manage_local.py check`: no issues.
- `manage_local.py makemigrations --check --dry-run`: no changes detected.
- `manage_local.py test`: see final test result below; disposable SQLite database.
- Core page smoke checks for borrower and admin: dashboard, loans/create/edit/detail,
  compare, disbursements, payments, schedule, ledger, prepayments, documents,
  notifications, support, profile setup, settings and marketplace.
- All five admin report types tested in CSV, Excel and PDF (15 combinations).
- Regression coverage: creation/registration, zero-interest/frequency schedules,
  arithmetic and closure, partial/no release, duplicate submission, pending-record
  reuse, prepayment validation, CSRF, owner isolation, document upload/download/delete,
  session isolation, support staff reply, marketplace Decimal/funds validation,
  scheduler repeat, historical-edit guards and unsafe redirects.
- Local migrations applied successfully from scratch; fictional demo users and a
  fully released manual loan created. Browser login and borrower dashboard rendered
  successfully with CSS/icons/charts visible.
- No PostgreSQL or production data was used. SQLite does not prove row-lock behavior
  under concurrent PostgreSQL traffic.

## Remaining limitations / not implemented

1. **Not a live payment platform.** Auto Debit and investment are internal postings.
   No bank settlement, mandate, failed-debit retry reconciliation, lender payout or
   external KYC integration exists. Do not represent postings as verified cash flow.
2. Existing historical bad data was neither read nor repaired. Fixes govern new
   operations; incorrect past closure dates, balances or debit amounts need a separate
   authorized reconciliation/migration plan.
3. Remaining Balance includes undisbursed sanction. Cancelling that commitment after
   payments, restructuring, reversals and adjustments need explicit business rules.
   Deleting a loan remains a cascading deletion, not an audited reversal.
4. Interest is periodic, not day-count based. Projections assume currently recorded
   releases, not future releases or bank-specific rules. Historical late/backdated
   mixed transactions require reconciliation. Prepayment savings are approximate;
   foreclosure simulation uses a fixed 2% illustration, not a verified lender fee.
5. Some legacy screens label periods as “months”; advanced scenario charts/affordability
   estimates are not all frequency-normalized. Collection report pending counts are
   based on stored payment rows, whereas overdue pages are projected. Complex combinations
   of report bank/date filters and all legacy export variants are not exhaustively tested.
6. Marketplace has no settlement/refund/return distribution or complete investor
   portfolio workflow. Credit score is user-entered; it is not a verified bureau score.
7. Preferences such as notification/security/privacy toggles are mostly stored data,
   not proof of implemented email/SMS/2FA/security controls. Support attachments are
   stored but not exposed in a completed download flow. Landing-page investment cards,
   metrics, ratings and marketing claims are static sample content, not live evidence.
8. Storage access must remain private in deployment. File extension/size checks are
   not malware scanning. Rate limiting, infrastructure hardening, full audit retention,
   accessibility/mobile regression and load testing were outside this local review.
9. Local SQLite is for demonstration. Loan/profile locks were added to the write paths,
   but production-database concurrency tests and idempotency for all non-EMI operations
   are still needed. Raw administrative database writes can bypass application rules.
10. Existing platform scheduler scripts still use normal settings. They were not run,
    installed or rewritten as a production rollout. Use `manage_local.py` for demo work.

## Final test result

**34 tests passed** (`Ran 34 tests in 4.881s`, `OK`). No system-check issues or migration drift. The test suite is `loans/tests.py`; tests run against a disposable database, not the demo or production database.
