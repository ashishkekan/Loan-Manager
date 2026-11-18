# Loan Manager: Accounting Logic and Project Flow

## 1. Purpose

This document describes the financial workflow used by Loan Manager:

- Construction-period partial disbursements.
- Daily disbursement-wise interest accrual.
- Bank-statement interest postings when the lender supplies an authoritative amount.
- EMI allocation into interest and principal.
- Principal-only prepayments.
- Maker-checker payment and disbursement approvals.
- Statement, schedule, and outstanding-balance reconciliation.

The accounting rule is:

```text
Bank-posted interest exists -> use the posted amount.
Bank-posted interest absent  -> use the daily 365-day accrual fallback.
Prepayment                  -> 100% principal reduction.
EMI                         -> interest first, remaining amount principal.
```

## 2. Main modules

| Area | Location | Responsibility |
|---|---|---|
| Loan entity and disbursements | `loans/models.py` | Loan terms, released tranches, balance fields |
| Daily interest ledger | `loans/services/accrued_interest_service.py` | Daily tranche-wise fallback interest |
| Bank interest ledger | `loans/models.py` (`BankInterestPosting`) | Authoritative lender-posted interest |
| Financial posting service | `operations/services.py` | Verify receipts and update balances atomically |
| Maker-checker operations | `operations/access.py`, `operations/services.py` | Permissions and separate reviewer enforcement |
| Receipt workflow | `operations/models.py`, `operations/views.py` | Pending, verified, rejected, and reversed receipts |
| EMI and prepayment records | `payments/models.py` | Principal/interest components and payment history |
| Loan page and pending-interest display | `loans/views.py`, `templates/loans/loan_detail.html` | Borrower-facing balance and ledger information |
| Admin bank-interest entry | `loans/admin.py` | Enter lender statement postings |

## 3. Loan creation flow

1. Borrower creates a loan with sanctioned amount, interest rate, tenure, frequency, start date, and first EMI date.
2. `remaining_balance` starts at zero because sanctioned amount is not the same as funded amount.
3. Each released disbursement increases the funded principal.
4. EMI is calculated from the loan terms, but the actual bank debit may be rounded to the nearest rupee.
5. The sanctioned amount is only a ceiling; released disbursements must not exceed it.

Do not enter the current bank outstanding as the sanctioned amount. Enter the original sanctioned amount and record every actual disbursement.

## 4. Disbursement flow

### Maker

Yash or another authorised maker enters one row per bank disbursement:

- Actual value date.
- Exact amount.
- Purpose.
- Bank reference and remarks.

Different bank value dates must not be combined. Two disbursements on the same date may be kept as separate rows when the statement shows separate transactions.

### Checker

Admin reviews and releases the pending disbursement. A different user must perform the approval.

When released:

```text
funded principal += disbursement amount
remaining balance = released principal - verified EMI principal - paid prepayments
```

Disbursements remain available during the construction period, including after an EMI, as long as the loan is active and the sanctioned limit is respected.

## 5. Daily interest fallback

Daily interest is calculated separately for each released tranche:

```text
daily interest = tranche principal balance × annual rate ÷ 100 ÷ 365
```

The ledger is created for dates before the receipt date. The receipt date is exclusive to avoid charging the same day twice.

Principal reductions before a day are applied in transaction order:

- Verified EMI principal.
- Verified principal-only prepayment.

Daily rows are stored in `loan_interest_accruals` and are grouped on the borrower page by disbursement instead of displaying one row per day.

## 6. Bank-posted interest override

Some lenders post a single interest debit that includes their internal value-date, day-count, and rounding rules. That amount may differ from the project's estimated daily total.

These entries are stored in `BankInterestPosting` with:

- Posting date.
- Period start and end.
- Posted amount.
- Bank reference.
- Recovery status.
- Related EMI when recovered.

When a loan has at least one bank posting:

1. The bank-posted ledger is authoritative for EMI interest allocation.
2. Daily fallback accrual is not generated for that loan.
3. EMI interest is recovered from the oldest open bank posting first.
4. The remaining EMI amount becomes principal.
5. Reversal restores the posting's unrecovered amount.

When no bank posting exists, the daily 365-day fallback remains active.

### BOB example

