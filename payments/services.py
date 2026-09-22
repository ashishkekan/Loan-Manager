from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from loans.services import AccruedInterestService
from loans.utils import add_periods, create_notification, next_emi_number
from payments.models import Payment


@transaction.atomic
def process_emi_payment(
    loan,
    payment_date=None,
    payment_mode="manual",
    payment_type="emi",
    expected_payment_number=None,
):
    loan = loan.__class__.objects.select_for_update().get(pk=loan.pk)
    if payment_date is None:
        payment_date = timezone.now().date()

    if payment_date < loan.schedule_start_date:
        raise ValueError("Payment date cannot be before loan start date.")

    if loan.status != "active":
        return None

    if loan.remaining_balance == Decimal("0.00"):
        loan.remaining_balance = Decimal("0.00")
        loan.status = "closed"
        loan.closed_date = payment_date
        loan.save(update_fields=["remaining_balance", "status", "closed_date"])
        create_notification(
            user=loan.user,
            title="Loan Fully Repaid",
            message=f"{loan.loan_name} has been fully repaid and is now closed.",
            notification_type="loan",
            loan=loan,
        )
        return None

    frequency = getattr(loan, "emi_frequency", "monthly")
    payment_number = next_emi_number(loan)
    if (
        expected_payment_number is not None
        and payment_number != expected_payment_number
    ):
        raise ValueError(
            "This EMI was already processed or the schedule changed. Refresh the page."
        )
    if Payment.objects.filter(
        loan=loan, payment_number=payment_number, status="paid"
    ).exists():
        return None
    due_date = add_periods(loan.schedule_start_date, payment_number - 1, frequency)
    if due_date > payment_date:
        raise ValueError(
            "Next EMI is not due yet. Use prepayment for an early principal payment."
        )
    breakup = AccruedInterestService.calculate_total_debit(loan=loan, emi_date=due_date)
    if breakup["outstanding_disbursed"] <= 0:
        raise ValueError("No released principal is outstanding for this EMI date.")
    regular_emi = breakup["regular_emi"]
    regular_interest = breakup["regular_interest"]
    total_debit = breakup["total_debit"]
    principal = (regular_emi - regular_interest).quantize(Decimal("0.01"))
    if principal <= Decimal("0.00"):
        raise ValueError("EMI is too low to cover interest.")

    if principal >= breakup["outstanding_disbursed"]:
        principal = breakup["outstanding_disbursed"]
        payment_amount = (principal + regular_interest).quantize(Decimal("0.01"))
    else:
        payment_amount = total_debit.quantize(Decimal("0.01"))
    new_balance = (loan.remaining_balance - principal).quantize(Decimal("0.01"))
    if new_balance < Decimal("0.00"):
        new_balance = Decimal("0.00")
    payment, _ = Payment.objects.update_or_create(
        loan=loan,
        payment_number=payment_number,
        defaults=dict(
            amount=payment_amount,
            principal_component=principal,
            interest_component=regular_interest,
            regular_emi_amount=regular_emi,
            total_debit_amount=payment_amount,
            balance_after=new_balance,
            due_date=due_date,
            payment_date=payment_date,
            payment_mode=payment_mode,
            payment_type=payment_type,
            status="paid",
        ),
    )
    loan.remaining_balance = new_balance
    loan.total_interest_paid += regular_interest.quantize(Decimal("0.01"))
    update_fields = ["remaining_balance", "total_interest_paid"]
    if loan.remaining_balance == Decimal("0.00"):
        loan.remaining_balance = Decimal("0.00")
        loan.status = "closed"
        loan.closed_date = payment_date
        update_fields.extend(["status", "closed_date"])
    loan.save(update_fields=update_fields)
    if loan.status == "closed":
        create_notification(
            user=loan.user,
            title="Loan Fully Repaid",
            message=f"{loan.loan_name} has been fully repaid and is now closed.",
            notification_type="loan",
            loan=loan,
        )
    return payment
