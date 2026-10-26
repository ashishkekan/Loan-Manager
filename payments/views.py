import math
import hashlib
from django.core import signing
from django.views.decorators.http import require_POST
from datetime import date
from decimal import Decimal

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Q, Sum
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import TemplateView
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from dashboard.utils import add_activity
from loans.models import Loan
from loans.utils import (
    add_months,
    add_periods,
    calculate_remaining_periods,
    create_notification,
    generate_full_schedule,
    get_period_details,
    get_schedule_summary,
)
from payments.forms import PrepaymentForm
from payments.models import Payment, Prepayment
from payments.services import process_emi_payment, process_prepayment


def _confirmation(request, loan, kind):
    token = request.POST.get("confirmation", "")
    try:
        data = signing.loads(token, salt="repayment", max_age=86400)
    except signing.BadSignature:
        raise ValueError(
            "Payment confirmation expired. Refresh the loan and try again."
        )
    if (
        data.get("loan") != loan.pk
        or data.get("user") != request.user.pk
        or data.get("kind") != kind
    ):
        raise ValueError("Invalid payment confirmation.")
    return data, hashlib.sha256(token.encode()).hexdigest()


@login_required
@require_POST
def pay_emi(request, loan_id):
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    try:
        data, key = _confirmation(request, loan, "emi")
        payment = process_emi_payment(
            loan,
            request_key=key,
            expected_period=data["period"],
            expected_amount=data["amount"],
        )
        messages.success(
            request,
            f"Installment #{payment.payment_number}: ₹{payment.amount:,.2f} recorded. No bank transfer was initiated.",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("loan_detail", pk=loan.id)


@login_required
@require_POST
def make_prepayment(request, loan_id):
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    try:
        _, key = _confirmation(request, loan, "prepayment")
        existing = Prepayment.objects.filter(loan=loan, request_key=key).first()
        if existing:
            messages.info(request, "This prepayment has already been recorded.")
        else:
            form = PrepaymentForm(request.POST, loan=loan)
            if not form.is_valid():
                raise ValueError(
                    " ".join(str(e) for errors in form.errors.values() for e in errors)
                )
            payment = process_prepayment(
                loan,
                form.cleaned_data["amount"],
                form.cleaned_data["prepayment_date"],
                request_key=key,
            )
            messages.success(request, f"Prepayment of ₹{payment.amount:,.2f} recorded.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("loan_detail", pk=loan.pk)


class EMIScheduleView(LoginRequiredMixin, TemplateView):
    template_name = "payments/emi_schedule.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan_id = kwargs.get("loan_id")
        if self.request.user.is_staff:
            loan = get_object_or_404(Loan, pk=loan_id)
        else:
            loan = get_object_or_404(Loan, pk=loan_id, user=self.request.user)
        schedule = generate_full_schedule(loan)
        paginator = Paginator(schedule, 20)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)
        context["loan"] = loan
        context["schedule"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["total_schedule_items"] = paginator.count
        return context


@login_required
def export_schedule_excel(request, loan_id):
    if request.user.is_staff:
        loan = get_object_or_404(Loan, pk=loan_id)
    else:
        loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    schedule = generate_full_schedule(loan)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Amortization Schedule"
    ws.merge_cells("A1:G1")
    ws["A1"] = f"Loan Schedule - {loan.loan_name}"
    ws["A1"].font = Font(bold=True, size=14, color="28A745")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append([])
    ws.append(["Loan Amount:", float(loan.amount)])
    ws.append(["Interest Rate:", f"{loan.interest_rate}%"])
    ws.append(["Tenure:", f"{loan.tenure_years} Years"])
    frequency_label = loan.get_emi_frequency_display()
    ws.append([f"{frequency_label} EMI:", float(loan.emi)])
    ws.append([])
    headers = [
        "Period",
        "Due Date",
        "EMI (₹)",
        "Principal (₹)",
        "Interest (₹)",
        "Total Debit (₹)",
        "Balance (₹)",
        "Status",
    ]
    ws.append(headers)
    for cell in ws[6]:
        cell.font = Font(bold=True, color="FFFFFF", size=11)
        cell.fill = PatternFill(
            start_color="28A745",
            end_color="28A745",
            fill_type="solid",
        )
        cell.alignment = Alignment(horizontal="center")
    for row in schedule:
        ws.append(
            [
                row["period"],
                row["due_date"].strftime("%Y-%m-%d"),
                float(row["regular_emi"]),
                float(row["principal"]),
                float(row["interest"]),
                float(row["total_debit"]),
                float(row["balance"]),
                row["status"].title(),
            ]
        )
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 15
    for col in ["C", "D", "E", "F", "G", "H"]:
        ws.column_dimensions[col].width = 20
    ws.column_dimensions["I"].width = 12
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
    template_name = "payments/transaction_ledger.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan_id = kwargs.get("loan_id")
        if self.request.user.is_staff:
            loan = get_object_or_404(Loan, pk=loan_id)
        else:
            loan = get_object_or_404(Loan, pk=loan_id, user=self.request.user)
        transactions = []
        for p in loan.payments.filter(status="paid").order_by("-payment_date"):
            transactions.append(
                {
                    "date": p.payment_date,
                    "amount": p.amount,
                    "type": p.payment_type,
                    "detail": f"EMI #{p.payment_number}",
                }
            )
        for p in loan.prepayments.all().order_by("-prepayment_date"):
            transactions.append(
                {
                    "date": p.prepayment_date,
                    "amount": p.amount,
                    "type": p.payment_type,
                    "detail": "Prepayment",
                }
            )
        transactions.sort(key=lambda x: x["date"], reverse=True)
        context["loan"] = loan
        context["transactions"] = transactions
        return context


@login_required
def payment_dashboard(request):
    from loans.accounting import ZERO

    loans = Loan.objects.select_related("user").all()
    if not request.user.is_staff:
        loans = loans.filter(user=request.user)
    rows = [r for loan in loans for r in generate_full_schedule(loan)]
    summary = {"paid": ZERO, "pending": ZERO, "overdue": ZERO}
    for row in rows:
        summary[row["status"]] += row["total_debit"]
    selected = request.GET.get("status", "")
    if selected in summary:
        rows = [r for r in rows if r["status"] == selected]
    rows.sort(key=lambda r: (r["due_date"], r["period"]), reverse=True)
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "payments/payment_dashboard.html",
        {
            "page_obj": page,
            "rows": page.object_list,
            "summary": summary,
            "selected_status": selected,
            "page_title": "Payments",
        },
    )


@login_required
def export_payment_excel(request):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Payments"
    headers = [
        "Payment No",
        "Loan",
        "Due Date",
        "Payment Date",
        "Status",
        "Payment Mode",
        "Regular EMI",
        "Additional Interest",
        "Total Debit",
        "Principal",
        "Interest",
        "Balance After",
    ]
    for col, header in enumerate(headers, start=1):
        sheet.cell(row=1, column=col).value = header
    payments = (
        Payment.objects.filter(
            **({} if request.user.is_staff else {"loan__user": request.user})
        )
        .select_related("loan")
        .order_by("payment_number")
    )
    row = 2
    for payment in payments:
        sheet.cell(row=row, column=1).value = payment.payment_number
        sheet.cell(row=row, column=2).value = payment.loan.loan_name
        sheet.cell(row=row, column=3).value = (
            payment.due_date.strftime("%d-%m-%Y") if payment.due_date else ""
        )
        sheet.cell(row=row, column=4).value = (
            payment.payment_date.strftime("%d-%m-%Y") if payment.payment_date else ""
        )
        sheet.cell(row=row, column=5).value = payment.get_status_display()
        sheet.cell(row=row, column=6).value = payment.get_payment_mode_display()
        sheet.cell(row=row, column=7).value = float(payment.regular_emi_amount)
        sheet.cell(row=row, column=9).value = float(payment.amount)
        sheet.cell(row=row, column=10).value = float(payment.principal_component)
        sheet.cell(row=row, column=11).value = float(payment.interest_component)
        sheet.cell(row=row, column=12).value = float(payment.balance_after)
        row += 1
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="payment_history.xlsx"'
    workbook.save(response)
    return response


@login_required
def download_statement(request):
    payments = (
        Payment.objects.filter(
            **({} if request.user.is_staff else {"loan__user": request.user})
        )
        .select_related("loan")
        .order_by("due_date")
    )

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="Payment_Statement.pdf"'

    doc = SimpleDocTemplate(response)
    styles = getSampleStyleSheet()

    elements = []
    elements.append(Paragraph("<b>NexusLoan Payment Statement</b>", styles["Title"]))
    elements.append(
        Paragraph(
            f"Customer : {request.user.get_full_name() or request.user.username}",
            styles["Normal"],
        )
    )

    elements.append(Spacer(1, 0.30 * inch))
    data = [["Loan", "EMI", "Due Date", "Payment Date", "Status", "Amount"]]
    total = 0
    for payment in payments:
        total += payment.total_debit_amount
        data.append(
            [
                payment.loan.loan_name,
                payment.payment_number,
                payment.due_date.strftime("%d-%m-%Y"),
                (
                    payment.payment_date.strftime("%d-%m-%Y")
                    if payment.payment_date
                    else "-"
                ),
                payment.get_status_display(),
                f"₹ {payment.total_debit_amount}",
            ]
        )
    data.append(["", "", "", "", "Total", f"₹ {total}"])
    table = Table(data)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 1), (-1, -2), colors.whitesmoke),
                ("BACKGROUND", (0, -1), (-1, -1), colors.lightgrey),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    return response