The BOB statement supplied for Loan #10 has these authoritative interest postings:

| Posting date | Period | Amount |
|---|---|---:|
| 10-Sep-2026 | 30-Jul to 09-Sep | ₹2,104 |
| 10-Oct-2026 | 10-Sep to 09-Oct | ₹3,066 |
| 10-Nov-2026 | 10-Oct to 09-Nov | ₹3,874 |

The project must use ₹3,874 for the November EMI when reconciling the statement. It must not replace it with an estimated ₹3,815.33.

## 7. EMI posting flow

### Maker

The borrower submits a manual in-app receipt or uses the configured payment provider. The receipt is initially `pending`.

The receipt contains:

- EMI amount actually debited by the bank.
- Value date.
- Bank reference.
- Payment mode.

### Checker

Admin opens `Loan Operations -> Payment Receipts`, checks the bank evidence, and verifies the receipt. Self-approval is prohibited.

### EMI allocation

When verified:

```text
interest = open bank-posted interest
          OR open daily accrued interest

principal = verified EMI amount - interest
remaining balance -= principal
```

The contractual EMI and actual rounded debit are kept separately. A small first-posting rounding difference is allowed; larger excess must be entered as a separate prepayment.

## 8. Prepayment rule

Prepayment is always principal-only:

```text
prepayment interest component = ₹0
prepayment principal component = full payment amount
remaining balance -= full payment amount
```

Pending accrued interest is not deducted from a prepayment. It remains a separate pending-interest item and must be settled through the lender's interest/EMI process.

For Loan #10:

```text
₹8,016 + ₹30,000 = ₹38,016 principal prepayment
```

## 9. Exact Loan #10 reconciliation

The statement sequence is:

```text
30-Jul disbursement                 +₹1,64,742
31-Aug disbursement                 +₹3,59,842
10-Sep interest                     +₹2,104
10-Sep EMI                          -₹15,869
25-Sep insurance debit/credits      net ₹0
09-Oct interest                     +₹3,066
10-Oct EMI                          -₹15,869
28-Oct prepayment                   -₹8,016
28-Oct disbursement                 +₹2,87,875
09-Nov interest                     +₹3,874
10-Nov EMI                          -₹15,869
18-Nov prepayment                   -₹30,000
```

Balance:

```text
₹5,24,584 + ₹2,104 - ₹15,869 = ₹5,10,819
₹5,10,819 + ₹3,066 - ₹15,869 = ₹4,98,016
₹4,98,016 - ₹8,016 + ₹2,87,875 = ₹7,77,875
₹7,77,875 + ₹3,874 - ₹15,869 = ₹7,65,880
₹7,65,880 - ₹30,000 = ₹7,35,880
```

Therefore the reconciled Loan #10 balance is **₹7,35,880**.

## 10. Roles and approval separation

| User | Allowed workflow role |
|---|---|
| Yash / borrower | Create loan, request disbursement, submit manual EMI, submit prepayment |
| Admin / authorised checker | Approve disbursement, verify/reject receipt, enter bank-posted interest, approve reversals |

The person who submits a receipt or disbursement cannot verify the same record.

## 11. Reversal and audit rules

- Financial history is not deleted directly.
- Verified receipts are reversed through the reversal workflow.
- Later postings must be reversed before earlier postings.
- Reversal restores loan balance, payment components, prepayment state, and interest recovery.
- Bank-posted interest entries also restore their recovered amount on reversal.
- A funded loan with financial history should not be deleted.

## 12. Reconciliation checklist

For every bank statement import, verify in this order:

1. Disbursements and their value dates.
2. Same-day transaction order.
3. Bank interest postings.
4. EMI debit amount and value date.
5. EMI interest and principal components.
6. Principal-only prepayments.
7. Insurance/charge entries separately from principal.
8. Loan detail balance.
9. Statement balance.
10. EMI schedule and payment history.

Never create an artificial disbursement or arbitrary balance override just to make a number match. If the lender posts an unexplained amount, record the lender-posted amount with its reference and ask the lender for the underlying breakup.

## 13. Verification commands

From the project directory:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py test operations
```

The bank-interest schema migration is `loans/migrations/0013_bankinterestposting.py`.

