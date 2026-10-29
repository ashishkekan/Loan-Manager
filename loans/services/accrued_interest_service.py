"""Compatibility facade for the next scheduled debit display."""

from decimal import Decimal
from loans.utils import generate_full_schedule


class AccruedInterestService:
    @classmethod
    def calculate_total_debit(cls, loan, emi_date=None):
        rows = [row for row in generate_full_schedule(loan) if not row["is_paid"]]
        row = (
            next((r for r in rows if r["due_date"] == emi_date), None)
            if emi_date
            else None
        )
        row = row or (rows[0] if rows else None)
        amount = row["total_debit"] if row else Decimal("0.00")
        return {
            "regular_emi": amount,
            "regular_interest": row["interest"] if row else Decimal("0.00"),
            "total_debit": amount,
            "outstanding_disbursed": loan.remaining_balance,
        }
