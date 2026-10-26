"""Compatibility adapter for installment previews; no separate accrued ledger."""

from loans.accounting import ZERO, schedule


class AccruedInterestService:
    @classmethod
    def calculate_total_debit(cls, loan, emi_date=None):
        rows = [
            r
            for r in schedule(loan)
            if not r["is_paid"] and (emi_date is None or r["due_date"] == emi_date)
        ]
        row = rows[0] if rows else None
        return {
            "regular_emi": row["total_debit"] if row else ZERO,
            "regular_interest": row["interest"] if row else ZERO,
            "total_debit": row["total_debit"] if row else ZERO,
            "outstanding_disbursed": row["balance"] + row["principal"] if row else ZERO,
        }
