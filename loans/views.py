import csv
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.sessions.models import Session
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from dashboard.utils import add_activity
from loans.forms import (
    AppearancePreferenceForm,
    BankAccountForm,
    LoanDisbursementForm,
    LoanDocumentForm,
    LoanForm,
    LoanNoteForm,
    NotificationPreferenceForm,
    PrivacySettingForm,
    SettingsPasswordForm,
    SettingsProfileForm,
    SupportTicketForm,
)
from loans.models import (
    AppearancePreference,
    BankAccount,
    Loan,
    LoanAccruedInterest,
    LoanDisbursement,
    LoanDocument,
    LoanNote,
    Notification,
    SupportTicket,
)
from loans.services import AccruedInterestService
from loans.utils import (
    add_periods,
    calculate_emi,
    calculate_foreclosure,
    compare_loans,
    create_notification,
    ensure_user_settings,
    generate_full_schedule,
    generate_projected_schedule,
    get_account_statistics,
    get_support_ticket_summary,
    simulate_extra_emi,
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
            amount,
            rate,
            tenure,
            form.cleaned_data["emi_frequency"],
        )

        form.instance.remaining_balance = amount

        if not form.instance.first_emi_date:
            form.instance.first_emi_date = form.instance.start_date

        response = super().form_valid(form)

        add_activity(
            self.request.user,
            "loan_created",
            f"{self.object.loan_name} created",
            self.object,
            f"Loan of ₹{self.object.amount:,.0f} added.",
        )

        create_notification(
            user=self.request.user,
            title="Loan Created",
            message=f"Your loan '{self.object.loan_name}' has been created successfully.",
            notification_type="loan",
            loan=self.object,
        )

        messages.success(self.request, f'"{self.object.loan_name}" created!')

        return response

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
            next_emi_date = add_periods(
                loan.schedule_start_date, next_num - 1, loan.emi_frequency
            )
        else:
            next_emi_date = None
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
            additional_interest = loan.total_pending_accrued_interest

            total_debit = (Decimal(str(loan.emi)) + additional_interest).quantize(
                Decimal("0.01")
            )

            context.update(
                {
                    "additional_interest": additional_interest,
                    "total_debit": total_debit,
                }
            )
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
            prepayment_status = "Basic"
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
        for prepayment in loan.prepayments.order_by("-prepayment_date")[:3]:
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
        extra = self.request.GET.get("extra_emi")
        if extra:
            try:
                context["simulation"] = simulate_extra_emi(
                    self.object,
                    Decimal(extra),
                )
                context["extra_emi"] = extra
            except Exception:
                pass
        context["goal_tracker"] = self.object.goal_tracker
        context["disbursements"] = loan.disbursements.order_by("disbursement_number")

        context["pending_accrued_interest"] = loan.accrued_interests.filter(
            status="pending"
        ).order_by("emi_date")

        context["recovered_accrued_interest"] = loan.accrued_interests.filter(
            status="recovered"
        ).order_by("-emi_date")[:20]

        context["total_disbursed_amount"] = loan.total_disbursed_amount

        context["remaining_sanction_amount"] = loan.remaining_sanction_amount

        context["pending_interest"] = loan.total_pending_accrued_interest

        context["recovered_interest"] = loan.total_recovered_accrued_interest
        summary = AccruedInterestService.calculate_total_debit(loan, next_emi_date)

        context["regular_emi"] = summary["regular_emi"]

        context["additional_interest"] = summary["additional_interest"]

        context["next_emi_total_debit"] = summary["total_debit"]

        context["regular_interest"] = summary["regular_interest"]
        affordability = {}
        monthly_income = self.request.GET.get("income")
        monthly_expenses = self.request.GET.get("expenses")

        if monthly_income and monthly_expenses:
            try:
                monthly_income = Decimal(monthly_income)
                monthly_expenses = Decimal(monthly_expenses)

                disposable_income = max(
                    monthly_income - monthly_expenses,
                    Decimal("0"),
                )

                effective_emi = (
                    Decimal(str(loan.emi)) + loan.total_pending_accrued_interest
                )

                if disposable_income > 0:
                    emi_ratio = (effective_emi / disposable_income) * Decimal("100")
                else:
                    emi_ratio = Decimal("100")

                emi_ratio = emi_ratio.quantize(Decimal("0.1"))

                if emi_ratio <= 35:
                    status = "Excellent"
                    color = "success"
                elif emi_ratio <= 50:
                    status = "Good"
                    color = "primary"
                elif emi_ratio <= 70:
                    status = "Risky"
                    color = "warning"
                else:
                    status = "Not Affordable"
                    color = "danger"
                balance_after_emi = max(
                    disposable_income - effective_emi,
                    Decimal("0"),
                )
                affordability = {
                    "income": monthly_income,
                    "expenses": monthly_expenses,
                    "disposable": disposable_income,
                    "emi_ratio": emi_ratio,
                    "balance_after_emi": balance_after_emi,
                    "status": status,
                    "color": color,
                }

            except Exception:
                pass

        context["affordability"] = affordability
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
                row["regular_emi"],
                row["principal"],
                row["interest"],
                row["balance"],
                row["status"],
            ]
        )
    return response


