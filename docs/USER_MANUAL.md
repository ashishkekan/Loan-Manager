# User manual / User side ka flow

## 1. Login and account

1. Open `/accounts/register/` and enter name, username, email and password, or use
   `/accounts/login/` for an existing account.
2. Local demo: use `localuser` and its password from `.local-demo-credentials.txt`.
3. Dashboard shows your loans, recorded payments, sanctioned balance and projections.
   Monthly EMI is the monthly equivalent when loans have different frequencies.
4. Settings lets you update your profile, password, bank records and preferences.
   “Logout all other devices” affects only your own other sessions. Bank details and
   notification/privacy preferences are stored records; they do not establish a
   bank mandate or activate external notification delivery.

## 2. Create and release a loan

1. Open Loans → Create Loan (`/loans/create/`).
2. Enter name, type, sanction amount, annual rate, tenure, creation date, first EMI
   date and frequency. First EMI cannot precede creation. Choose Auto Debit only if
   you want the scheduled command to automatically record paid installments.
3. Save. EMI is calculated automatically.
4. Open the loan → Disbursements → Add. Enter amount, actual release date, purpose
   and status. Only **Released** funds are eligible for repayment/interest.
5. Multiple releases are supported within sanctioned principal. Editing/deleting
   earlier releases is blocked once a payment is recorded.

**Meaning of amounts:** Sanction is the agreed loan limit. Total Released is money
recorded as disbursed. Remaining Sanction is not yet released. Remaining Balance is
sanction minus principal repayments, so it can include unreleased money.

## 3. Record EMI

1. Open the loan or Payments. With manual mode, use Pay EMI for the next due EMI.
2. Check the due date and amount. Submitting records payment internally; it does not
   initiate a real bank transfer. Record only a payment you intend to recognize.
3. View the result in EMI Schedule and Transaction Ledger. Principal plus interest
   equals the debit; the final installment can be smaller than the normal EMI.
4. A refreshed form is needed after payment. Replaying the same installment request
   will not record the next overdue installment accidentally.
5. “Not due yet” → wait until due, or use Prepayment. “No released principal” → check
   disbursement status/date. These errors do not create a payment.

Auto Debit requires an operator to run the scheduling command. Merely enabling the
checkbox does not start a background service.

## 4. Prepayment and closure

1. Record earlier overdue EMIs before a later prepayment.
2. Open the prepayment form, enter a positive amount within released outstanding and
   a date on/after loan start and existing transactions, not in the future.
3. Save and inspect remaining principal and the prepayment ledger. Savings and tenure
   reduction are estimates. They are not a bank foreclosure quotation.
4. Full repayment sets Closed and records the closing date. Manual close is rejected
   while principal remains. If unreleased sanction remains, the loan stays active;
   reducing/cancelling that commitment after repayment is not implemented.

## 5. Documents, notes and exports

- Upload from loan detail or Documents → Upload; select your loan and document type.
- View/download through the application's protected buttons. Another ordinary user
  cannot access your loan or documents by guessing its URL. Staff can review them.
- Notes can be added/removed from loan detail. Delete actions require POST forms.
- EMI schedule has an Excel export; Payments has statement/Excel output; Prepayments
  has Excel/PDF outputs. A schedule includes projections, not just cash received.
- Delete Loan removes related financial history. Use only for intentional removal,
  especially in the demo; it is not a payment reversal workflow.

## 6. Support and notifications

Create a ticket from Support, optionally link your loan, and describe the problem.
Staff can open the ticket and reply; closed/resolved tickets do not accept new replies.
Use in-app notifications for loan/payment events. No external email/SMS delivery was
verified. Uploaded support attachments are stored; the current ticket page does not
provide an attachment-download workflow.

## 7. Lender / marketplace

1. Set role to Lender in Profile Setup and provide the requested details.
2. An admin must verify KYC and record available funds. Changing PAN clears verification.
3. Open Marketplace and enter an amount on a public active opportunity. You cannot
   invest in your own loan, overfund it, or exceed your available funds.
4. Successful investment reduces the internal funds balance and increases funded
   amount. This does not release funds, initiate settlement or guarantee returns.
   Other borrowers' private detail pages are not exposed to lenders.

## Demo reset / help

The demo is isolated in `local.sqlite3`; production is not used. Do not delete the
existing `db.sqlite3` or import `data.json` to reset a demo. Ask the operator to
recreate the local database after preserving anything needed. Report an error with
page URL, action, time and message; do not send passwords, PAN or bank numbers.
