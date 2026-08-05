"""
Utility functions for EMI calculation and amortization scheduling.
These are pure functions with no side effects — easy to test and reuse.
"""

import calendar
import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, getcontext

from django.db.models import Sum

getcontext().prec = 50


def calculate_emi(
    principal,
    annual_rate,
    tenure_years,
    frequency="monthly",
):
    """
    Calculates EMI according to repayment frequency.
    """
    P = Decimal(str(principal))
    _, periods_per_year = get_period_details(frequency)
    total_periods = tenure_years * periods_per_year
    R = Decimal(str(annual_rate)) / Decimal(str(periods_per_year)) / Decimal("100")
    N = Decimal(str(total_periods))
    if R == 0:
        return (P / N).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    factor = (Decimal("1") + R) ** N
    emi = (P * R * factor) / (factor - Decimal("1"))
    return emi.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def calculate_remaining_periods(
    remaining_balance,
    annual_rate,
    emi,
    frequency="monthly",
):
    """
    Estimate remaining months given current balance and EMI.

    Derived by inverting the EMI formula to solve for N.
    """
    balance = Decimal(str(remaining_balance))
    _, periods_per_year = get_period_details(frequency)

    R = Decimal(str(annual_rate)) / Decimal(str(periods_per_year)) / Decimal("100")
    E = Decimal(str(emi))

    if balance <= Decimal("0.01") or E <= 0:
        return 0

    if R == 0:
        return int(balance / E) + (1 if balance % E > 0 else 0)

    ratio = float(balance * R / E)
    if ratio >= 1:
        return 999  # EMI too small, effectively infinite

    n = -math.log(1 - ratio) / math.log(1 + float(R))
    return max(0, int(math.ceil(n)))


