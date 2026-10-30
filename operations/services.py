"""Authoritative, atomic transitions for financial operations."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from loans.models import Loan, LoanDisbursement
from loans.utils import (
    add_periods,
    calculate_emi,
    create_notification,
    get_period_details,
)
from payments.models import Payment, Prepayment

from .access import application_scope, can_operate
from .models import Application, Charge, LoanRevision, Receipt, Reversal

ZERO = Decimal("0.00")


def money(v):
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def permit(user, permission):
    if not user.is_active or not user.has_perm("operations." + permission):
        raise PermissionDenied(
            "This action requires an assigned operations permission."
        )


def checker(user, maker, permission):
    permit(user, permission)
    if user.pk == maker:
        raise ValidationError(
            "A different authorised person must approve this request."
        )


def next_due(loan, number):
    if loan.schedule_anchor and number > loan.schedule_offset:
        return add_periods(
            loan.schedule_anchor, number - loan.schedule_offset - 1, loan.emi_frequency
        )
    return add_periods(loan.schedule_start_date, number - 1, loan.emi_frequency)


def loan_state(loan):
    return {
        k: str(getattr(loan, k)) if getattr(loan, k) is not None else None
        for k in (
            "remaining_balance",
            "total_interest_paid",
            "status",
            "closed_date",
            "interest_rate",
            "emi",
            "schedule_version",
            "capitalized_interest",
        )
    }


def payment_state(payment):
    if payment is None:
        return None
    return {
        k: str(getattr(payment, k)) if getattr(payment, k) is not None else None
        for k in (
            "id",
            "amount",
            "principal_component",
            "interest_component",
            "balance_after",
            "payment_date",
            "status",
            "regular_emi_amount",
            "total_debit_amount",
        )
    }


def finish(loan, day):
    if loan.remaining_balance == 0 and not loan.charges.filter(status="due").exists():
        loan.status, loan.closed_date = "closed", day
        create_notification(
            loan.user,
            "Loan Fully Repaid",
            f"{loan.loan_name} has been fully repaid.",
            "loan",
            loan,
        )
    loan.save()


@transaction.atomic
def verify_receipt(pk, user, approve=True, remarks=""):
    # Always lock the loan first, for consistent ordering between posting/reversal.
    ref = Receipt.objects.get(pk=pk)
    loan = Loan.objects.select_for_update().get(pk=ref.loan_id)
    receipt = Receipt.objects.select_for_update().get(pk=pk)
    can_operate(user, loan)
    checker(user, receipt.submitted_by_id, "verify_receipt")
    if receipt.status != "pending":
        raise ValidationError("Receipt has already been reviewed.")
    if not approve:
        if not remarks.strip():
            raise ValidationError("A rejection reason is required.")
        receipt.status, receipt.reviewer, receipt.remarks = "rejected", user, remarks
        receipt.save()
        return receipt
    if receipt.amount <= 0:
        raise ValidationError("Payment amount must be positive.")
    if loan.status == "closed":
        raise ValidationError("Cannot post against a closed loan.")
    if not loan.start_date <= receipt.received_on <= timezone.localdate():
        raise ValidationError("Payment date must be between loan start and today.")
    if loan.revisions.filter(
        status="approved", effective_from__lte=timezone.localdate()
    ).exists():
        raise ValidationError(
            "Apply the approved schedule change before posting payments."
        )
    latest = (
        loan.receipts.filter(status="verified").order_by("-verified_at", "-id").first()
    )
    if latest and receipt.received_on < latest.received_on:
        raise ValidationError(
            "Backdated posting before the latest receipt is not allowed."
        )
    receipt.before = {"loan": loan_state(loan)}
    if receipt.kind == "charge":
        charge = Charge.objects.select_for_update().get(pk=receipt.charge_id, loan=loan)
        if charge.status != "due" or charge.amount != receipt.amount:
            raise ValidationError("Select an unpaid charge for its exact amount.")
        charge.status = "paid"
        charge.save()
    elif receipt.kind == "prepayment":
        if loan.payments.filter(status="partial").exists():
            raise ValidationError("Complete the partial EMI before prepayment.")
        if receipt.amount > loan.remaining_balance:
            raise ValidationError("Prepayment exceeds outstanding principal.")
        from loans.utils import calculate_remaining_periods, simulate_extra_emi

        old_periods = calculate_remaining_periods(
            loan.remaining_balance, loan.interest_rate, loan.emi, loan.emi_frequency
        )
        new_balance = money(loan.remaining_balance - receipt.amount)
        new_periods = calculate_remaining_periods(
            new_balance, loan.interest_rate, loan.emi, loan.emi_frequency
        )
        months = (old_periods - new_periods) * get_period_details(loan.emi_frequency)[0]

        # Compare amortized future interest, rather than multiplying months by periodic interest.
        def projected_interest(balance):
            total = ZERO
            rate = loan.interest_rate / Decimal(
                100 * get_period_details(loan.emi_frequency)[1]
            )
            for _ in range(1000):
                if balance <= 0:
                    return total
                interest = money(balance * rate)
                principal = min(balance, loan.emi - interest)
                if principal <= 0:
                    raise ValidationError("EMI does not cover interest.")
                total += interest
                balance -= principal
            raise ValidationError("Schedule exceeds supported length.")

        saved = max(
            ZERO,
            projected_interest(loan.remaining_balance)
            - projected_interest(new_balance),
        )
        receipt.prepayment = Prepayment.objects.create(
            loan=loan,
            amount=receipt.amount,
            prepayment_date=receipt.received_on,
            months_reduced=max(0, months),
            interest_saved=saved,
        )
        loan.remaining_balance = new_balance
    else:
        payment = loan.payments.filter(status="partial").first()
        receipt.before["payment"] = payment_state(payment)
        if payment is None:
            number = loan.payments.filter(status="paid").count() + 1
            due = next_due(loan, number)
            if receipt.received_on < due:
                raise ValidationError(
                    "Use prepayment for a payment before the EMI due date."
                )
            if loan.remaining_balance <= 0:
                raise ValidationError("No principal remains.")
            rate = loan.interest_rate / Decimal(
                100 * get_period_details(loan.emi_frequency)[1]
            )
            interest = money(loan.remaining_balance * rate)
            target = min(loan.emi, money(loan.remaining_balance + interest))
            if target <= interest:
                raise ValidationError("EMI must cover interest and principal.")
            payment = loan.payments.filter(payment_number=number).first()
            if payment and payment.status not in ("pending", "overdue"):
                raise ValidationError("Existing instalment must be resolved first.")
            if payment:
                receipt.before["payment"] = payment_state(payment)
            else:
                payment = Payment(loan=loan, payment_number=number)
            payment.amount = payment.principal_component = (
                payment.interest_component
            ) = ZERO
            payment.regular_emi_amount = target
            payment.total_debit_amount = target
            payment.balance_after = loan.remaining_balance
            payment.due_date = due
            payment.status = "partial"
            payment.save()
        remaining = money(payment.total_debit_amount - payment.amount)
        if receipt.amount > remaining:
            raise ValidationError(
                f"This EMI has {remaining} remaining. Submit excess separately as prepayment."
            )
        # Interest-first allocation, retaining the original instalment interest target.
        original_balance = loan.remaining_balance + payment.principal_component
        target_interest = money(
            original_balance
            * loan.interest_rate
            / Decimal(100 * get_period_details(loan.emi_frequency)[1])
        )
        interest = min(
            receipt.amount, max(ZERO, target_interest - payment.interest_component)
        )
        principal = money(receipt.amount - interest)
        payment.amount += receipt.amount
        payment.interest_component += interest
        payment.principal_component += principal
        loan.remaining_balance = money(loan.remaining_balance - principal)
        loan.total_interest_paid = money(loan.total_interest_paid + interest)
        payment.balance_after = loan.remaining_balance
        payment.payment_date = receipt.received_on
        payment.status = (
            "paid" if payment.amount == payment.total_debit_amount else "partial"
        )
        payment.save()
        receipt.payment = payment
    finish(loan, receipt.received_on)
    receipt.status, receipt.reviewer, receipt.remarks = "verified", user, remarks
    receipt.verified_at = timezone.now()
    receipt.after = {
        "loan": loan_state(loan),
        "payment": payment_state(receipt.payment),
    }
    receipt.save()
    from .advanced import allocate_receipt

    allocate_receipt(receipt)
    create_notification(
        loan.user,
        "Payment verified",
        f"INR {receipt.amount} received for {loan.loan_name}. Receipt {receipt.number}.",
        "payment",
        loan,
    )
    return receipt


@transaction.atomic
def approve_reversal(pk, user, approve=True):
    ref = Reversal.objects.get(pk=pk)
    loan = Loan.objects.select_for_update().get(pk=ref.receipt.loan_id)
    request = Reversal.objects.select_for_update().get(pk=pk)
    can_operate(user, loan)
    checker(user, request.requested_by_id, "approve_reversal")
    if request.status != "pending":
        raise ValidationError("Already reviewed.")
    if not approve:
        request.status, request.reviewer = "rejected", user
        request.save()
        return
    receipt = Receipt.objects.select_for_update().get(pk=request.receipt_id)
    if receipt.status != "verified":
        raise ValidationError("Only verified receipts can be reversed.")
    if loan.collateral.filter(released_at__isnull=False).exists():
        raise ValidationError(
            "Collateral has been released. Resolve the release before reopening this loan through reversal."
        )
    from .models import Payout

    if Payout.objects.filter(allocation__receipt=receipt).exists():
        raise ValidationError(
            "Resolve allocated lender payouts before reversing this receipt."
        )
    latest = (
        loan.receipts.filter(status="verified").order_by("-verified_at", "-id").first()
    )
    if latest.pk != receipt.pk:
        raise ValidationError(
            "Reverse later receipts first, newest to oldest, to preserve accounting order."
        )
    if loan_state(loan) != receipt.after["loan"]:
        raise ValidationError(
            "Loan terms or balance changed since this receipt. Resolve later changes before reversal."
        )
    if receipt.payment and payment_state(receipt.payment) != receipt.after["payment"]:
        raise ValidationError("The instalment has changed since posting.")
    for key, value in receipt.before["loan"].items():
        setattr(loan, key, value)
    loan.save()
    if receipt.payment:
        previous = receipt.before.get("payment")
        if previous:
            for key, value in previous.items():
                setattr(receipt.payment, key, value)
        else:
            receipt.payment.amount = receipt.payment.principal_component = (
                receipt.payment.interest_component
            ) = ZERO
            receipt.payment.balance_after = loan.remaining_balance
            receipt.payment.payment_date = None
            receipt.payment.status = "pending"
        receipt.payment.save()
    if receipt.prepayment:
        receipt.prepayment.status = "pending"
        receipt.prepayment.save()
    if receipt.charge:
        receipt.charge.status = "due"
        receipt.charge.save()
    receipt.status = "reversed"
    receipt.save()
    request.status, request.reviewer = "approved", user
    request.save()


@transaction.atomic
def review_application(pk, user, action, remarks, identity=False, income=False):
    app = (
        Application.objects.select_for_update()
        .filter(application_scope(user))
        .get(pk=pk)
    )
    checker(user, app.applicant_id, "approve_application")
    if app.status not in ("submitted", "review"):
        raise ValidationError("Application is not awaiting review.")
    if action not in ("review", "approved", "rejected"):
        raise ValidationError("Invalid decision.")
    if not remarks.strip():
        raise ValidationError("Review comments are required.")
    if action == "approved":
        if not identity or not income:
            raise ValidationError("Complete the identity and income checklist.")
        kinds = set(app.attachments.values_list("kind", flat=True))
        if not {"identity", "income"}.issubset(kinds):
            raise ValidationError(
                "Identity and income documents must be uploaded before approval."
            )
        if app.first_emi_date < timezone.localdate():
            raise ValidationError(
                "First EMI date is in the past; return for correction."
            )
        app.loan = Loan.objects.create(
            organization=app.organization,
            branch=app.branch,
            user=app.applicant,
            loan_name=app.loan_name,
            loan_type=app.loan_type,
            amount=app.amount,
            interest_rate=app.interest_rate,
            tenure_years=app.tenure_years,
            emi=calculate_emi(
                app.amount, app.interest_rate, app.tenure_years, app.frequency
            ),
            start_date=timezone.localdate(),
            first_emi_date=app.first_emi_date,
            emi_frequency=app.frequency,
            remaining_balance=ZERO,
            auto_debit=False,
        )
    app.status, app.remarks, app.reviewer = action, remarks, user
    app.identity_checked, app.income_checked = identity, income
    app.save()
    return app


def amortize(balance, rate, periods, frequency):
    periodic = rate / Decimal(100 * get_period_details(frequency)[1])
    if periodic:
        factor = (1 + periodic) ** periods
        emi = money(balance * periodic * factor / (factor - 1))
    else:
        emi = money(balance / periods)
    return emi


@transaction.atomic
def review_revision(pk, user, approve=True):
    revision = LoanRevision.objects.select_for_update().get(pk=pk)
    can_operate(user, revision.loan)
    checker(user, revision.requested_by_id, "approve_revision")
    if revision.status != "pending":
        raise ValidationError("Already reviewed.")
    if revision.loan.revisions.filter(status="approved").exists():
        raise ValidationError("Another approved revision is waiting to take effect.")
    revision.status, revision.reviewer = ("approved" if approve else "rejected"), user
    revision.save()
    if approve and revision.effective_from <= timezone.localdate():
        apply_revision(revision.pk)


@transaction.atomic
def apply_revision(pk):
    ref = LoanRevision.objects.get(pk=pk)
    loan = Loan.objects.select_for_update().get(pk=ref.loan_id)
    rev = LoanRevision.objects.select_for_update().get(pk=pk)
    if rev.status != "approved" or rev.effective_from > timezone.localdate():
        return
    if loan.status != "active" or loan.remaining_balance <= 0:
        raise ValidationError("Revision requires an active outstanding balance.")
    if (
        loan.payments.filter(status="partial").exists()
        or loan.receipts.filter(status="pending").exists()
    ):
        raise ValidationError(
            "Resolve partial EMIs and pending receipts before applying the revision."
        )
    number = loan.payments.filter(status="paid").count() + 1
    due = next_due(loan, number)
    if rev.kind != "holiday" and rev.next_due != due:
        raise ValidationError("Only a holiday may defer the next due date.")
    if rev.kind == "holiday" and rev.next_due <= due:
        raise ValidationError("Holiday must defer the next due date.")
    if rev.next_due < rev.effective_from:
        raise ValidationError("Next due date must be on or after the effective date.")
    from loans.utils import generate_full_schedule

    rev.before = {
        "loan": loan_state(loan),
        "schedule": [
            {k: str(v) for k, v in row.items() if k not in ("loan", "payment")}
            for row in generate_full_schedule(loan)
        ],
    }
    if rev.kind == "holiday":
        interest = money(
            loan.remaining_balance
            * loan.interest_rate
            / Decimal(100)
            * Decimal((rev.next_due - due).days)
            / Decimal(365)
        )
        loan.capitalized_interest += interest
        loan.remaining_balance += interest
    loan.interest_rate = rev.rate
    loan.emi = amortize(
        loan.remaining_balance, rev.rate, rev.periods, loan.emi_frequency
    )
    rev.before["new_emi"] = str(loan.emi)
    loan.schedule_version += 1
    loan.schedule_offset, loan.schedule_anchor = number - 1, rev.next_due
    loan.save()
    rev.schedule = [
        {k: str(v) for k, v in row.items() if k not in ("loan", "payment")}
        for row in generate_full_schedule(loan)
    ]
    rev.status = "applied"
    rev.save()


@transaction.atomic
def waive_charge(pk, user):
    charge = Charge.objects.select_for_update().get(pk=pk)
    can_operate(user, charge.loan)
    if not charge.waiver_by_id or not charge.waiver_reason:
        raise ValidationError("A waiver request is required.")
    checker(user, charge.waiver_by_id, "approve_waiver")
    if charge.status != "due":
        raise ValidationError("Charge is not due.")
    charge.status, charge.reviewer = "waived", user
    charge.save()
