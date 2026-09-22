# Admin manual

## Access and roles

- Application staff access is determined by Django `is_staff`.
- Django `/admin/` additionally applies model permissions; the demo `localadmin` is a
  superuser. Its generated password is in `.local-demo-credentials.txt`.
- Marketplace lender/borrower roles do not grant staff access.
- Staff dashboard covers all users; ordinary users are restricted to their own data.

## Daily loan workflow

1. Login at `/accounts/login/`; open Dashboard, Users and Loans.
2. Create a loan using the application Create Loan form and choose its owner.
3. Review calculated EMI, frequency, first due date and Auto Debit selection.
4. Record actual disbursements on the application loan pages. Released totals must
   stay within sanction. Future releases and releases before loan start are rejected.
5. Review Documents; protected view/download supports staff. Document verification
   status can be maintained through Django admin.
6. Monitor Payments, overdue projections and Transaction Ledger. Staff payment pages
   are portfolio monitoring pages; manual borrower payment/prepayment endpoints remain
   owner-only. Do not impersonate users or enter duplicate records through admin.
7. After repayments, financial terms and existing disbursements are protected from
   historical rewrites. No reversal/restructuring workflow is implemented.

Django admin loan financial fields and disbursements are intentionally read-only;
use application forms for validation/accounting. Loan publication and Auto Debit can
be managed through the allowed admin fields. Deleting a loan still cascades to its
history; this is not an audited reversal or archival mechanism.

## Reports and interpretation

`/reports/` has Portfolio, Payment Collection, Overdue, User Summary and Performance
reports with CSV, Excel and PDF downloads.

- Portfolio: sanctioned principal, repayment balance and contractual end date.
- Collection: stored payment records. It does not verify bank settlement.
- Overdue: unpaid projected installments based on released principal, including
  loans with no saved payment rows. Filter dates refer to installment due dates.
- User Summary: principal repaid includes paid prepayments.
- Performance: released amounts and projected overdue totals by loan type or user's
  default bank. The default bank is a grouping convenience, not a recorded originating
  lender relationship. Do not use it for lender-level reconciliation.
- Remaining Balance includes undisbursed sanction. Projection and savings numbers
  are estimates, and scheduled/expected payments are not collected funds.

Use date, user, loan type and status filters appropriate to each report. Some filter
controls/collection summaries still follow stored-payment semantics; see review
limitations. Validate against bank statements independently.

## Support and users

1. Open Support to see all users' tickets; inspect a ticket and reply. Staff replies
   are marked as staff and move the ticket to In Progress.
2. Set Resolved/Closed or reopen through Django admin → Support Tickets.
3. Manage user active/staff flags and permissions through Django Users.
4. User settings can be opened from the application admin user workflow. “Logout all
   other devices” targets only that selected user's sessions, never all users.
5. Password changes use Django's validation. Store credentials outside shared docs.

## Marketplace administration

1. Django admin → Profiles: review role/KYC and internally recorded available funds.
2. Mark KYC verified only after your own verification process. No KYC provider is
   integrated. A PAN edit revokes the existing verification flag.
3. Django admin → Loans: enable Is Public for appropriate active loans.
4. Inspect investments. Funding is an internal ledger record, separate from released
   disbursement and borrower repayment. There is no automatic lender distribution,
   settlement, refund, reconciliation or return calculation.

## Run and verify locally

From `loan_manager/`:

```bash
../venv/bin/python manage_local.py migrate
../venv/bin/python manage_local.py seed_local_demo
../venv/bin/python manage_local.py check
../venv/bin/python manage_local.py test
../venv/bin/python manage_local.py runserver 127.0.0.1:8011 --noreload
```

For local scheduled posting only:

```bash
../venv/bin/python manage_local.py process_auto_debits
```

It selects active Auto Debit loans, catches up due installments, logs failures and
returns nonzero if any loan fails. Do not enable scheduled posting for a real account
without an agreed reconciliation process: it records paid automatically without
checking a bank balance or payment confirmation.

No cron/task scheduler was installed. Existing scheduler wrappers use normal
`manage.py` and could connect to the configured production database. Do not run them
as a local smoke test. Database selection must be verified before operational commands.

## Operational limits

The review used local SQLite and fictional data only. It did not validate production
rows, repair prior mispostings, test PostgreSQL races, run load tests or deploy. Keep
loan files private at the storage/web-server layer. There is no general audited
reversal, sanction-cancellation or full lender accounting workflow. Consult
[REVIEW.md](REVIEW.md) before deployment or reconciliation.
