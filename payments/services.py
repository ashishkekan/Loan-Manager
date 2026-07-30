"""
Payment service layer.

Contains reusable business logic for processing EMI payments.
This service is used by:

- Pay Now button
- Auto Debit scheduler
- Future API endpoints
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from loans.utils import (
    add_periods,
    get_period_details,
)

from .models import Payment


@transaction.atomic
def process_emi_payment(
    loan,
    payment_date=None,
    payment_mode="manual",
):
    """
    Process a single EMI payment.

    Returns:
        Payment instance if payment is processed.

        None if:
            - Loan already closed
            - Balance already zero
            - EMI already paid
    """

    if payment_date is None:
        payment_date = timezone.now().date()

    # Already closed
    if loan.status == "closed":
        return None

    if loan.remaining_balance <= Decimal("0.01"):
        loan.status = "closed"
        loan.remaining_balance = Decimal("0.00")
        loan.save(update_fields=["status", "remaining_balance"])
        return None

    frequency = getattr(
        loan,
        "emi_frequency",
        "monthly",
    )

    _, periods_per_year = get_period_details(frequency)

    period_rate = (
        Decimal(str(loan.interest_rate))
        / Decimal(str(periods_per_year))
        / Decimal("100")
    )

    payment_number = loan.payments.filter(status="paid").count() + 1

    # Prevent duplicate payment
    if Payment.objects.filter(
        loan=loan,
        payment_number=payment_number,
        status="paid",
    ).exists():
        return None

    interest = loan.remaining_balance * period_rate

    principal = Decimal(str(loan.emi)) - interest

    if principal >= loan.remaining_balance:

        principal = loan.remaining_balance
        actual_emi = principal + interest

    else:

        actual_emi = Decimal(str(loan.emi))

    new_balance = loan.remaining_balance - principal

    if new_balance < 0:
        new_balance = Decimal("0.00")

    due_date = add_periods(
        loan.schedule_start_date,
        payment_number - 1,
        frequency,
    )

    payment = Payment.objects.create(
        loan=loan,
        payment_number=payment_number,
        amount=actual_emi.quantize(Decimal("0.01")),
        principal_component=principal.quantize(Decimal("0.01")),
        interest_component=interest.quantize(Decimal("0.01")),
        balance_after=new_balance.quantize(Decimal("0.01")),
        due_date=due_date,
        payment_date=payment_date,
        status="paid",
    )

    loan.remaining_balance = new_balance.quantize(Decimal("0.01"))

    loan.total_interest_paid += interest.quantize(Decimal("0.01"))

    if loan.remaining_balance <= Decimal("0.01"):

        loan.remaining_balance = Decimal("0.00")
        loan.status = "closed"

    loan.save()

    return payment
