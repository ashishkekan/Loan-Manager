"""
Utility functions for EMI calculation and amortization scheduling.
These are pure functions with no side effects — easy to test and reuse.
"""

import calendar
import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, getcontext

from django.db.models import Avg, Sum
from django.utils import timezone

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


def build_paid_schedule(loan):
    """
    Returns all paid EMIs indexed by payment_number.
    """
    paid_rows = {}
    payments = (
        loan.payments.select_related("loan")
        .filter(status="paid")
        .order_by("payment_number")
    )
    for payment in payments:
        paid_rows[payment.payment_number] = {
            "period": payment.payment_number,
            "loan": loan,
            "payment": payment,
            "due_date": payment.due_date,
            "payment_date": payment.payment_date,
            "status": "paid",
            "regular_emi": Decimal(str(payment.regular_emi_amount)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            "principal": Decimal(str(payment.principal_component)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            "interest": Decimal(str(payment.interest_component)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            "additional_interest": Decimal(
                str(payment.additional_interest or 0)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_debit": Decimal(str(payment.total_debit_amount)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            "balance": Decimal(str(payment.balance_after)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            "payment_mode": payment.payment_mode,
            "payment_type": payment.payment_type,
            "is_paid": True,
            "is_pending": False,
            "is_overdue": False,
            "is_projected": False,
        }
    return paid_rows


def build_projected_schedule(loan):
    """
    Generates projected EMIs from current loan state.
    Does not use Payment table.
    """
    from loans.models import LoanAccruedInterest

    frequency = getattr(loan, "emi_frequency", "monthly")
    _, periods_per_year = get_period_details(frequency)
    rate = (
        Decimal(str(loan.interest_rate))
        / Decimal(str(periods_per_year))
        / Decimal("100")
    )
    emi = Decimal(str(loan.emi))
    balance = Decimal(str(loan.amount))
    today = date.today()
    accrued_lookup = {
        obj.emi_date: obj for obj in LoanAccruedInterest.objects.filter(loan=loan)
    }
    prepayments = list(loan.prepayments.order_by("prepayment_date"))
    prepayment_index = 0
    rows = {}
    total_periods = (loan.tenure_years * periods_per_year) + 20
    for period in range(1, total_periods + 1):
        if balance <= Decimal("0.01"):
            break
        due_date = add_periods(loan.schedule_start_date, period - 1, frequency)
        while (
            prepayment_index < len(prepayments)
            and prepayments[prepayment_index].prepayment_date <= due_date
        ):
            balance = max(
                Decimal("0.00"),
                balance - Decimal(str(prepayments[prepayment_index].amount)),
            )
            prepayment_index += 1
        interest = (balance * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        principal = (emi - interest).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        current_emi = emi
        if principal >= balance:
            principal = balance
            current_emi = (principal + interest).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        projected_balance = max(Decimal("0.00"), balance - principal)
        accrued = accrued_lookup.get(due_date)
        additional_interest = (
            Decimal(str(accrued.interest_amount)) if accrued else Decimal("0.00")
        )
        total_debit = (current_emi + additional_interest).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        status = "pending" if due_date >= today else "overdue"
        rows[period] = {
            "period": period,
            "loan": loan,
            "payment": None,
            "due_date": due_date,
            "payment_date": None,
            "status": status,
            "regular_emi": current_emi,
            "principal": principal,
            "interest": interest,
            "additional_interest": additional_interest.quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            ),
            "total_debit": total_debit,
            "balance": projected_balance,
            "payment_mode": ("auto_debit" if loan.auto_debit else "manual"),
            "payment_type": "emi",
            "is_paid": False,
            "is_pending": status == "pending",
            "is_overdue": status == "overdue",
            "is_projected": True,
        }
        balance = projected_balance
    return rows


def merge_schedule(loan):
    """
    Merge paid schedule with projected schedule.

    Paid payments always override projected rows.
    """
    projected = build_projected_schedule(loan)
    paid = build_paid_schedule(loan)
    projected.update(paid)
    schedule = list(projected.values())
    schedule.sort(key=lambda row: (row["period"], row["due_date"]))
    return schedule


def generate_full_schedule(loan):
    """
    Public API used by dashboard, reports,
    analytics and payment history.
    """
    return merge_schedule(loan)


def generate_projected_schedule(loan):
    """
    Returns only future pending EMIs.

    Used for:
        • Dashboard Upcoming EMI
        • Payment Forecast
        • Charts
    """
    today = date.today()
    return [
        row
        for row in generate_full_schedule(loan)
        if row["status"] == "pending" and row["due_date"] >= today
    ]


def get_next_emi(loan):
    """
    Returns next payable EMI for a loan.
    """
    projected = generate_projected_schedule(loan)
    return projected[0] if projected else None


def get_overdue_emis(loan):
    """
    Returns all overdue EMIs.
    """
    return [row for row in generate_full_schedule(loan) if row["status"] == "overdue"]


def get_schedule_summary(loan):
    """
    Dashboard helper.

    Returns payment summary generated from schedule instead
    of depending only on Payment records.
    """
    schedule = generate_full_schedule(loan)

    paid = 0
    pending = 0
    overdue = 0

    paid_amount = Decimal("0.00")
    pending_amount = Decimal("0.00")
    overdue_amount = Decimal("0.00")

    principal_paid = Decimal("0.00")
    interest_paid = Decimal("0.00")
    additional_interest = Decimal("0.00")

    for row in schedule:
        if row["status"] == "paid":
            paid += 1
            paid_amount += row["total_debit"]
            principal_paid += row["principal"]
            interest_paid += row["interest"]
            additional_interest += row["additional_interest"]
        elif row["status"] == "pending":
            pending += 1
            pending_amount += row["total_debit"]
        else:
            overdue += 1
            overdue_amount += row["total_debit"]
    return {
        "paid": paid,
        "pending": pending,
        "overdue": overdue,
        "paid_amount": paid_amount.quantize(Decimal("0.01")),
        "pending_amount": pending_amount.quantize(Decimal("0.01")),
        "overdue_amount": overdue_amount.quantize(Decimal("0.01")),
        "principal_paid": principal_paid.quantize(Decimal("0.01")),
        "interest_paid": interest_paid.quantize(Decimal("0.01")),
        "additional_interest": additional_interest.quantize(Decimal("0.01")),
    }


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


def create_notification(user, title, message, notification_type="system", loan=None):
    from loans.models import Notification

    return Notification.objects.create(
        user=user,
        loan=loan,
        title=title,
        message=message,
        notification_type=notification_type,
    )


def get_support_ticket_summary(user):

    from loans.models import SupportTicket

    tickets = SupportTicket.objects.filter(user=user)
    total = tickets.count()
    open_count = tickets.filter(status__in=["open", "in_progress"]).count()
    resolved_count = tickets.filter(status__in=["resolved", "closed"]).count()
    response_times = []
    for ticket in tickets:
        if ticket.last_response_at:
            response_time = (
                ticket.last_response_at - ticket.created_at
            ).total_seconds() / 3600
            response_times.append(response_time)
    if response_times:
        average_hours = sum(response_times) / len(response_times)
        if average_hours < 1:
            avg_response_time = f"{round(average_hours * 60)} Minutes"
        else:
            avg_response_time = f"{round(average_hours, 1)} Hours"
    else:
        avg_response_time = "—"
    return {
        "total": total,
        "open": open_count,
        "resolved": resolved_count,
        "avg_response_time": avg_response_time,
    }


def ensure_user_settings(user):
    from loans.models import (
        AppearancePreference,
        NotificationPreference,
        PrivacySetting,
        SecuritySetting,
        UserProfile,
    )

    profile, _ = UserProfile.objects.get_or_create(user=user)
    notification_preferences, _ = NotificationPreference.objects.get_or_create(
        user=user
    )
    appearance_preferences, _ = AppearancePreference.objects.get_or_create(user=user)
    privacy_settings, _ = PrivacySetting.objects.get_or_create(user=user)
    security_settings, _ = SecuritySetting.objects.get_or_create(user=user)
    return {
        "profile": profile,
        "notification_preferences": notification_preferences,
        "appearance_preferences": appearance_preferences,
        "privacy_settings": privacy_settings,
        "security_settings": security_settings,
    }


def get_account_statistics(user):
    from loans.models import Loan
    from payments.models import Payment

    from .models import SupportTicket

    loans = Loan.objects.filter(user=user)
    total_loans = loans.count()
    active_loans = loans.filter(status="active").count()
    closed_loans = loans.filter(status="closed").count()
    total_paid = 0
    total_interest_paid = 0

    payments = Payment.objects.filter(loan__user=user, status="paid")
    total_paid = payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    total_interest_paid = payments.aggregate(total=Sum("interest_component"))[
        "total"
    ] or Decimal("0.00")
    try:
        total_support_tickets = SupportTicket.objects.filter(user=user).count()
    except Exception:
        total_support_tickets = 0
    return {
        "total_loans": total_loans,
        "active_loans": active_loans,
        "closed_loans": closed_loans,
        "total_paid": total_paid,
        "total_interest_paid": total_interest_paid,
        "total_support_tickets": total_support_tickets,
        "last_login": user.last_login,
        "account_status": "Active" if user.is_active else "Inactive",
    }
