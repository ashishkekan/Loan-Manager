import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
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
        # Next EMI info
        if loan.status == "active":
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
        # Foreclosure calculation
        if loan.status == "active":
            context["foreclosure"] = calculate_foreclosure(loan)
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
