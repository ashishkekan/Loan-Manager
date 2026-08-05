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

from loans.services import AccruedInterestService
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
    payment_type="emi",
):
    """
    Process a single EMI payment.

    Returns:
        Payment instance if payment is processed.

        Returns None if:
            - Loan already closed
            - Loan already fully paid
            - Duplicate payment detected
    """

    # Lock loan row to avoid concurrent payments
    loan = loan.__class__.objects.select_for_update().get(pk=loan.pk)

    if payment_date is None:
        payment_date = timezone.now().date()

    if payment_date < loan.schedule_start_date:
        raise ValueError("Payment date cannot be before loan start date.")

    # Loan already closed
    if loan.status == "closed":
        return None

    # Already fully paid
    if (
        loan.remaining_balance == Decimal("0.00")
        and not loan.has_pending_accrued_interest
    ):
        loan.remaining_balance = Decimal("0.00")
        loan.status = "closed"
        loan.closed_date = payment_date
        loan.save(
            update_fields=[
                "remaining_balance",
                "status",
                "closed_date",
            ]
        )
        return None

    frequency = getattr(loan, "emi_frequency", "monthly")

    # Safe payment number
    last_payment = loan.payments.select_for_update().order_by("-payment_number").first()

    payment_number = 1 if last_payment is None else last_payment.payment_number + 1

    # Prevent duplicate
    if Payment.objects.filter(
        loan=loan,
        payment_number=payment_number,
        status="paid",
    ).exists():
        return None

    due_date = add_periods(
        loan.schedule_start_date,
        payment_number - 1,
        frequency,
    )

    breakup = AccruedInterestService.calculate_total_debit(
        loan=loan,
        emi_date=due_date,
    )

    regular_emi = breakup["regular_emi"]
    regular_interest = breakup["regular_interest"]
    additional_interest = breakup["additional_interest"]
    total_debit = breakup["total_debit"]

    principal = (regular_emi - regular_interest).quantize(Decimal("0.01"))

    if principal <= Decimal("0.00"):
        raise ValueError("EMI is too low to cover interest.")

    # Last EMI adjustment
    if principal >= loan.remaining_balance:
        principal = loan.remaining_balance

        payment_amount = (principal + regular_interest + additional_interest).quantize(
            Decimal("0.01")
        )
    else:
        payment_amount = total_debit.quantize(Decimal("0.01"))

    new_balance = (loan.remaining_balance - principal).quantize(Decimal("0.01"))

    if new_balance < Decimal("0.00"):
        new_balance = Decimal("0.00")

    payment = Payment.objects.create(
        loan=loan,
        payment_number=payment_number,
        amount=payment_amount,
        principal_component=principal,
        interest_component=regular_interest,
        regular_emi_amount=regular_emi,
        additional_interest=additional_interest,
        total_debit_amount=total_debit,
        balance_after=new_balance,
        due_date=due_date,
        payment_date=payment_date,
        payment_mode=payment_mode,
        payment_type=payment_type,
        status="paid",
    )

    AccruedInterestService.mark_interest_recovered(
        loan=loan,
        emi_date=due_date,
        payment=payment,
    )

    loan.remaining_balance = new_balance
    loan.total_interest_paid += (regular_interest + additional_interest).quantize(
        Decimal("0.01")
    )

    update_fields = [
        "remaining_balance",
        "total_interest_paid",
    ]

    if (
        loan.remaining_balance == Decimal("0.00")
        and not loan.has_pending_accrued_interest
    ):
        loan.remaining_balance = Decimal("0.00")
        loan.status = "closed"
        loan.closed_date = payment_date

        update_fields.extend(
            [
                "status",
                "closed_date",
            ]
        )

    loan.save(update_fields=update_fields)

    return payment