@login_required
def prepayment_dashboard(request):
    today = timezone.localdate()
    if request.user.is_staff:
        prepayments = Prepayment.objects.select_related("loan", "loan__user").order_by(
            "-prepayment_date", "-created_at"
        )
        paid_prepayments = prepayments.filter(status="paid")
        pending_prepayments = prepayments.filter(status="pending")
        summary = paid_prepayments.aggregate(
            total_prepaid_amount=Sum("amount"),
            total_interest_saved=Sum("interest_saved"),
            total_tenure_reduced=Sum("months_reduced"),
            loans_benefited=Count("loan", distinct=True),
        )
        total_prepaid_amount = summary["total_prepaid_amount"] or Decimal("0.00")
        total_interest_saved = summary["total_interest_saved"] or Decimal("0.00")
        total_tenure_reduced = summary["total_tenure_reduced"] or 0
        loans_benefited = summary["loans_benefited"] or 0
        today_prepaid_amount = paid_prepayments.filter(prepayment_date=today).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.00")
        month_start = today.replace(day=1)
        month_prepaid_amount = paid_prepayments.filter(
            prepayment_date__gte=month_start,
            prepayment_date__lte=today,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        pending_amount = pending_prepayments.aggregate(total=Sum("amount"))[
            "total"
        ] or Decimal("0.00")
        foreclosure_count = paid_prepayments.filter(payment_type="foreclosure").count()
        foreclosure_amount = paid_prepayments.filter(
            payment_type="foreclosure"
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        loan_prepaid_data = []
        loan_ids = paid_prepayments.values_list("loan_id", flat=True).distinct()
        loans = (
            Loan.objects.filter(id__in=loan_ids)
            .select_related("user")
            .order_by("loan_name")
        )
        for loan in loans:
            loan_prepayments = paid_prepayments.filter(loan=loan)
            loan_summary = loan_prepayments.aggregate(
                total_prepaid_amount=Sum("amount"),
                total_interest_saved=Sum("interest_saved"),
                total_months_reduced=Sum("months_reduced"),
                last_prepayment_date=Max("prepayment_date"),
                prepayment_count=Count("id"),
            )
            if not loan_summary["prepayment_count"]:
                continue
            loan_prepaid_data.append(
                {
                    "loan": loan,
                    "user": loan.user,
                    "total_prepaid_amount": (
                        loan_summary["total_prepaid_amount"] or Decimal("0.00")
                    ),
                    "total_interest_saved": (
                        loan_summary["total_interest_saved"] or Decimal("0.00")
                    ),
                    "months_reduced": (loan_summary["total_months_reduced"] or 0),
                    "last_prepayment_date": (loan_summary["last_prepayment_date"]),
                    "prepayment_count": (loan_summary["prepayment_count"] or 0),
                }
            )
        context = {
            "page_title": "Prepayments",
            "admin_view": True,
            "loans": loans,
            "prepayments": paid_prepayments,
            "admin_total_prepaid_amount": total_prepaid_amount,
            "admin_total_interest_saved": total_interest_saved,
            "admin_loans_benefited": loans_benefited,
            "admin_total_tenure_reduced": total_tenure_reduced,
            "admin_today_prepaid_amount": today_prepaid_amount,
            "admin_month_prepaid_amount": month_prepaid_amount,
            "admin_pending_count": pending_prepayments.count(),
            "admin_pending_amount": pending_amount,
            "admin_foreclosure_count": foreclosure_count,
            "admin_foreclosure_amount": foreclosure_amount,
            "admin_prepayment_count": paid_prepayments.count(),
            "loan_prepayment_summary": loan_prepaid_data,
        }
        return render(request, "payments/prepayment_dashboard.html", context)

    loans = Loan.objects.filter(user=request.user).order_by("loan_name")
    prepayment_qs = (
        Prepayment.objects.filter(loan__user=request.user, status="paid")
        .select_related("loan")
        .order_by("-prepayment_date", "-created_at")
    )
    summary = prepayment_qs.aggregate(
        total_prepaid_amount=Sum("amount"),
        total_interest_saved=Sum("interest_saved"),
        total_tenure_reduced=Sum("months_reduced"),
        loans_benefited=Count("loan", distinct=True),
    )
    total_prepaid_amount = summary["total_prepaid_amount"] or Decimal("0.00")
    total_interest_saved = summary["total_interest_saved"] or Decimal("0.00")
    total_tenure_reduced = summary["total_tenure_reduced"] or 0
    loans_benefited = summary["loans_benefited"] or 0
    loan_prepaid_data = []
    for loan in loans:
        loan_prepayments = prepayment_qs.filter(loan=loan)
        loan_summary = loan_prepayments.aggregate(
            total_prepaid_amount=Sum("amount"),
            total_interest_saved=Sum("interest_saved"),
            total_months_reduced=Sum("months_reduced"),
            last_prepayment_date=Max("prepayment_date"),
            prepayment_count=Count("id"),
        )
        if not loan_summary["prepayment_count"]:
            continue
        loan_prepaid_data.append(
            {
                "loan": loan,
                "total_prepaid_amount": (
                    loan_summary["total_prepaid_amount"] or Decimal("0.00")
                ),
                "total_interest_saved": (
                    loan_summary["total_interest_saved"] or Decimal("0.00")
                ),
                "months_reduced": (loan_summary["total_months_reduced"] or 0),
                "last_prepayment_date": (loan_summary["last_prepayment_date"]),
                "prepayment_count": (loan_summary["prepayment_count"] or 0),
            }
        )

    paginator = Paginator(prepayment_qs, 15)
    prepayments = paginator.get_page(request.GET.get("page"))
    context = {
        "page_title": "Prepayments",
        "loans": loans,
        "prepayments": prepayments,
        "total_prepaid_amount": total_prepaid_amount,
        "total_interest_saved": total_interest_saved,
        "loans_benefited": loans_benefited,
        "total_tenure_reduced": total_tenure_reduced,
        "loan_prepayment_summary": loan_prepaid_data,
        "has_prepayments": prepayment_qs.exists(),
    }
    return render(request, "payments/prepayment_dashboard.html", context)


@login_required
def export_prepayment_excel(request):
    prepayments = (
        Prepayment.objects.filter(
            **({} if request.user.is_staff else {"loan__user": request.user})
        )
        .select_related("loan")
        .order_by("-prepayment_date")
    )
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Prepayments"
    headers = [
        "Loan Name",
        "Date",
        "Amount",
        "Payment Type",
        "Payment Mode",
        "Interest Saved",
        "Months Reduced",
        "Status",
    ]
    for col, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=col)
        cell.value = header
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(
            start_color="1E40AF", end_color="1E40AF", fill_type="solid"
        )
        cell.alignment = Alignment(horizontal="center")
    row = 2
    total_amount = Decimal("0.00")
    total_interest_saved = Decimal("0.00")
    total_months_reduced = 0
    for prepayment in prepayments:
        sheet.cell(row=row, column=1).value = prepayment.loan.loan_name
        sheet.cell(row=row, column=2).value = prepayment.prepayment_date.strftime(
            "%d-%m-%Y"
        )
        sheet.cell(row=row, column=3).value = float(prepayment.amount)
        sheet.cell(row=row, column=4).value = prepayment.get_payment_type_display()
        sheet.cell(row=row, column=5).value = prepayment.get_payment_mode_display()
        sheet.cell(row=row, column=6).value = float(prepayment.interest_saved)
        sheet.cell(row=row, column=7).value = prepayment.months_reduced
        sheet.cell(row=row, column=8).value = prepayment.get_status_display()
        total_amount += prepayment.amount
        total_interest_saved += prepayment.interest_saved
        total_months_reduced += prepayment.months_reduced
        row += 1

    row += 1
    sheet.cell(row=row, column=1).value = "SUMMARY"
    sheet.cell(row=row, column=1).font = Font(bold=True)
    row += 1
    sheet.cell(row=row, column=1).value = "Total Prepaid Amount"
    sheet.cell(row=row, column=2).value = float(total_amount)
    row += 1
    sheet.cell(row=row, column=1).value = "Total Interest Saved"
    sheet.cell(row=row, column=2).value = float(total_interest_saved)
    row += 1
    sheet.cell(row=row, column=1).value = "Total Tenure Reduced"
    sheet.cell(row=row, column=2).value = total_months_reduced
    widths = {
        "A": 30,
        "B": 15,
        "C": 18,
        "D": 18,
        "E": 18,
        "F": 20,
        "G": 18,
        "H": 15,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    response = HttpResponse(
        content_type=(
            "application/vnd.openxmlformats-officedocument." "spreadsheetml.sheet"
        )
    )
    response["Content-Disposition"] = 'attachment; filename="prepayment_report.xlsx"'
    workbook.save(response)
    return response


@login_required
def export_prepayment_pdf(request):
    prepayments = (
        Prepayment.objects.filter(
            **({} if request.user.is_staff else {"loan__user": request.user})
        )
        .select_related("loan")
        .order_by("-prepayment_date")
    )
    total_amount = Decimal("0.00")
    total_interest_saved = Decimal("0.00")
    total_months_reduced = 0
    for prepayment in prepayments:
        total_amount += prepayment.amount
        total_interest_saved += prepayment.interest_saved
        total_months_reduced += prepayment.months_reduced
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="Prepayment_Report.pdf"'
    doc = SimpleDocTemplate(
        response, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph("<b>NexusLoan Prepayment Report</b>", styles["Title"]))
    elements.append(
        Paragraph(
            f"Customer: " f"{request.user.get_full_name() or request.user.username}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 0.25 * inch))
    summary_data = [
        ["Metric", "Value"],
        ["Total Prepaid Amount", f"₹ {total_amount:,.2f}"],
        ["Total Interest Saved", f"₹ {total_interest_saved:,.2f}"],
        ["Total Tenure Reduced", f"{total_months_reduced} months"],
    ]
    summary_table = Table(summary_data, colWidths=[3.2 * inch, 2.5 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ]
        )
    )
    elements.append(summary_table)
    elements.append(Spacer(1, 0.35 * inch))
    elements.append(Paragraph("<b>Prepayment History</b>", styles["Heading2"]))
    data = [["Loan", "Date", "Amount", "Type", "Mode", "Interest Saved", "Months"]]
    for prepayment in prepayments:
        data.append(
            [
                prepayment.loan.loan_name,
                prepayment.prepayment_date.strftime("%d-%m-%Y"),
                f"₹ {prepayment.amount:,.2f}",
                prepayment.get_payment_type_display(),
                prepayment.get_payment_mode_display(),
                f"₹ {prepayment.interest_saved:,.2f}",
                str(prepayment.months_reduced),
            ]
        )
    if len(data) == 1:
        data.append(["No prepayments found", "-", "-", "-", "-", "-", "-"])
    table = Table(
        data,
        repeatRows=1,
        colWidths=[
            1.35 * inch,
            0.85 * inch,
            0.9 * inch,
            0.75 * inch,
            0.75 * inch,
            1.0 * inch,
            0.55 * inch,
        ],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    return response