@login_required
def upload_document(request):
    if request.method != "POST":
        return redirect("documents_dashboard")
    loan_id = request.POST.get("loan")
    loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    form = LoanDocumentForm(request.POST, request.FILES)
    if form.is_valid():
        document = form.save(commit=False)
        document.loan = loan
        document.save()
        messages.success(request, f'"{document.title}" uploaded successfully.')
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
    return redirect("documents_dashboard")


@login_required
def delete_document(request, document_id):
    document = get_object_or_404(LoanDocument, pk=document_id, loan__user=request.user)
    if request.method == "POST":
        title = document.title
        document.delete()
        messages.success(request, f'"{title}" deleted successfully.')
    return redirect("documents_dashboard")


@login_required
def close_loan(request, pk):
    loan = get_object_or_404(Loan, pk=pk, user=request.user)
    if request.method == "POST":
        pending_interest = loan.total_pending_accrued_interest
        if pending_interest > 0:
            messages.error(
                request, "Loan cannot be closed while accrued interest is pending."
            )
            return redirect(
                "loan_detail",
                pk=loan.pk,
            )
        loan.status = "closed"
        loan.closed_date = parse_date(request.POST.get("closing_date"))
        loan.save()
        add_activity(
            loan.user,
            "loan_closed",
            f"{loan.loan_name} Closed",
            loan,
            "Congratulations! Loan completed.",
        )
        create_notification(
            user=loan.user,
            title="Loan Closed",
            message=f"{loan.loan_name} has been closed successfully.",
            notification_type="loan",
            loan=loan,
        )
        messages.success(request, "Loan closed successfully.")
    return redirect("loan_detail", pk=loan.pk)


class LoanUpdateView(LoginRequiredMixin, UpdateView):
    model = Loan
    form_class = LoanForm
    template_name = "loans/create_loan.html"

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user)

    def form_valid(self, form):
        amount = form.cleaned_data["amount"]
        rate = form.cleaned_data["interest_rate"]
        tenure = form.cleaned_data["tenure_years"]

        form.instance.emi = calculate_emi(
            amount, rate, tenure, form.cleaned_data["emi_frequency"]
        )

        if not form.instance.first_emi_date:
            form.instance.first_emi_date = form.instance.start_date

        messages.success(self.request, "Loan updated successfully.")
        LoanAccruedInterest.objects.filter(
            loan=form.instance, status="pending"
        ).delete()
        for disbursement in form.instance.disbursements.filter(status="released"):
            AccruedInterestService.generate_for_disbursement(disbursement)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "loan_detail",
            kwargs={"pk": self.object.pk},
        )


