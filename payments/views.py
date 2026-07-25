"""
Views for recording EMI payments and prepayments.
These are POST-only actions that redirect back to the loan detail page.
"""

import math
from decimal import Decimal

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import TemplateView
from openpyxl.styles import Alignment, Font, PatternFill

from loans.models import Loan
from loans.utils import add_months, calculate_remaining_months, generate_full_schedule

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
    """Display the full amortization schedule for a loan."""

    template_name = "payments/emi_schedule.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan_id = kwargs.get("loan_id")
        loan = get_object_or_404(Loan, pk=loan_id, user=self.request.user)

        from loans.utils import generate_full_schedule

        schedule = generate_full_schedule(loan)

        # Add Pagination manually for tables
        from django.core.paginator import Paginator

        paginator = Paginator(schedule, 20)  # 20 rows per page
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["loan"] = loan
        context["schedule"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["total_schedule_items"] = paginator.count
        return context


@login_required
def export_schedule_excel(request, loan_id):
    """Export beautifully formatted amortization schedule to Excel."""
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    schedule = generate_full_schedule(loan)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Amortization Schedule"

    # Title Row
    ws.merge_cells("A1:G1")
    ws["A1"] = f"Loan Schedule - {loan.loan_name}"
    ws["A1"].font = Font(bold=True, size=14, color="28A745")
    ws["A1"].alignment = Alignment(horizontal="center")

    # Meta Info
    ws.append([])
    ws.append(["Loan Amount:", float(loan.amount)])
    ws.append(["Interest Rate:", f"{loan.interest_rate}%"])
    ws.append(["Tenure:", f"{loan.tenure_years} Years"])
    ws.append(["Monthly EMI:", float(loan.emi)])
    ws.append([])

    # Headers
    headers = [
        "Month",
        "Due Date",
        "EMI (₹)",
        "Principal (₹)",
        "Interest (₹)",
        "Balance (₹)",
        "Status",
    ]
    ws.append(headers)

    # Style Headers (Green Background)
    for cell in ws[6]:
        cell.font = Font(bold=True, color="FFFFFF", size=11)
        cell.fill = PatternFill(
            start_color="28A745", end_color="28A745", fill_type="solid"
        )
        cell.alignment = Alignment(horizontal="center")

    # Data Rows
    for row in schedule:
        ws.append(
            [
                row["month"],
                row["due_date"].strftime("%Y-%m-%d"),
                float(row["emi"]),
                float(row["principal"]),
                float(row["interest"]),
                float(row["balance"]),
                row["status"].title(),
            ]
        )

    # Column Widths
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 15
    for col in ["C", "D", "E", "F"]:
        ws.column_dimensions[col].width = 20
    ws.column_dimensions["G"].width = 12

    # Response
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    safe_name = loan.loan_name.replace(" ", "_")
    response["Content-Disposition"] = (
        f'attachment; filename="{safe_name}_Schedule.xlsx"'
    )
    wb.save(response)
    return response


class TransactionLedgerView(LoginRequiredMixin, TemplateView):
    """Combined view of all EMIs and Prepayments sorted by date."""

    template_name = "payments/transaction_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan_id = kwargs.get("loan_id")
        loan = get_object_or_404(Loan, pk=loan_id, user=self.request.user)

        transactions = []

        # Add EMIs
        for p in loan.payments.filter(status="paid").order_by("-payment_date"):
            transactions.append(
                {
                    "date": p.payment_date,
                    "amount": p.amount,
                    "type": "emi",
                    "detail": f"EMI #{p.payment_number}",
                }
            )

        # Add Prepayments
        for p in loan.prepayments.all().order_by("-prepayment_date"):
            transactions.append(
                {
                    "date": p.prepayment_date,
                    "amount": p.amount,
                    "type": "prepayment",
                    "detail": "Prepayment",
                }
            )

        # Sort combined list by date descending
        transactions.sort(key=lambda x: x["date"], reverse=True)

        context["loan"] = loan
        context["transactions"] = transactions
        return context
