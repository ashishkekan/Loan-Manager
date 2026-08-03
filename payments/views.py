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
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import TemplateView
from openpyxl.styles import Alignment, Font, PatternFill

from dashboard.utils import add_activity
from loans.models import Loan
from loans.utils import (
    add_months,
    add_periods,
    calculate_remaining_periods,
    generate_full_schedule,
    get_period_details,
)

from .forms import PrepaymentForm
from .models import Payment, Prepayment
from .services import process_emi_payment


@login_required
def pay_emi(request, loan_id):
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    payment = process_emi_payment(loan, payment_mode="manual", payment_type="emi")
    if payment is None:
        if loan.status == "closed":
            messages.warning(request, "Loan is already closed.")
        else:
            messages.warning(request, "Payment could not be processed.")
    else:
        add_activity(
            loan.user,
            "emi_paid",
            f"EMI #{payment.payment_number} Paid",
            loan,
            f"₹{payment.amount:,.0f}",
        )
        messages.success(
            request,
            f"EMI #{payment.payment_number} of ₹{payment.amount:,.2f} paid successfully.",
        )
    return redirect("loan_detail", pk=loan.id)


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
            frequency = getattr(loan, "emi_frequency", "monthly")
            # Calculate months reduced
            old_periods = calculate_remaining_periods(
                old_balance, loan.interest_rate, loan.emi, frequency
            )
            new_periods = calculate_remaining_periods(
                max(new_balance, Decimal("0.01")),
                loan.interest_rate,
                loan.emi,
                frequency,
            )
            periods_reduced = max(0, old_periods - new_periods)
            # Estimate interest saved (average monthly interest × months saved)
            _, periods_per_year = get_period_details(frequency)
            R = (
                Decimal(str(loan.interest_rate))
                / Decimal(str(periods_per_year))
                / Decimal("100")
            )
            avg_period_interest = (old_balance + new_balance) / 2 * R
            months_per_period, _ = get_period_details(frequency)

            months_reduced = periods_reduced * months_per_period
            interest_saved = avg_period_interest * months_reduced
            prepayment = Prepayment.objects.create(
                loan=loan,
                amount=amount,
                prepayment_date=prepayment_date,
                months_reduced=months_reduced,
                interest_saved=interest_saved.quantize(Decimal("0.01")),
                payment_mode="manual",
                payment_type="prepayment",
                status="paid",
            )

            # Update loan balance
            loan.remaining_balance = max(new_balance, Decimal("0.00"))
            loan.save(update_fields=["remaining_balance", "status"])
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
            add_activity(
                loan.user,
                "prepayment",
                "Prepayment Done",
                loan,
                f"₹{prepayment.amount:,.0f} prepaid.",
            )
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
    return redirect("loan_detail", pk=loan_id)


class EMIScheduleView(LoginRequiredMixin, TemplateView):
    """Display the full amortization schedule for a loan."""

    template_name = "payments/emi_schedule.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan_id = kwargs.get("loan_id")
        loan = get_object_or_404(Loan, pk=loan_id, user=self.request.user)

        schedule = generate_full_schedule(loan)

        # Add Pagination manually for tables

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
    frequency_label = loan.get_emi_frequency_display()
    ws.append([f"{frequency_label} EMI:", float(loan.emi)])
    ws.append([])

    # Headers
    headers = [
        "Period",
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
                row["period"],
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
                    "type": p.payment_type,
                    "detail": f"EMI #{p.payment_number}",
                }
            )

        # Add Prepayments
        for p in loan.prepayments.all().order_by("-prepayment_date"):
            transactions.append(
                {
                    "date": p.prepayment_date,
                    "amount": p.amount,
                    "type": p.payment_type,
                    "detail": "Prepayment",
                }
            )

        # Sort combined list by date descending
        transactions.sort(key=lambda x: x["date"], reverse=True)

        context["loan"] = loan
        context["transactions"] = transactions
        return context
