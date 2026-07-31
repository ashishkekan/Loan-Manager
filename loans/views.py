import csv
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import default_storage
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
)

from loans.forms import LoanForm, LoanNoteForm
from loans.models import Loan, LoanDocument, LoanNote
from loans.utils import (
    add_periods,
    calculate_emi,
    calculate_foreclosure,
    compare_loans,
    generate_full_schedule,
    generate_projected_schedule,
)


class LoanListView(LoginRequiredMixin, ListView):
    model = Loan
    template_name = "loans/loan_list.html"
    context_object_name = "loans"

    def get_queryset(self):
        return (
            Loan.objects.filter(user=self.request.user)
            .select_related("user")
            .order_by("-created_at")
        )


class LoanCreateView(LoginRequiredMixin, CreateView):
    model = Loan
    form_class = LoanForm
    template_name = "loans/create_loan.html"

    def form_valid(self, form):
        form.instance.user = self.request.user
        amount = form.cleaned_data["amount"]
        rate = form.cleaned_data["interest_rate"]
        tenure = form.cleaned_data["tenure_years"]
        form.instance.emi = calculate_emi(
            amount, rate, tenure, form.cleaned_data["emi_frequency"]
        )
        form.instance.remaining_balance = amount
        if not form.instance.first_emi_date:
            form.instance.first_emi_date = form.instance.start_date
        messages.success(self.request, f'"{form.instance.loan_name}" created!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("loan_detail", kwargs={"pk": self.object.pk})


class LoanDetailView(LoginRequiredMixin, DetailView):
    model = Loan
    template_name = "loans/loan_detail.html"
    context_object_name = "loan"

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user).prefetch_related(
            "payments", "prepayments", "notes"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan = self.object

        context["paid_payments"] = loan.payments.filter(status="paid").order_by(
            "-payment_number"
        )[:20]

        context["prepayments"] = loan.prepayments.all().order_by("-prepayment_date")
        context["notes"] = loan.notes.all()[:10]
        context["note_form"] = LoanNoteForm()

        total_principal_paid = loan.amount - loan.remaining_balance
        context["total_principal_paid"] = total_principal_paid
        context["total_paid"] = total_principal_paid + loan.total_interest_paid
        context["progress"] = loan.progress_percent
        context["health_score"] = loan.health_score
        context["health_label"] = loan.health_label
        context["is_overdue"] = loan.is_overdue
        context["overdue_days"] = loan.overdue_days
        last_payment = (
            loan.payments.filter(status="paid").order_by("-payment_date").first()
        )
        last_prepayment = loan.prepayments.order_by("-prepayment_date").first()
        last_transaction = None
        last_transaction_type = None
        if last_payment and last_prepayment:
            if last_payment.payment_date >= last_prepayment.prepayment_date:
                last_transaction = last_payment
                last_transaction_type = "payment"
            else:
                last_transaction = last_prepayment
                last_transaction_type = "prepayment"
        elif last_payment:
            last_transaction = last_payment
            last_transaction_type = "payment"
        elif last_prepayment:
            last_transaction = last_prepayment
            last_transaction_type = "prepayment"
        context["last_payment"] = last_transaction
        context["last_payment_type"] = last_transaction_type
        if loan.status == "active":
            next_num = loan.payments.filter(status="paid").count() + 1
            context["next_emi_num"] = next_num
            context["next_emi_date"] = add_periods(
                loan.schedule_start_date,
                next_num - 1,
                loan.emi_frequency,
            )
        projected = generate_projected_schedule(loan)
        context["projected_schedule_json"] = {
            "labels": [f"M{r['period']}" for r in projected[:60]],
            "balances": [float(r["balance"]) for r in projected[:60]],
        }
        context["pie_data_json"] = {
            "principal": round(float(total_principal_paid), 2),
            "interest": round(float(loan.total_interest_paid), 2),
        }
        if loan.status == "active":
            context["foreclosure"] = calculate_foreclosure(loan)
        total_prepayment_amount = loan.prepayments.aggregate(total=Sum("amount"))[
            "total"
        ] or Decimal("0")
        total_paid = (
            total_principal_paid + loan.total_interest_paid + total_prepayment_amount
        )
        original_closure_date = add_periods(
            loan.schedule_start_date, loan.tenure_years * 12, loan.emi_frequency
        )
        next_emi_date = add_periods(
            loan.schedule_start_date, next_num - 1, loan.emi_frequency
        )
        estimated_closure_date = add_periods(
            next_emi_date, loan.months_remaining - 1, loan.emi_frequency
        )
        delta = relativedelta(original_closure_date, estimated_closure_date)
        years_saved = delta.years
        remaining_months_saved = delta.months
        months_saved = years_saved * 12 + remaining_months_saved
        years_saved = months_saved // 12
        remaining_months_saved = months_saved % 12
        context["total_prepayment_amount"] = total_prepayment_amount
        context["total_paid"] = total_paid
        context["estimated_closure_date"] = estimated_closure_date
        context.update(
            {
                "estimated_closure_date": estimated_closure_date,
                "original_closure_date": original_closure_date,
                "months_saved": months_saved,
                "years_saved": years_saved,
                "remaining_months_saved": remaining_months_saved,
            }
        )
        lifetime_interest_saved = loan.prepayments.aggregate(
            total=Sum("interest_saved")
        )["total"] or Decimal("0")
        lifetime_months_saved = (
            loan.prepayments.aggregate(total=Sum("months_reduced"))["total"] or 0
        )
        roi_percentage = Decimal("0")
        if total_prepayment_amount > 0:
            roi_percentage = (
                lifetime_interest_saved / total_prepayment_amount
            ) * Decimal("100")
        context.update(
            {
                "lifetime_interest_saved": lifetime_interest_saved,
                "lifetime_months_saved": lifetime_months_saved,
                "roi_percentage": round(roi_percentage, 1),
            }
        )
        if loan.status == "active" and loan.remaining_balance > 0:
            period_rate = (
                Decimal(str(loan.interest_rate)) / Decimal("12") / Decimal("100")
            )
            next_interest = loan.remaining_balance * period_rate
            next_principal = Decimal(str(loan.emi)) - next_interest
            next_interest = next_interest.quantize(Decimal("0.01"))
            next_principal = next_principal.quantize(Decimal("0.01"))
            if next_principal > loan.remaining_balance:
                next_principal = loan.remaining_balance
            balance_after_next = loan.remaining_balance - next_principal
            balance_after_next = balance_after_next.quantize(Decimal("0.01"))
            context.update(
                {
                    "next_interest": next_interest,
                    "next_principal": next_principal,
                    "balance_after_next": balance_after_next,
                }
            )
        health_factors = []
        if loan.is_overdue:
            payment_score = max(0, 100 - (loan.overdue_days * 2))
            payment_status = "Overdue"
        else:
            payment_score = 100
            payment_status = "Excellent"
        health_factors.append(
            {
                "title": "Payment Discipline",
                "score": payment_score,
                "status": payment_status,
                "icon": "fa-calendar-check",
            }
        )
        interest_ratio = (
            (loan.total_interest_projected / loan.amount) * 100
            if loan.amount
            else Decimal("0")
        )
        if interest_ratio < 40:
            interest_status = "Low"
            interest_score = 100
        elif interest_ratio < 70:
            interest_status = "Medium"
            interest_score = 80
        else:
            interest_status = "High"
            interest_score = 60
        health_factors.append(
            {
                "title": "Interest Burden",
                "score": interest_score,
                "status": interest_status,
                "icon": "fa-percent",
            }
        )
        if total_prepayment_amount == 0:
            prepayment_score = 70
            prepayment_status = "None"
        elif total_prepayment_amount < loan.amount * Decimal("0.10"):
            prepayment_score = 90
            prepayment_status = "Good"
        else:
            prepayment_score = 100
            prepayment_status = "Excellent"
        health_factors.append(
            {
                "title": "Prepayment Habit",
                "score": prepayment_score,
                "status": prepayment_status,
                "icon": "fa-bolt",
            }
        )
        progress_score = min(100, int(loan.progress_percent))
        health_factors.append(
            {
                "title": "Repayment Progress",
                "score": progress_score,
                "status": f"{progress_score}%",
                "icon": "fa-chart-line",
            }
        )
        context["health_factors"] = health_factors
        financial_tips = []
        if loan.interest_rate >= 10:
            financial_tips.append(
                {
                    "icon": "fa-percent",
                    "title": "Consider Refinancing",
                    "message": "Your interest rate is relatively high. Refinancing could reduce your monthly EMI and total interest.",
                    "type": "warning",
                }
            )
        if total_prepayment_amount == 0:
            financial_tips.append(
                {
                    "icon": "fa-bolt",
                    "title": "Start Making Prepayments",
                    "message": "Even one extra EMI every year can significantly reduce your loan tenure and interest cost.",
                    "type": "success",
                }
            )
        if loan.progress_percent >= 80:
            financial_tips.append(
                {
                    "icon": "fa-flag-checkered",
                    "title": "Almost There!",
                    "message": "You're close to becoming debt-free. Continue paying EMIs on time to avoid unnecessary penalties.",
                    "type": "primary",
                }
            )
        if loan.is_overdue:
            financial_tips.append(
                {
                    "icon": "fa-triangle-exclamation",
                    "title": "Pay Overdue EMI",
                    "message": f"Your EMI is overdue by {loan.overdue_days} day(s). Paying it now will prevent additional charges.",
                    "type": "danger",
                }
            )
        if (
            not loan.is_overdue
            and total_prepayment_amount > 0
            and loan.progress_percent >= 20
        ):
            financial_tips.append(
                {
                    "icon": "fa-heart",
                    "title": "Excellent Financial Discipline",
                    "message": "You're managing this loan efficiently. Keep making occasional prepayments whenever possible.",
                    "type": "success",
                }
            )
        if not financial_tips:
            financial_tips.append(
                {
                    "icon": "fa-lightbulb",
                    "title": "Stay Consistent",
                    "message": "Pay every EMI on time. Consistency is the easiest way to save money and improve your financial health.",
                    "type": "primary",
                }
            )
        context["financial_tips"] = financial_tips
        achievement_badges = []
        paid_emis = loan.payments.filter(status="paid").count()
        if paid_emis >= 1:
            achievement_badges.append(
                {
                    "title": "First EMI",
                    "description": "Successfully paid your first EMI.",
                    "icon": "fa-seedling",
                    "color": "success",
                    "earned": True,
                }
            )
        if paid_emis >= 6:
            achievement_badges.append(
                {
                    "title": "Consistent Payer",
                    "description": "Completed 6 EMIs without stopping.",
                    "icon": "fa-calendar-check",
                    "color": "primary",
                    "earned": True,
                }
            )
        if paid_emis >= 12:
            achievement_badges.append(
                {
                    "title": "One Year Strong",
                    "description": "Completed one full year of repayments.",
                    "icon": "fa-medal",
                    "color": "warning",
                    "earned": True,
                }
            )
        if loan.prepayments.exists():
            achievement_badges.append(
                {
                    "title": "Smart Saver",
                    "description": "Made your first prepayment.",
                    "icon": "fa-bolt",
                    "color": "success",
                    "earned": True,
                }
            )
        lifetime_interest_saved = loan.prepayments.aggregate(
            total=Sum("interest_saved")
        )["total"] or Decimal("0")
        if lifetime_interest_saved >= Decimal("100000"):
            achievement_badges.append(
                {
                    "title": "Interest Slayer",
                    "description": "Saved ₹1 Lakh+ in interest.",
                    "icon": "fa-fire",
                    "color": "danger",
                    "earned": True,
                }
            )
        if loan.progress_percent >= 50:
            achievement_badges.append(
                {
                    "title": "Halfway There",
                    "description": "Completed 50% of your loan.",
                    "icon": "fa-flag",
                    "color": "primary",
                    "earned": True,
                }
            )
        if loan.status == "closed":
            achievement_badges.append(
                {
                    "title": "Debt Free",
                    "description": "Congratulations! Loan fully repaid.",
                    "icon": "fa-trophy",
                    "color": "gold",
                    "earned": True,
                }
            )
        if not loan.is_overdue and paid_emis >= 12:
            achievement_badges.append(
                {
                    "title": "Perfect Payer",
                    "description": "Completed 12+ EMIs without any overdue payment.",
                    "icon": "fa-star",
                    "color": "warning",
                    "earned": True,
                }
            )
        if total_principal_paid >= loan.amount * Decimal("0.25"):
            achievement_badges.append(
                {
                    "title": "Principal Crusher",
                    "description": "Repaid 25% of your principal amount.",
                    "icon": "fa-hammer",
                    "color": "primary",
                    "earned": True,
                }
            )
        if months_saved >= 12:
            achievement_badges.append(
                {
                    "title": "Fast Tracker",
                    "description": "Reduced your loan tenure by one year or more.",
                    "icon": "fa-rocket",
                    "color": "success",
                    "earned": True,
                }
            )
        if lifetime_interest_saved >= Decimal("50000"):
            achievement_badges.append(
                {
                    "title": "Interest Saver",
                    "description": "Saved ₹50,000+ in interest payments.",
                    "icon": "fa-gem",
                    "color": "success",
                    "earned": True,
                }
            )
        if loan.progress_percent >= 25:
            achievement_badges.append(
                {
                    "title": "Quarter Paid",
                    "description": "Completed 25% of your loan journey.",
                    "icon": "fa-chart-pie",
                    "color": "primary",
                    "earned": True,
                }
            )
        if loan.progress_percent >= 50:
            achievement_badges.append(
                {
                    "title": "Halfway Hero",
                    "description": "You've crossed the halfway mark.",
                    "icon": "fa-mountain",
                    "color": "warning",
                    "earned": True,
                }
            )
        if loan.progress_percent >= 90:
            achievement_badges.append(
                {
                    "title": "Loan Master",
                    "description": "Less than 10% of your loan remains.",
                    "icon": "fa-crown",
                    "color": "gold",
                    "earned": True,
                }
            )
        if loan.status == "closed":
            achievement_badges.append(
                {
                    "title": "Debt Free",
                    "description": "Congratulations! You have completely repaid your loan.",
                    "icon": "fa-trophy",
                    "color": "gold",
                    "earned": True,
                }
            )
        context["achievement_badges"] = achievement_badges
        timeline = []
        for prepayment in loan.prepayments.order_by("prepayment_date"):
            timeline.append(
                {
                    "date": prepayment.prepayment_date,
                    "amount": prepayment.amount,
                    "interest_saved": prepayment.interest_saved,
                    "months_saved": prepayment.months_reduced,
                    "payment_mode": prepayment.get_payment_mode_display(),
                    "payment_type": prepayment.get_payment_type_display(),
                }
            )
        context["prepayment_timeline"] = timeline
        return context


class LoanDeleteView(LoginRequiredMixin, DeleteView):
    model = Loan
    success_url = reverse_lazy("loan_list")

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.success(request, "Loan deleted.")
        return super().delete(request, *args, **kwargs)


def add_note(request, loan_id):
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    if request.method == "POST":
        form = LoanNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.loan = loan
            note.save()
            messages.success(request, "Note added.")
    return redirect("loan_detail", pk=loan_id)


def delete_note(request, loan_id, note_id):
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    LoanNote.objects.filter(pk=note_id, loan=loan).delete()
    messages.success(request, "Note removed.")
    return redirect("loan_detail", pk=loan_id)


class LoanCompareView(LoginRequiredMixin, TemplateView):
    template_name = "loans/loan_compare.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loans = Loan.objects.filter(user=self.request.user).select_related("user")
        if loans.count() >= 2:
            context["comparison"] = compare_loans(loans)
        context["loans"] = loans
        return context


def export_loan_csv(request, loan_id):
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    schedule = generate_full_schedule(loan)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="{loan.loan_name}_schedule.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            f"Amortization Schedule — {loan.loan_name}",
            f"Amount: ₹{loan.amount}",
            f"Rate: {loan.interest_rate}%",
            f"Tenure: {loan.tenure_years} years",
            f"{loan.get_emi_frequency_display()} EMI: ₹{loan.emi}",
            "",
            "Period",
            "Due Date",
            "EMI",
            "Principal",
            "Interest",
            "Balance",
            "Status",
        ]
    )
    for row in schedule:
        writer.writerow(
            [
                "",
                row["period"],
                row["due_date"],
                row["emi"],
                row["principal"],
                row["interest"],
                row["balance"],
                row["status"],
            ]
        )
    return response


@login_required
def upload_document(request, loan_id):
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    if request.method == "POST":
        title = request.POST.get("title")
        doc_type = request.POST.get("doc_type", "other")
        file = request.FILES.get("file")
        if title and file:
            LoanDocument.objects.create(
                loan=loan, title=title, doc_type=doc_type, file=file
            )
            messages.success(request, "Document uploaded.")
    return redirect("loan_detail", pk=loan_id)


@login_required
def delete_document(request, loan_id, doc_id):
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    doc = get_object_or_404(LoanDocument, pk=doc_id, loan=loan)
    if doc.file:
        default_storage.delete(doc.file.path)
    doc.delete()
    messages.success(request, "Document deleted.")
    return redirect("loan_detail", pk=loan_id)