def add_months(source_date, months):
    """
    Add a given number of months to a date, handling year rollover
    and end-of-month edge cases (e.g., Jan 31 + 1 month = Feb 28/29).
    """
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    day = min(source_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def get_period_details(frequency):
    """
    Returns:
        months_per_period
        periods_per_year
    """
    mapping = {
        "monthly": (1, 12),
        "quarterly": (3, 4),
        "half_yearly": (6, 2),
        "yearly": (12, 1),
    }
    return mapping.get(frequency or "monthly", (1, 12))


def add_periods(source_date, period_number, frequency):
    """
    Add EMI periods according to frequency.
    """
    months_per_period, _ = get_period_details(frequency)
    return add_months(source_date, months_per_period * period_number)


def generate_full_schedule(loan):
    """
    Generate the complete amortization schedule for a loan.

    Past payments use actual recorded data.
    Future months are projected from the current remaining balance.

    Returns a list of dicts with monthly breakdown.
    """
    frequency = getattr(loan, "emi_frequency", "monthly")
    _, periods_per_year = get_period_details(frequency)

    R = (
        Decimal(str(loan.interest_rate))
        / Decimal(str(periods_per_year))
        / Decimal("100")
    )
    emi = Decimal(str(loan.emi))

    # Build a map of past payments by payment_number
    paid_payments = {
        p.payment_number: p
        for p in loan.payments.filter(status="paid").order_by("payment_number")
    }

    schedule = []
    balance = Decimal(str(loan.amount))
    max_periods = (loan.tenure_years * periods_per_year) + 20
    for month_num in range(1, max_periods + 1):
        if balance <= Decimal("0.01"):
            break

        interest = balance * R
        principal = emi - interest
        # Last payment: principal can't exceed balance
        if principal >= balance:
            principal = balance
            actual_emi = principal + interest
        else:
            actual_emi = emi
        new_balance = balance - principal
        if new_balance < 0:
            new_balance = Decimal("0.00")
        due_date = add_periods(loan.schedule_start_date, month_num - 1, frequency)

        # Determine if this month has an actual payment
        is_paid = month_num in paid_payments
        actual_payment = paid_payments[month_num] if is_paid else None

        # If there's a prepayment before this month, adjust balance
        prepayments_before = loan.prepayments.filter(
            prepayment_date__lte=due_date
        ).order_by("prepayment_date")

        # Simple approach: subtract prepayments from projected balance
        for prep in prepayments_before:
            if not hasattr(prep, "_applied"):
                new_balance = max(new_balance - prep.amount, Decimal("0.00"))
                prep._applied = True  # Temporary flag, won't persist
        from loans.models import LoanAccruedInterest

        additional_interest = LoanAccruedInterest.objects.filter(
            loan=loan,
            emi_date=due_date,
            status="pending",
        ).aggregate(total=Sum("interest_amount"))["total"] or Decimal("0")
        total_debit = emi + additional_interest
        row = {
            "period": month_num,
            "regular_emi": actual_emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "principal": principal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "interest": interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "balance": new_balance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "due_date": due_date,
            "status": "paid" if is_paid else "projected",
            "payment_date": actual_payment.payment_date if is_paid else None,
            "total_debit": total_debit,
            "additional_interest": additional_interest,
        }
        schedule.append(row)
        balance = new_balance
    return schedule


def generate_projected_schedule(loan):
    """
    Generate projected amortization from the CURRENT remaining balance.
    Used for charts and future projections.
    """
    balance = Decimal(str(loan.remaining_balance))
    frequency = getattr(loan, "emi_frequency", "monthly")
    _, periods_per_year = get_period_details(frequency)

    R = (
        Decimal(str(loan.interest_rate))
        / Decimal(str(periods_per_year))
        / Decimal("100")
    )
    emi = Decimal(str(loan.emi))
    start_month = loan.payments.filter(status="paid").count()

    schedule = []
    month_num = start_month + 1
    max_periods = (loan.tenure_years * periods_per_year) + 20
    for _ in range(max_periods):
        if balance <= Decimal("0.01"):
            break

        interest = balance * R
        principal = emi - interest
        if principal >= balance:
            principal = balance
            actual_emi = principal + interest
        else:
            actual_emi = emi
        balance = max(balance - principal, Decimal("0.00"))
        due_date = add_periods(loan.schedule_start_date, month_num - 1, frequency)
        schedule.append(
            {
                "period": month_num,
                "emi": actual_emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                "principal": principal.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "interest": interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                "balance": balance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                "due_date": due_date,
            }
        )
        month_num += 1
    return schedule


def simulate_extra_emi(loan, extra_emi):
    """
    Simulate paying an additional fixed amount with every EMI.

    Returns:
        {
            months_saved,
            interest_saved,
            new_payoff_periods,
            total_interest
        }
    """
    balance = Decimal(str(loan.remaining_balance))

    if balance <= Decimal("0.01"):
        return None

    frequency = getattr(loan, "emi_frequency", "monthly")
    _, periods_per_year = get_period_details(frequency)

    rate = (
        Decimal(str(loan.interest_rate))
        / Decimal(str(periods_per_year))
        / Decimal("100")
    )

    emi = Decimal(str(loan.emi))
    extra = Decimal(str(extra_emi or 0))

    payment = emi + extra

    if payment <= 0:
        return None

    total_interest = Decimal("0")
    periods = 0

    while balance > Decimal("0.01"):
        interest = balance * rate
        principal = payment - interest

        if principal <= 0:
            break

        if principal > balance:
            principal = balance

        balance -= principal
        total_interest += interest
        periods += 1

    current_periods = loan.months_remaining

    remaining_interest = (Decimal(str(loan.emi)) * current_periods) - Decimal(
        str(loan.remaining_balance)
    )

    return {
        "months_saved": max(current_periods - periods, 0),
        "interest_saved": max(
            remaining_interest - total_interest,
            Decimal("0"),
        ).quantize(Decimal("0.01")),
        "new_payoff_periods": periods,
        "total_interest": total_interest.quantize(Decimal("0.01")),
    }


def compare_loans(loans):
    """
    Compare multiple loans side by side.
    Returns a list of dicts with key metrics for each loan.
    """
    comparison = []
    for loan in loans:
        comparison.append(
            {
                "loan": loan,
                "emi": float(loan.emi),
                "total_interest": float(loan.total_interest_projected),
                "total_payable": float(loan.total_payable),
                "interest_ratio": (
                    round(
                        float(loan.total_interest_projected / loan.total_payable * 100),
                        1,
                    )
                    if loan.total_payable > 0
                    else 0
                ),
                "months_remaining": loan.months_remaining,
                "health_score": loan.health_score,
                "progress": loan.progress_percent,
                "effective_rate": round(float(loan.interest_rate), 1),
            }
        )
    # Sort by total interest (worst first)
    comparison.sort(key=lambda x: x["total_interest"], reverse=True)
    return comparison


def calculate_foreclosure(loan):
    """
    Calculate foreclosure details.

    Returns:
        {
            "outstanding_balance": Decimal,
            "penalty": Decimal,
            "total_amount": Decimal,
            "interest_saved": Decimal,
            "remaining_periods": int,
        }
    """
    balance = Decimal(str(loan.remaining_balance))
    if balance <= 0:
        return {
            "outstanding_balance": Decimal("0.00"),
            "penalty": Decimal("0.00"),
            "total_amount": Decimal("0.00"),
            "interest_saved": Decimal("0.00"),
            "remaining_periods": 0,
        }

    _, periods_per_year = get_period_details(loan.emi_frequency)
    rate_per_period = (
        Decimal(str(loan.interest_rate))
        / Decimal("100")
        / Decimal(str(periods_per_year))
    )
    emi = Decimal(str(loan.emi))
    remaining_periods = max(0, int(loan.months_remaining))
    future_interest = Decimal("0")
    temp_balance = balance
    for _ in range(remaining_periods):
        if temp_balance <= 0:
            break
        interest = temp_balance * rate_per_period
        future_interest += interest
        principal = emi - interest
        if principal <= 0:
            break
        principal = min(principal, temp_balance)
        temp_balance -= principal
    penalty = balance * Decimal("0.02")
    total_foreclosure = balance + penalty
    return {
        "outstanding_balance": balance.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        "penalty": penalty.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_amount": total_foreclosure.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        "interest_saved": future_interest.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        "remaining_periods": remaining_periods,
    }
