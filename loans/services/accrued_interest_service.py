from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from loans.utils import get_period_details


class AccruedInterestService:

    @classmethod
    def calculate_total_debit(cls, loan, emi_date=None):
        _, periods_per_year = get_period_details(loan.emi_frequency)
        period_rate = (
            Decimal(str(loan.interest_rate))
            / Decimal(str(periods_per_year))
            / Decimal("100")
        )
        paid_principal = loan.payments.filter(status="paid").aggregate(
            total=Sum("principal_component")
        )["total"] or Decimal("0.00")
        paid_prepayment = loan.prepayments.filter(status="paid").aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.00")
        outstanding_disbursed = (
            loan.total_disbursed_amount - paid_principal - paid_prepayment
        )
        outstanding_disbursed = max(outstanding_disbursed, Decimal("0.00"))
        regular_interest = (outstanding_disbursed * period_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        regular_emi = loan.emi
        total_debit = regular_emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {
            "regular_emi": regular_emi,
            "regular_interest": regular_interest,
            "total_debit": total_debit,
            "outstanding_disbursed": outstanding_disbursed,
        }
