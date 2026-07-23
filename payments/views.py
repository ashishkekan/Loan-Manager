"""
Views for recording EMI payments and prepayments.
These are POST-only actions that redirect back to the loan detail page.
"""

import math
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import TemplateView

from loans.models import Loan
from loans.utils import add_months, calculate_remaining_months

from .forms import PrepaymentForm
from .models import Payment, Prepayment


def pay_emi(request, loan_id):
    """
    Process a single EMI payment for the given loan.
    Calculates interest/principal split and updates the loan balance.
    """
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)

    if loan.status == "closed":
        messages.warning(request, "This loan is already closed.")
        return redirect("loan_detail", pk=loan_id)

    if loan.remaining_balance <= Decimal("0.01"):
        loan.status = "closed"
        loan.save()
        messages.info(request, "Loan balance is zero. Loan marked as closed.")
        return redirect("loan_detail", pk=loan_id)

    # Monthly interest rate
    R = Decimal(str(loan.interest_rate)) / Decimal("12") / Decimal("100")
    interest = loan.remaining_balance * R
    principal = Decimal(str(loan.emi)) - interest

    # Handle last payment (principal can't exceed balance)
    if principal >= loan.remaining_balance:
        principal = loan.remaining_balance
        actual_emi = principal + interest
    else:
        actual_emi = loan.emi

    new_balance = loan.remaining_balance - principal
    if new_balance < 0:
        new_balance = Decimal("0.00")

    # Determine payment number and due date
    payment_number = loan.payments.filter(status="paid").count() + 1
    due_date = add_months(loan.start_date, payment_number - 1)

    # Create payment record
    Payment.objects.create(
        loan=loan,
        payment_number=payment_number,
        amount=actual_emi.quantize(Decimal("0.01")),
        principal_component=principal.quantize(Decimal("0.01")),
        interest_component=interest.quantize(Decimal("0.01")),
        balance_after=new_balance.quantize(Decimal("0.01")),
        due_date=due_date,
        payment_date=timezone.now().date(),
        status="paid",
    )

    # Update loan
    loan.remaining_balance = new_balance.quantize(Decimal("0.01"))
    loan.total_interest_paid += interest.quantize(Decimal("0.01"))

    if loan.remaining_balance <= Decimal("0.01"):
        loan.status = "closed"
        loan.remaining_balance = Decimal("0.00")
        messages.success(request, f"EMI #{payment_number} paid. Loan fully repaid!")
    else:
        messages.success(
            request, f"EMI #{payment_number} of ₹{actual_emi:,.2f} paid successfully."
        )

    loan.save()
    return redirect("loan_detail", pk=loan_id)


def make_prepayment(request, loan_id):
    """
    Process a prepayment (extra payment towards principal).
    Recalculates interest saved and months reduced.
    """
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)

    if loan.status == "closed":
        messages.warning(request, "Cannot make prepayment on a closed loan.")
        return redirect("loan_detail", pk=loan_id)

    if request.method == "POST":
        form = PrepaymentForm(request.POST, loan=loan)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            prepayment_date = form.cleaned_data["prepayment_date"]

            old_balance = loan.remaining_balance
            new_balance = old_balance - amount

            # Calculate months reduced
            old_months = calculate_remaining_months(
                old_balance, loan.interest_rate, loan.emi
            )
            new_months = calculate_remaining_months(
                max(new_balance, Decimal("0.01")), loan.interest_rate, loan.emi
            )
            months_reduced = max(0, old_months - new_months)

            # Estimate interest saved (average monthly interest × months saved)
            R = Decimal(str(loan.interest_rate)) / Decimal("12") / Decimal("100")
            avg_monthly_interest = (old_balance + new_balance) / 2 * R
            interest_saved = avg_monthly_interest * months_reduced

            Prepayment.objects.create(
                loan=loan,
                amount=amount,
                prepayment_date=prepayment_date,
                months_reduced=months_reduced,
                interest_saved=interest_saved.quantize(Decimal("0.01")),
            )

            # Update loan balance
            loan.remaining_balance = max(new_balance, Decimal("0.00"))
            if loan.remaining_balance <= Decimal("0.01"):
                loan.status = "closed"
                loan.remaining_balance = Decimal("0.00")
                messages.success(
                    request, f"Prepayment of ₹{amount:,.2f} applied. Loan fully repaid!"
                )
            else:
                messages.success(
                    request,
                    f"Prepayment of ₹{amount:,.2f} applied. "
                    f"~{months_reduced} months saved, ~₹{interest_saved:,.0f} interest saved.",
                )
            loan.save()

    return redirect("loan_detail", pk=loan_id)


class EMIScheduleView(LoginRequiredMixin, TemplateView):
    # Inside get_context_data, change the schedule line to:
    # (We handle pagination manually in template via JS for performance,
    # or slice it here. Slicing is easier for pure Django):

    def get_context_data(self, **kwargs):
        # ... existing code ...
        schedule = generate_full_schedule(loan)

        # Add Pagination manually for tables
        from django.core.paginator import Paginator

        paginator = Paginator(schedule, 20)  # 20 rows per page
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["schedule"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["total_schedule_items"] = paginator.count
        return context
