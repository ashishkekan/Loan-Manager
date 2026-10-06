"""Atomic recording of externally confirmed repayments (no bank transfer)."""

from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from loans.accounting import (
    ZERO,
    last_transaction_date,
    money,
    outstanding,
    refresh_balance,
    schedule,
)
from loans.utils import (
    calculate_remaining_periods,
    create_notification,
    get_period_details,
)
from payments.models import Payment, Prepayment


def _validate_date(loan, value):
    if value < last_transaction_date(loan) or value > timezone.localdate():
        raise ValueError(
            "Use a date on or after the last recorded transaction and no later than today."
        )


def _notify_closed(loan):
    if loan.status == "closed":
        create_notification(
            user=loan.user,
            title="Loan Fully Repaid",
            message=f"{loan.loan_name} has been fully repaid.",
            notification_type="loan",
            loan=loan,
        )


@transaction.atomic
def process_emi_payment(
    loan,
    payment_date=None,
    payment_mode="manual",
    payment_type="emi",
    *,
    request_key,
    expected_period,
    expected_amount=None,
):
    loan = loan.__class__.objects.select_for_update().get(pk=loan.pk)
    existing = Payment.objects.filter(request_key=request_key, loan=loan).first()
    if existing:
        return existing
    if not request_key:
        raise ValueError("Payment confirmation is required.")
    if payment_mode != "manual":
        raise ValueError("Automatic collection requires a verified bank integration.")
    payment_date = payment_date or timezone.localdate()
    _validate_date(loan, payment_date)
    rows = [r for r in schedule(loan) if not r["is_paid"]]
    if loan.status != "active" or not rows:
        raise ValueError("There is no outstanding installment to record.")
    row = rows[0]
    if row["period"] != expected_period:
        raise ValueError(
            "This installment has changed. Refresh the loan before confirming."
        )
    if row["due_date"] > payment_date:
        raise ValueError(
            "This installment is not due yet. Use prepayment for an early repayment."
        )
    if expected_amount is not None and money(expected_amount) != row["total_debit"]:
        raise ValueError(
            "The amount has changed. Refresh the loan before confirming payment."
        )
    if row["principal"] <= ZERO:
        raise ValueError(
            "The installment does not cover interest. Review the loan terms."
        )
    if row["principal"] > outstanding(loan, payment_date):
        raise ValueError(
            "Installment exceeds released outstanding principal. Refresh the loan."
        )
    payment, _ = Payment.objects.update_or_create(
        loan=loan,
        payment_number=row["period"],
        defaults=dict(
            request_key=request_key,
            amount=row["total_debit"],
            principal_component=row["principal"],
            interest_component=row["interest"],
            regular_emi_amount=row["total_debit"],
            total_debit_amount=row["total_debit"],
            balance_after=outstanding(loan, payment_date) - row["principal"],
            due_date=row["due_date"],
            payment_date=payment_date,
            payment_mode=payment_mode,
            payment_type=payment_type,
            status="paid",
        ),
    )
    refresh_balance(loan, close=True)
    _notify_closed(loan)
    from dashboard.utils import add_activity

    add_activity(
        loan.user,
        "emi_paid",
        f"Installment {payment.payment_number} recorded",
        loan,
        f"₹{payment.amount:,.2f}",
    )
    return payment


@transaction.atomic
def process_prepayment(loan, amount, payment_date, *, request_key):
    loan = loan.__class__.objects.select_for_update().get(pk=loan.pk)
    existing = Prepayment.objects.filter(loan=loan, request_key=request_key).first()
    if existing:
        return existing
    _validate_date(loan, payment_date)
    amount = money(amount)
    balance = outstanding(loan, payment_date)
    if not request_key or loan.status != "active" or not ZERO < amount <= balance:
        raise ValueError(
            "Prepayment must be positive and cannot exceed released outstanding principal."
        )
    if any(not r["is_paid"] and r["due_date"] <= payment_date for r in schedule(loan)):
        raise ValueError("Record due installments before making a prepayment.")
    old = calculate_remaining_periods(
        balance, loan.interest_rate, loan.emi, loan.emi_frequency
    )
    new = calculate_remaining_periods(
        balance - amount, loan.interest_rate, loan.emi, loan.emi_frequency
    )
    months, _ = get_period_details(loan.emi_frequency)

    def interest_total(principal):
        _, ppy = get_period_details(loan.emi_frequency)
        total = ZERO
        for _ in range(1200):
            if principal <= ZERO:
                break
            interest = money(principal * loan.interest_rate / Decimal(ppy * 100))
            paid = min(principal, loan.emi - interest)
            if paid <= ZERO:
                raise ValueError("The installment must cover interest.")
            total += interest
            principal -= paid
        return total

    saved = max(ZERO, interest_total(balance) - interest_total(balance - amount))
    prepayment = Prepayment.objects.create(
        loan=loan,
        request_key=request_key,
        amount=amount,
        prepayment_date=payment_date,
        months_reduced=max(0, old - new) * months,
        interest_saved=saved,
        status="paid",
    )
    refresh_balance(loan, close=True)
    _notify_closed(loan)
    from dashboard.utils import add_activity

    add_activity(
        loan.user,
        "prepayment",
        "Prepayment recorded",
        loan,
        f"₹{prepayment.amount:,.2f}",
    )
    return prepayment
