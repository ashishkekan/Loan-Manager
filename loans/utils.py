"""
Utility functions for EMI calculation and amortization scheduling.
These are pure functions with no side effects — easy to test and reuse.
"""

import calendar
import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, getcontext

# High precision for financial calculations
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

    return add_months(
        source_date,
        months_per_period * period_number,
    )


def generate_full_schedule(loan):
    """
    Generate the complete amortization schedule for a loan.

    Past payments use actual recorded data.
    Future months are projected from the current remaining balance.

    Returns a list of dicts with monthly breakdown.
    """
    frequency = getattr(
        loan,
        "emi_frequency",
        "monthly",
    )
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
    frequency = getattr(
        loan,
        "emi_frequency",
        "monthly",
    )
    _, periods_per_year = get_period_details(frequency)

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

        due_date = add_periods(
            loan.schedule_start_date,
            month_num - 1,
            getattr(
                loan,
                "emi_frequency",
                "monthly",
            ),
        )

        # Determine if this month has an actual payment
        is_paid = month_num in paid_payments
        actual_payment = paid_payments[month_num] if is_paid else None

        # If there's a prepayment before this month, adjust balance
        prepayments_before = loan.prepayments.filter(
            prepayment_date__lte=due_date
        ).order_by("prepayment_date")

        # Simple approach: subtract prepayments from projected balance
        for prep in prepayments_before:
            # Only count prepayments not already factored in
            if not hasattr(prep, "_applied"):
                new_balance = max(new_balance - prep.amount, Decimal("0.00"))
                prep._applied = True  # Temporary flag, won't persist

        row = {
            "period": month_num,
            "emi": actual_emi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "principal": principal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "interest": interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "balance": new_balance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "due_date": due_date,
            "status": "paid" if is_paid else "projected",
            "payment_date": actual_payment.payment_date if is_paid else None,
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
    frequency = getattr(
        loan,
        "emi_frequency",
        "monthly",
    )
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
        frequency = getattr(
            loan,
            "emi_frequency",
            "monthly",
        )
        due_date = add_periods(
            loan.schedule_start_date,
            month_num - 1,
            frequency,
        )

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
    Calculate the exact amount needed to close the loan today.
    Includes standard 2% foreclosure penalty and projects interest saved.
    """
    balance = Decimal(str(loan.remaining_balance))

    # Standard foreclosure penalty is 2% of remaining principal
    penalty = balance * Decimal("0.02")
    total_foreclosure_amount = balance + penalty

    # Calculate interest saved if closed today vs waiting for full tenure
    frequency = getattr(
        loan,
        "emi_frequency",
        "monthly",
    )
    _, periods_per_year = get_period_details(frequency)

    R = (
        Decimal(str(loan.interest_rate))
        / Decimal(str(periods_per_year))
        / Decimal("100")
    )
    emi = Decimal(str(loan.emi))

    projected_interest = Decimal("0")
    temp_balance = balance
    max_periods = (loan.tenure_years * periods_per_year) + 20
    for _ in range(max_periods):
        if temp_balance <= Decimal("0.01"):
            break
        interest = temp_balance * R
        projected_interest += interest
        principal = emi - interest
        if principal >= temp_balance:
            break
        temp_balance -= principal

    return {
        "outstanding_balance": balance.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        "penalty": penalty.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_amount": total_foreclosure_amount.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        "interest_saved": projected_interest.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
    }