class LoanDisbursementListView(LoginRequiredMixin, ListView):
    model = LoanDisbursement
    template_name = "loans/disbursement_list.html"
    context_object_name = "disbursements"

    def dispatch(self, request, *args, **kwargs):
        self.loan = get_object_or_404(
            Loan,
            pk=self.kwargs["loan_id"],
            user=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return LoanDisbursement.objects.filter(loan=self.loan).order_by(
            "disbursement_number"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["loan"] = self.loan

        context["total_disbursed"] = self.loan.total_disbursed_amount

        context["remaining_sanction"] = self.loan.remaining_sanction_amount

        context["pending_interest"] = self.loan.total_pending_accrued_interest

        context["recovered_interest"] = self.loan.total_recovered_accrued_interest

        return context


class LoanDisbursementDetailView(LoginRequiredMixin, DetailView):
    model = LoanDisbursement
    template_name = "loans/disbursement_detail.html"
    context_object_name = "disbursement"

    def get_queryset(self):
        return LoanDisbursement.objects.select_related("loan").filter(
            loan__user=self.request.user
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["interest_entries"] = self.object.interest_entries.order_by("emi_date")

        context["total_interest"] = self.object.interest_entries.filter(
            status="recovered"
        ).aggregate(total=Sum("interest_amount"))["total"] or Decimal("0.00")

        context["pending_interest"] = self.object.interest_entries.filter(
            status="pending"
        ).aggregate(total=Sum("interest_amount"))["total"] or Decimal("0.00")

        return context


class LoanDisbursementCreateView(LoginRequiredMixin, CreateView):
    model = LoanDisbursement
    form_class = LoanDisbursementForm
    template_name = "loans/create_disbursement.html"

    def dispatch(self, request, *args, **kwargs):
        self.loan = get_object_or_404(
            Loan,
            pk=self.kwargs["loan_id"],
            user=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["loan"] = self.loan
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["loan"] = self.loan
        return context

    @transaction.atomic
    def form_valid(self, form):
        form.instance.loan = self.loan
        response = super().form_valid(form)

        if self.object.status == "released":
            AccruedInterestService.generate_for_disbursement(self.object)

        messages.success(self.request, "Loan disbursement created successfully.")
        return response

    def get_success_url(self):
        return reverse_lazy(
            "loan_disbursement_list",
            kwargs={"loan_id": self.loan.pk},
        )


class LoanDisbursementUpdateView(LoginRequiredMixin, UpdateView):
    model = LoanDisbursement
    form_class = LoanDisbursementForm
    template_name = "loans/create_disbursement.html"

    def get_queryset(self):
        return LoanDisbursement.objects.filter(loan__user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["loan"] = self.object.loan
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["loan"] = self.object.loan
        return context

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        LoanAccruedInterest.objects.filter(
            disbursement=self.object,
            status="pending",
        ).delete()
        if self.object.status == "released":
            AccruedInterestService.generate_for_disbursement(self.object)
        messages.success(self.request, "Disbursement updated successfully.")
        return response

    def get_success_url(self):
        return reverse_lazy(
            "loan_disbursement_list", kwargs={"loan_id": self.object.loan.pk}
        )


class LoanDisbursementDeleteView(LoginRequiredMixin, DeleteView):
    model = LoanDisbursement

    def get_queryset(self):
        return LoanDisbursement.objects.filter(loan__user=self.request.user)

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        loan_id = self.object.loan.pk
        LoanAccruedInterest.objects.filter(
            disbursement=self.object,
            status="pending",
        ).delete()
        self.object.delete()
        messages.success(request, "Disbursement deleted successfully.")
        return redirect(
            "loan_disbursement_list",
            loan_id=loan_id,
        )


@login_required
def documents_dashboard(request):
    """
    User Document Vault.

    Features:
        - Document listing
        - Search
        - Loan filter
        - Document type filter
        - Summary statistics
        - Pagination
    """
    loans = Loan.objects.filter(user=request.user).order_by("loan_name")
    documents = (
        LoanDocument.objects.filter(loan__user=request.user)
        .select_related("loan")
        .order_by("-uploaded_at")
    )
    search = request.GET.get("search", "").strip()
    if search:
        documents = documents.filter(
            Q(title__icontains=search) | Q(loan__loan_name__icontains=search)
        )
    loan_id = request.GET.get("loan")
    if loan_id:
        documents = documents.filter(loan_id=loan_id, loan__user=request.user)

    doc_type = request.GET.get("doc_type")
    if doc_type:
        documents = documents.filter(doc_type=doc_type)

    all_documents = LoanDocument.objects.filter(loan__user=request.user)
    total_documents = all_documents.count()
    loan_agreements = all_documents.filter(doc_type="agreement").count()
    pending_uploads = loans.filter(documents__isnull=True).count()
    storage_used = all_documents.aggregate(total=Sum("file_size"))["total"] or 0
    paginator = Paginator(documents, 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    form = LoanDocumentForm()
    context = {
        "page_title": "Documents",
        "documents": page_obj,
        "page_obj": page_obj,
        "loans": loans,
        "form": form,
        "total_documents": total_documents,
        "loan_agreements": loan_agreements,
        "pending_uploads": pending_uploads,
        "storage_used": storage_used,
        "search": search,
        "selected_loan": loan_id,
        "selected_doc_type": doc_type,
        "doc_types": LoanDocument.DOC_TYPES,
    }
    return render(request, "loans/documents_dashboard.html", context)


@login_required
def download_document(request, document_id):
    document = get_object_or_404(
        LoanDocument.objects.select_related("loan"),
        pk=document_id,
        loan__user=request.user,
    )
    if not document.file:
        raise Http404("Document file not found.")
    response = FileResponse(
        document.file.open("rb"),
        as_attachment=True,
        filename=document.file.name.split("/")[-1],
    )
    return response


@login_required
def view_document(request, document_id):
    document = get_object_or_404(
        LoanDocument.objects.select_related("loan"),
        pk=document_id,
        loan__user=request.user,
    )
    if not document.file:
        raise Http404("Document file not found.")
    response = FileResponse(document.file.open("rb"), as_attachment=False)
    return response


@login_required
def notifications_dashboard(request):
    notifications = Notification.objects.filter(user=request.user).select_related(
        "loan"
    )

    search = request.GET.get("search", "").strip()
    notification_type = request.GET.get("type", "").strip()
    status = request.GET.get("status", "").strip()

    if search:
        notifications = notifications.filter(
            Q(title__icontains=search)
            | Q(message__icontains=search)
            | Q(loan__loan_name__icontains=search)
        )

    if notification_type:
        notifications = notifications.filter(notification_type=notification_type)

    if status == "read":
        notifications = notifications.filter(is_read=True)
    elif status == "unread":
        notifications = notifications.filter(is_read=False)

    all_notifications = Notification.objects.filter(user=request.user)

    total_notifications = all_notifications.count()

    unread_count = all_notifications.filter(is_read=False).count()

    payment_alerts = all_notifications.filter(notification_type="payment").count()

    loan_updates = all_notifications.filter(notification_type="loan").count()

    paginator = Paginator(notifications, 12)

    page_number = request.GET.get("page")

    page_obj = paginator.get_page(page_number)

    context = {
        "page_title": "Notifications",
        "notifications": page_obj.object_list,
        "page_obj": page_obj,
        "total_notifications": total_notifications,
        "unread_count": unread_count,
        "payment_alerts": payment_alerts,
        "loan_updates": loan_updates,
        "search": search,
        "selected_type": notification_type,
        "selected_status": status,
        "notification_types": Notification.TYPE_CHOICES,
        "has_notifications": total_notifications > 0,
    }

    return render(
        request,
        "loans/notifications_dashboard.html",
        context,
    )


@login_required
def mark_notification_read(request, notification_id):
    notification = get_object_or_404(
        Notification,
        id=notification_id,
        user=request.user,
    )

    if request.method == "POST":
        notification.is_read = True
        notification.save(update_fields=["is_read"])

    next_url = request.POST.get("next")

    if next_url:
        return redirect(next_url)

    return redirect("notifications_dashboard")


@login_required
def mark_all_notifications_read(request):
    if request.method == "POST":
        Notification.objects.filter(
            user=request.user,
            is_read=False,
        ).update(is_read=True)

    return redirect("notifications_dashboard")


@login_required
def support_dashboard(request):
    tickets = (
        SupportTicket.objects.filter(user=request.user)
        .select_related("loan")
        .order_by("-created_at")
    )
    search = request.GET.get("search", "").strip()
    status = request.GET.get("status", "").strip()
    category = request.GET.get("category", "").strip()
    if search:
        tickets = tickets.filter(
            Q(ticket_number__icontains=search)
            | Q(subject__icontains=search)
            | Q(message__icontains=search)
        )
    if status:
        tickets = tickets.filter(status=status)
    if category:
        tickets = tickets.filter(category=category)
    paginator = Paginator(tickets, 10)
    page_obj = paginator.get_page(request.GET.get("page"))
    summary = get_support_ticket_summary(request.user)
    context = {
        "page_title": "Support Center",
        "tickets": page_obj.object_list,
        "page_obj": page_obj,
        "summary": summary,
        "search": search,
        "selected_status": status,
        "selected_category": category,
        "status_choices": SupportTicket.STATUS_CHOICES,
        "category_choices": SupportTicket.CATEGORY_CHOICES,
    }
    return render(
        request,
        "loans/support_dashboard.html",
        context,
    )


@login_required
def create_support_ticket(request):
    if request.method == "POST":
        form = SupportTicketForm(
            request.POST,
            request.FILES,
            user=request.user,
        )
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.user = request.user
            ticket.save()
            SupportMessage.objects.create(
                ticket=ticket,
                user=request.user,
                message=ticket.message,
                attachment=ticket.attachment,
                is_staff_reply=False,
            )
            create_notification(
                request.user,
                "Support Ticket Created",
                f"Your support ticket #{ticket.ticket_number} has been created.",
                "system",
                ticket.loan,
            )
            return redirect(
                "support_ticket_detail",
                ticket_id=ticket.id,
            )
    else:
        form = SupportTicketForm(user=request.user)
    return render(
        request,
        "loans/support_create_ticket.html",
        {
            "page_title": "Create Support Ticket",
            "form": form,
        },
    )


@login_required
def support_ticket_detail(request, ticket_id):
    ticket = get_object_or_404(
        SupportTicket.objects.select_related("loan"),
        id=ticket_id,
        user=request.user,
    )
    if request.method == "POST":
        if ticket.status in ["resolved", "closed"]:
            return redirect(
                "support_ticket_detail",
                ticket_id=ticket.id,
            )
        form = SupportReplyForm(
            request.POST,
            request.FILES,
        )
        if form.is_valid():
            reply = form.save(commit=False)
            reply.ticket = ticket
            reply.user = request.user
            reply.is_staff_reply = False
            reply.save()
            ticket.status = "open"
            ticket.last_response_at = timezone.now()
            ticket.save(
                update_fields=[
                    "status",
                    "last_response_at",
                    "updated_at",
                ]
            )
            return redirect(
                "support_ticket_detail",
                ticket_id=ticket.id,
            )
    else:
        form = SupportReplyForm()
    messages = ticket.messages.select_related("user").all()
    return render(
        request,
        "loans/support_ticket_detail.html",
        {
            "page_title": f"Ticket {ticket.ticket_number}",
            "ticket": ticket,
            "ticket_messages": messages,
            "form": form,
        },
    )


@login_required
def request_account_deactivation(request):
    if request.method != "POST":
        return redirect("settings")

    messages.success(
        request,
        "Your account deactivation request has been submitted.",
    )

    return redirect("settings")


@login_required
def request_account_deletion(request):
    if request.method != "POST":
        return redirect("settings")

    messages.success(
        request,
        "Your account deletion request has been submitted.",
    )

    return redirect("settings")


@login_required
def update_settings_profile(request):
    settings_data = ensure_user_settings(request.user)
    profile = settings_data["profile"]

    if request.method != "POST":
        return redirect("settings")

    form = SettingsProfileForm(
        request.POST,
        request.FILES,
        instance=profile,
    )

    if form.is_valid():
        form.save()
        messages.success(request, "Profile updated successfully.")
    else:
        messages.error(
            request,
            "Please correct the errors in your profile information.",
        )

    return redirect("settings")


@login_required
def change_settings_password(request):
    if request.method != "POST":
        return redirect("settings")

    form = SettingsPasswordForm(
        request.user,
        request.POST,
    )

    if form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)

        messages.success(
            request,
            "Your password has been changed successfully.",
        )
    else:
        messages.error(
            request,
            "Unable to change password. Please check the entered details.",
        )

    return redirect("settings")


@login_required
def update_notification_preferences(request):
    settings_data = ensure_user_settings(request.user)
    preferences = settings_data["notification_preferences"]

    if request.method != "POST":
        return redirect("settings")

    form = NotificationPreferenceForm(
        request.POST,
        instance=preferences,
    )

    if form.is_valid():
        form.save()

        messages.success(
            request,
            "Notification preferences updated successfully.",
        )
    else:
        messages.error(
            request,
            "Unable to update notification preferences.",
        )

    return redirect("settings")


@login_required
def update_appearance_preferences(request):
    settings_data = ensure_user_settings(request.user)
    preferences = settings_data["appearance_preferences"]

    if request.method != "POST":
        return redirect("settings")

    form = AppearancePreferenceForm(
        request.POST,
        instance=preferences,
    )

    if form.is_valid():
        form.save()

        messages.success(
            request,
            "Appearance preferences updated successfully.",
        )
    else:
        messages.error(
            request,
            "Unable to update appearance preferences.",
        )

    return redirect("settings")


@login_required
def update_privacy_settings(request):
    settings_data = ensure_user_settings(request.user)
    privacy_settings = settings_data["privacy_settings"]

    if request.method != "POST":
        return redirect("settings")

    form = PrivacySettingForm(
        request.POST,
        instance=privacy_settings,
    )

    if form.is_valid():
        form.save()

        messages.success(
            request,
            "Privacy settings updated successfully.",
        )
    else:
        messages.error(
            request,
            "Unable to update privacy settings.",
        )

    return redirect("settings")


@login_required
def update_settings_theme(request):
    if request.method != "POST":
        return redirect("settings")

    theme = request.POST.get("theme")

    allowed_themes = {value for value, label in AppearancePreference.THEME_CHOICES}

    if theme not in allowed_themes:
        messages.error(request, "Invalid theme selected.")
        return redirect("settings")

    settings_data = ensure_user_settings(request.user)
    appearance = settings_data["appearance_preferences"]

    appearance.theme = theme
    appearance.save(update_fields=["theme"])

    messages.success(request, "Theme preference updated.")

    return redirect("settings")


@login_required
def logout_all_devices(request):
    if request.method != "POST":
        return redirect("settings")

    current_session_key = request.session.session_key

    Session.objects.filter(
        expire_date__gte=timezone.now(),
    ).exclude(
        session_key=current_session_key,
    ).delete()

    messages.success(
        request,
        "You have been logged out from all other devices.",
    )

    return redirect("settings")


@login_required
def settings_dashboard(request):
    settings_data = ensure_user_settings(request.user)
    statistics = get_account_statistics(request.user)
    context = {
        "profile_form": SettingsProfileForm(instance=settings_data["profile"]),
        "password_form": SettingsPasswordForm(request.user),
        "bank_accounts": BankAccount.objects.filter(user=request.user).order_by(
            "-is_default", "-created_at"
        ),
        **settings_data,
        **statistics,
    }
    return render(request, "loans/settings.html", context)


@login_required
def add_bank_account(request):
    if request.method == "POST":
        form = BankAccountForm(request.POST)

        if form.is_valid():
            bank_account = form.save(commit=False)
            bank_account.user = request.user

            if bank_account.is_default:
                BankAccount.objects.filter(user=request.user).update(is_default=False)

            bank_account.save()

            messages.success(request, "Bank account added successfully.")

            return redirect("settings_dashboard")
    else:
        form = BankAccountForm()

    return render(
        request,
        "settings/add_bank_account.html",
        {"form": form},
    )


@login_required
def edit_bank_account(request, pk):
    bank_account = get_object_or_404(
        BankAccount,
        pk=pk,
        user=request.user,
    )

    if request.method == "POST":
        form = BankAccountForm(
            request.POST,
            instance=bank_account,
        )

        if form.is_valid():
            if form.cleaned_data.get("is_default"):
                BankAccount.objects.filter(user=request.user).exclude(
                    pk=bank_account.pk
                ).update(is_default=False)

            form.save()

            messages.success(request, "Bank account updated successfully.")

            return redirect("settings")
    else:
        form = BankAccountForm(instance=bank_account)

    return render(
        request,
        "settings/edit_bank_account.html",
        {
            "form": form,
            "bank_account": bank_account,
        },
    )


@login_required
def update_profile(request):
    profile = ensure_user_settings(request.user)["profile"]
    if request.method == "POST":
        form = SettingsProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save()
            user = request.user
            user.first_name = form.cleaned_data["first_name"]
            user.last_name = form.cleaned_data["last_name"]
            user.email = form.cleaned_data["email"]
            user.save(update_fields=["first_name", "last_name", "email"])
            messages.success(request, "Profile updated successfully.")
            return redirect("settings_dashboard")
    else:
        form = SettingsProfileForm(instance=profile)
    return render(
        request,
        "loans/settings.html",
        {
            "page_title": "Settings",
            "profile_form": form,
            **ensure_user_settings(request.user),
            **get_account_statistics(request.user),
            "bank_accounts": BankAccount.objects.filter(user=request.user).order_by(
                "-is_default", "-created_at"
            ),
        },
    )


@login_required
def update_password(request):
    settings_data = ensure_user_settings(request.user)
    if request.method == "POST":
        form = SettingsPasswordForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            settings_data["security_settings"].last_password_change = timezone.now()
            settings_data["security_settings"].save(
                update_fields=["last_password_change", "updated_at"]
            )
            messages.success(request, "Password changed successfully.")
            return redirect("settings_dashboard")
    else:
        form = SettingsPasswordForm(request.user)
    return render(
        request,
        "loans/settings.html",
        {
            "page_title": "Settings",
            "password_form": form,
            **settings_data,
            **get_account_statistics(request.user),
            "bank_accounts": BankAccount.objects.filter(user=request.user).order_by(
                "-is_default", "-created_at"
            ),
        },
    )


@login_required
def delete_bank_account(request, pk):
    bank = BankAccount.objects.filter(
        pk=pk,
        user=request.user,
    ).first()
    if not bank:
        messages.error(request, "Bank account not found.")
        return redirect("settings_dashboard")
    bank.delete()
    messages.success(request, "Bank account removed successfully.")
    return redirect("settings_dashboard")


@login_required
def set_default_bank_account(request, pk):
    bank = BankAccount.objects.filter(
        pk=pk,
        user=request.user,
    ).first()
    if not bank:
        messages.error(request, "Bank account not found.")
        return redirect("settings_dashboard")
    BankAccount.objects.filter(user=request.user).update(is_default=False)
    bank.is_default = True
    bank.save(update_fields=["is_default", "updated_at"])
    messages.success(request, "Default bank account updated.")
    return redirect("settings_dashboard")
