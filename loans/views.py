import csv
import uuid
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from dashboard.models import ActivityLog
from dashboard.utils import add_activity
from loans.accounting import last_transaction_date, outstanding, refresh_balance
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
    SupportReplyForm,
    SupportTicketForm,
)
from loans.models import (
    AppearancePreference,
    BankAccount,
    Loan,
    LoanDisbursement,
    LoanDocument,
    LoanNote,
    Notification,
    SupportMessage,
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
    get_admin_statistics,
    get_period_details,
    get_support_ticket_summary,
    get_user_loans,
    simulate_extra_emi,
)
from payments.models import Payment, Prepayment

User = get_user_model()


class LoanListView(LoginRequiredMixin, ListView):
    model = Loan
    template_name = "loans/loan_list.html"
    context_object_name = "loans"
    paginate_by = 12

    def get_queryset(self):
        queryset = Loan.objects.select_related("user").order_by("-created_at")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)


class LoanCreateView(LoginRequiredMixin, CreateView):
    model = Loan
    form_class = LoanForm
    template_name = "loans/create_loan.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        if self.request.user.is_staff:
            form.instance.user = form.cleaned_data["user"]
        else:
            form.instance.user = self.request.user
        amount = form.cleaned_data["amount"]
        rate = form.cleaned_data["interest_rate"]
        tenure = form.cleaned_data["tenure_years"]

        form.instance.emi = form.cleaned_data.get("emi") or calculate_emi(
            amount, rate, tenure, form.cleaned_data["emi_frequency"]
        )
        form.instance.remaining_balance = Decimal("0.00")
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
        return get_user_loans(self.request.user).prefetch_related(
            "payments", "prepayments", "notes"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan = self.object
        rows = generate_full_schedule(loan)
        due = [r for r in rows if not r["is_paid"]]
        next_row = due[0] if due else None
        balance = outstanding(loan)
        context.update(
            page_title=loan.loan_name,
            display_balance=balance,
            total_released=loan.total_disbursed_amount,
            notes=loan.notes.all(),
            documents=loan.documents.all(),
            disbursements=loan.disbursements.order_by("disbursement_date"),
            note_form=LoanNoteForm(),
            document_form=LoanDocumentForm(),
            next_installment=next_row,
            schedule_preview=rows[:6],
            total_paid=(
                loan.payments.filter(status="paid").aggregate(t=Sum("amount"))["t"]
                or Decimal("0")
            )
            + loan.total_prepayment_amount,
            can_record_payment=bool(
                next_row
                and next_row["due_date"] <= timezone.localdate()
                and self.request.user.pk == loan.user_id
            ),
            today=timezone.localdate(),
        )
        context["payment_confirmation"] = signing.dumps(
            {
                "loan": loan.pk,
                "user": self.request.user.pk,
                "kind": "emi",
                "period": next_row["period"] if next_row else 0,
                "amount": str(next_row["total_debit"]) if next_row else "0",
            },
            salt="repayment",
        )
        context["prepayment_confirmation"] = signing.dumps(
            {
                "loan": loan.pk,
                "user": self.request.user.pk,
                "kind": "prepayment",
                "nonce": uuid.uuid4().hex,
            },
            salt="repayment",
        )
        return context


class LoanDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "loans/confirm_delete.html"
    model = Loan
    success_url = reverse_lazy("loan_list")

    def get_queryset(self):
        queryset = Loan.objects.all()
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)

    @transaction.atomic
    def form_valid(self, form):
        loan = Loan.objects.select_for_update().get(pk=self.object.pk)
        if (
            loan.payments.exists()
            or loan.prepayments.exists()
            or loan.investments.exists()
        ):
            messages.error(
                self.request, "Loans with financial history cannot be deleted."
            )
            return redirect("loan_detail", pk=loan.pk)
        return super().form_valid(form)


@login_required
@require_POST
def add_note(request, loan_id):
    if request.user.is_staff:
        loan = get_object_or_404(Loan, pk=loan_id)
    else:
        loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    if request.method == "POST":
        form = LoanNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.loan = loan
            note.save()
            messages.success(request, "Note added.")
    return redirect("loan_detail", pk=loan_id)


@login_required
@require_POST
def delete_note(request, loan_id, note_id):
    if request.user.is_staff:
        loan = get_object_or_404(Loan, pk=loan_id)
    else:
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


@login_required
@require_POST
def upload_document(request, loan_id=None):
    loan_id = loan_id or request.POST.get("loan")
    if not loan_id or not str(loan_id).isdigit():
        raise Http404("Select a loan.")
    if request.user.is_staff:
        loan = get_object_or_404(Loan, pk=loan_id)
    else:
        loan = get_object_or_404(Loan, pk=loan_id, user=request.user)
    if request.method == "POST":
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
    return redirect("loan_detail", pk=loan.pk)


@login_required
@require_POST
def delete_document(request, document_id, loan_id=None):
    if request.user.is_staff:
        document = get_object_or_404(LoanDocument, pk=document_id)
    else:
        document = get_object_or_404(
            LoanDocument, pk=document_id, loan__user=request.user
        )
    if request.method == "POST":
        title = document.title
        loan_id = document.loan_id
        document.delete()
        messages.success(request, f'"{title}" deleted successfully.')
        return redirect("loan_detail", pk=loan_id)
    return redirect("loan_detail", pk=document.loan_id)


@login_required
@require_POST
@transaction.atomic
def close_loan(request, pk):
    if request.user.is_staff:
        loan = get_object_or_404(Loan.objects.select_for_update(), pk=pk)
    else:
        loan = get_object_or_404(
            Loan.objects.select_for_update(), pk=pk, user=request.user
        )
    if request.method == "POST":
        if outstanding(loan) > 0:
            messages.error(
                request, "Record the outstanding repayment before closing this loan."
            )
            return redirect("loan_detail", pk=loan.pk)
        closing_date = parse_date(request.POST.get("closing_date", ""))
        if (
            not closing_date
            or closing_date < last_transaction_date(loan)
            or closing_date > timezone.localdate()
        ):
            messages.error(
                request,
                "Enter a valid closing date after the last transaction and no later than today.",
            )
            return redirect("loan_detail", pk=loan.pk)
        loan.status = "closed"
        loan.closed_date = closing_date
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
        queryset = Loan.objects.all()
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    @transaction.atomic
    def form_valid(self, form):
        locked = Loan.objects.select_for_update().get(pk=self.object.pk)
        if (locked.payments.exists() or locked.prepayments.exists()) and any(
            field in form.changed_data
            for field in (
                "amount",
                "interest_rate",
                "interest_basis",
                "emi",
                "tenure_years",
                "emi_frequency",
                "start_date",
                "first_emi_date",
                "user",
            )
        ):
            form.add_error(
                None, "Financial terms cannot change after transactions are recorded."
            )
            return self.form_invalid(form)
        if form.cleaned_data["amount"] < locked.total_disbursed_amount:
            form.add_error("amount", "Sanction cannot be less than released funds.")
            return self.form_invalid(form)
        # Do not overwrite balances changed by a concurrent repayment.
        form.instance.remaining_balance = locked.remaining_balance
        form.instance.total_interest_paid = locked.total_interest_paid
        form.instance.status = locked.status
        form.instance.closed_date = locked.closed_date
        amount = form.cleaned_data["amount"]
        rate = form.cleaned_data["interest_rate"]
        tenure = form.cleaned_data["tenure_years"]
        form.instance.emi = form.cleaned_data.get("emi") or calculate_emi(
            amount, rate, tenure, form.cleaned_data["emi_frequency"]
        )
        if not form.instance.first_emi_date:
            form.instance.first_emi_date = form.instance.start_date
        messages.success(self.request, "Loan updated successfully.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("loan_detail", kwargs={"pk": self.object.pk})


class LoanDisbursementListView(LoginRequiredMixin, ListView):
    model = LoanDisbursement
    template_name = "loans/disbursement_list.html"
    context_object_name = "disbursements"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_staff:
            self.loan = get_object_or_404(Loan, pk=self.kwargs["loan_id"])
        else:
            self.loan = get_object_or_404(
                Loan, pk=self.kwargs["loan_id"], user=request.user
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
        return context


class LoanDisbursementDetailView(LoginRequiredMixin, DetailView):
    model = LoanDisbursement
    template_name = "loans/disbursement_detail.html"
    context_object_name = "disbursement"

    def get_queryset(self):
        queryset = LoanDisbursement.objects.select_related("loan")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(loan__user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


class LoanDisbursementCreateView(LoginRequiredMixin, CreateView):
    model = LoanDisbursement
    form_class = LoanDisbursementForm
    template_name = "loans/create_disbursement.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_staff:
            self.loan = get_object_or_404(Loan, pk=self.kwargs["loan_id"])
        else:
            self.loan = get_object_or_404(
                Loan, pk=self.kwargs["loan_id"], user=request.user
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
        self.loan = Loan.objects.select_for_update().get(pk=self.loan.pk)
        form.instance.loan = self.loan
        if (
            form.cleaned_data["status"] == "released"
            and self.loan.total_disbursed_amount + form.cleaned_data["amount"]
            > self.loan.amount
        ):
            form.add_error("amount", "Release exceeds the remaining sanction.")
            return self.form_invalid(form)
        if form.cleaned_data["disbursement_date"] < last_transaction_date(self.loan):
            form.add_error(
                "disbursement_date", "Release cannot precede the last repayment."
            )
            return self.form_invalid(form)
        response = super().form_valid(form)
        refresh_balance(self.object.loan)
        messages.success(self.request, "Loan disbursement created successfully.")
        return response

    def get_success_url(self):
        return reverse_lazy("loan_disbursement_list", kwargs={"loan_id": self.loan.pk})


class LoanDisbursementUpdateView(LoginRequiredMixin, UpdateView):
    model = LoanDisbursement
    form_class = LoanDisbursementForm
    template_name = "loans/create_disbursement.html"

    def get_queryset(self):
        queryset = LoanDisbursement.objects.select_related("loan")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(loan__user=self.request.user)

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
        loan = Loan.objects.select_for_update().get(pk=self.object.loan_id)
        if loan.payments.exists() or loan.prepayments.exists():
            form.add_error(
                None,
                "Disbursements with repayment history cannot be changed. Add a new release instead.",
            )
            return self.form_invalid(form)
        original = LoanDisbursement.objects.get(pk=self.object.pk)
        released = loan.total_disbursed_amount - (
            original.amount if original.status == "released" else 0
        )
        if (
            form.cleaned_data["status"] == "released"
            and released + form.cleaned_data["amount"] > loan.amount
        ):
            form.add_error("amount", "Release exceeds the remaining sanction.")
            return self.form_invalid(form)
        response = super().form_valid(form)
        refresh_balance(loan)
        messages.success(self.request, "Disbursement updated.")
        return response

    def get_success_url(self):
        return reverse_lazy(
            "loan_disbursement_list", kwargs={"loan_id": self.object.loan.pk}
        )


class LoanDisbursementDeleteView(LoginRequiredMixin, DeleteView):
    model = LoanDisbursement

    def get_queryset(self):
        queryset = LoanDisbursement.objects.all()
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(loan__user=self.request.user)

    template_name = "loans/confirm_delete.html"

    @transaction.atomic
    def form_valid(self, form):
        loan = Loan.objects.select_for_update().get(pk=self.object.loan_id)
        if loan.payments.exists() or loan.prepayments.exists():
            messages.error(
                self.request, "Disbursements with repayment history cannot be deleted."
            )
        else:
            self.object.delete()
            refresh_balance(loan)
            messages.success(self.request, "Disbursement deleted.")
        return redirect("loan_disbursement_list", loan_id=loan.pk)


@login_required
def documents_dashboard(request):
    loans = get_user_loans(request.user).order_by("loan_name")
    documents = (
        LoanDocument.objects.filter(
            **({} if request.user.is_staff else {"loan__user": request.user})
        )
        .select_related("loan")
        .order_by("-uploaded_at")
    )
    search = request.GET.get("search", "").strip()
    if search:
        documents = documents.filter(
            Q(title__icontains=search) | Q(loan__loan_name__icontains=search)
        )
    loan_id = request.GET.get("loan")
    if loan_id and str(loan_id).isdigit():
        documents = documents.filter(loan_id=loan_id)

    doc_type = request.GET.get("doc_type")
    if doc_type:
        documents = documents.filter(doc_type=doc_type)

    all_documents = LoanDocument.objects.filter(
        **({} if request.user.is_staff else {"loan__user": request.user})
    )
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
        **({} if request.user.is_staff else {"loan__user": request.user}),
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
        **({} if request.user.is_staff else {"loan__user": request.user}),
    )
    if not document.file:
        raise Http404("Document file not found.")
    response = FileResponse(document.file.open("rb"), as_attachment=False)
    return response


@login_required
def notifications_dashboard(request):
    is_admin = request.user.is_staff or request.user.is_superuser
    if is_admin:
        notifications = (
            Notification.objects.select_related("user", "loan")
            .all()
            .order_by("-created_at")
        )
        all_notifications = Notification.objects.all()
    else:
        notifications = (
            Notification.objects.filter(user=request.user)
            .select_related("loan")
            .order_by("-created_at")
        )
        all_notifications = Notification.objects.filter(user=request.user)
    search = request.GET.get("search", "").strip()
    notification_type = request.GET.get("type", "").strip()
    status = request.GET.get("status", "").strip()
    if search:
        search_filter = (
            Q(title__icontains=search)
            | Q(message__icontains=search)
            | Q(loan__loan_name__icontains=search)
        )
        if is_admin:
            search_filter |= (
                Q(user__username__icontains=search)
                | Q(user__email__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )
        notifications = notifications.filter(search_filter)
    if notification_type:
        notifications = notifications.filter(notification_type=notification_type)
    if status == "read":
        notifications = notifications.filter(is_read=True)
    elif status == "unread":
        notifications = notifications.filter(is_read=False)
    total_notifications = all_notifications.count()
    unread_count = all_notifications.filter(is_read=False).count()
    payment_alerts = all_notifications.filter(notification_type="payment").count()
    loan_updates = all_notifications.filter(notification_type="loan").count()
    if is_admin:
        notified_users = all_notifications.values("user_id").distinct().count()
    else:
        notified_users = 1
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
        "notified_users": notified_users,
        "search": search,
        "selected_type": notification_type,
        "selected_status": status,
        "notification_types": Notification.TYPE_CHOICES,
        "has_notifications": total_notifications > 0,
        "is_admin": is_admin,
    }
    return render(request, "loans/notifications_dashboard.html", context)


@login_required
def mark_notification_read(request, notification_id):
    notification = get_object_or_404(
        Notification, id=notification_id, user=request.user
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
        Notification.objects.filter(user=request.user, is_read=False).update(
            is_read=True
        )
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
    return render(request, "loans/support_dashboard.html", context)


@login_required
def create_support_ticket(request):
    if request.method == "POST":
        form = SupportTicketForm(request.POST, request.FILES, user=request.user)
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
            return redirect("support_ticket_detail", ticket_id=ticket.id)
    else:
        form = SupportTicketForm(user=request.user)
    return render(
        request,
        "loans/support_create_ticket.html",
        {"page_title": "Create Support Ticket", "form": form},
    )


@login_required
def support_ticket_detail(request, ticket_id):
    ticket = get_object_or_404(
        SupportTicket.objects.select_related("loan"), id=ticket_id, user=request.user
    )
    if request.method == "POST":
        if ticket.status in ["resolved", "closed"]:
            return redirect("support_ticket_detail", ticket_id=ticket.id)
        form = SupportReplyForm(request.POST, request.FILES)
        if form.is_valid():
            reply = form.save(commit=False)
            reply.ticket = ticket
            reply.user = request.user
            reply.is_staff_reply = False
            reply.save()
            ticket.status = "open"
            ticket.last_response_at = timezone.now()
            ticket.save(update_fields=["status", "last_response_at", "updated_at"])
            return redirect("support_ticket_detail", ticket_id=ticket.id)
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
    messages.success(request, "Your account deactivation request has been submitted.")
    return redirect("settings")


@login_required
def request_account_deletion(request):
    if request.method != "POST":
        return redirect("settings")
    messages.success(request, "Your account deletion request has been submitted.")
    return redirect("settings")


@login_required
def update_settings_profile(request):
    settings_data = ensure_user_settings(request.user)
    profile = settings_data["profile"]
    if request.method != "POST":
        return redirect("settings")
    form = SettingsProfileForm(request.POST, request.FILES, instance=profile)
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
    form = SettingsPasswordForm(request.user, request.POST)
    if form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Your password has been changed successfully.")
    else:
        messages.error(
            request, "Unable to change password. Please check the entered details."
        )
    return redirect("settings")


@login_required
def edit_bank_account(request, pk):
    bank_account = get_object_or_404(BankAccount, pk=pk, user=request.user)
    if request.method == "POST":
        form = BankAccountForm(request.POST, instance=bank_account)
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
        {"form": form, "bank_account": bank_account},
    )


def staff_required(user):
    return user.is_authenticated and user.is_staff


@login_required
@user_passes_test(staff_required)
def admin_banks(request):
    banks = BankAccount.objects.select_related("user").order_by(
        "bank_name", "account_holder"
    )
    total_bank_accounts = banks.count()
    total_banks = BankAccount.objects.values("bank_name").distinct().count()
    total_users_with_bank = BankAccount.objects.values("user_id").distinct().count()
    default_accounts = BankAccount.objects.filter(is_default=True).count()
    bank_summary = (
        BankAccount.objects.values("bank_name")
        .annotate(
            account_count=Count("id", distinct=True),
            user_count=Count("user_id", distinct=True),
        )
        .order_by("bank_name")
    )
    context = {
        "page_title": "Banks",
        "banks": banks,
        "total_bank_accounts": total_bank_accounts,
        "total_banks": total_banks,
        "total_users_with_bank": total_users_with_bank,
        "default_accounts": default_accounts,
        "bank_summary": bank_summary,
    }
    return render(request, "loans/banks.html", context)


@login_required
def activity_logs_dashboard(request):
    if not request.user.is_staff:
        return redirect("dashboard")
    logs = (
        ActivityLog.objects.filter(user__isnull=False)
        .select_related("user", "loan")
        .order_by("-created_at")
    )
    search = request.GET.get("search", "").strip()
    action = request.GET.get("action", "").strip()
    date_range = request.GET.get("date_range", "").strip()
    if search:
        logs = logs.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(user__username__icontains=search)
            | Q(loan__loan_name__icontains=search)
        )
    if action:
        logs = logs.filter(action=action)
    today = timezone.localdate()
    if date_range == "today":
        logs = logs.filter(created_at__date=today)
    elif date_range == "7_days":
        logs = logs.filter(created_at__date__gte=today - timedelta(days=6))
    elif date_range == "30_days":
        logs = logs.filter(created_at__date__gte=today - timedelta(days=29))
    all_logs = ActivityLog.objects.all()
    total_logs = all_logs.count()
    today_logs = all_logs.filter(created_at__date=today).count()
    emi_paid_count = all_logs.filter(action="emi_paid").count()
    auto_debit_count = all_logs.filter(action="auto_debit").count()
    paginator = Paginator(logs, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    context = {
        "page_title": "Activity Logs",
        "logs": page_obj.object_list,
        "page_obj": page_obj,
        "total_logs": total_logs,
        "today_logs": today_logs,
        "emi_paid_count": emi_paid_count,
        "auto_debit_count": auto_debit_count,
        "search": search,
        "selected_action": action,
        "selected_date_range": date_range,
        "action_choices": ActivityLog.ACTIONS,
    }

    return render(request, "loans/activity_logs_dashboard.html", context)


def _resolve_target_user(request, user_id):
    if user_id:
        if not request.user.is_staff:
            return None, redirect("settings_dashboard")
        target_user = get_object_or_404(User, pk=user_id)
        return target_user, None
    return request.user, None


@login_required
def update_settings_theme(request, user_id=None):
    from loans.models import SiteAppearance
    from django.http import HttpResponseForbidden
    site = SiteAppearance.objects.filter(pk=1).first()
    if site and not site.allow_personal_themes and not request.user.is_staff:
        return HttpResponseForbidden("Your admin manages workspace appearance.")
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    if request.method != "POST":
        return redirect("settings_dashboard")

    theme = request.POST.get("theme")
    allowed_themes = {value for value, label in AppearancePreference.THEME_CHOICES}
    if theme not in allowed_themes:
        messages.error(request, "Invalid theme selected.")
        return redirect("settings_dashboard")

    settings_data = ensure_user_settings(target_user)
    appearance = settings_data["appearance_preferences"]
    appearance.theme = theme
    if appearance.color_palette == "inherit":
        appearance.color_palette = site.default_palette if site else "green"
    appearance.save(update_fields=["theme", "color_palette"])
    messages.success(request, "Theme preference updated.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def logout_all_devices(request, user_id=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    if request.method != "POST":
        return redirect("settings_dashboard")

    current_session_key = request.session.session_key
    sessions = Session.objects.filter(expire_date__gte=timezone.now()).exclude(
        session_key=current_session_key
    )
    keys = [
        session.session_key
        for session in sessions
        if session.get_decoded().get("_auth_user_id") == str(target_user.pk)
    ]
    Session.objects.filter(session_key__in=keys).delete()

    messages.success(request, "All other devices have been logged out.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def settings_dashboard(request, user_id=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    settings_data = ensure_user_settings(target_user)
    context = {
        "page_title": "Settings",
        "target_user": target_user,
        "is_admin": request.user.is_staff,
        "is_admin_editing": bool(
            request.user.is_staff and user_id and target_user != request.user
        ),
        "all_users": (
            User.objects.filter(is_active=True).order_by("username")
            if request.user.is_staff
            else []
        ),
        "profile_form": SettingsProfileForm(instance=settings_data["profile"]),
        "password_form": SettingsPasswordForm(target_user),
        "bank_accounts": BankAccount.objects.filter(user=target_user).order_by(
            "-is_default", "-created_at"
        ),
        **settings_data,
    }
    if request.user.is_staff:
        context.update(get_admin_statistics())
        if target_user != request.user:
            context.update(get_account_statistics(target_user))
    else:
        context.update(get_account_statistics(request.user))
    return render(request, "loans/settings.html", context)


@login_required
def update_profile(request, user_id=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    settings_data = ensure_user_settings(target_user)
    profile = settings_data["profile"]
    if request.method == "POST":
        form = SettingsProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save()
            target_user.first_name = form.cleaned_data["first_name"]
            target_user.last_name = form.cleaned_data["last_name"]
            target_user.email = form.cleaned_data["email"]
            target_user.save(update_fields=["first_name", "last_name", "email"])
            messages.success(request, "Profile updated successfully.")
            return (
                redirect("settings_dashboard_user", user_id=target_user.id)
                if user_id
                else redirect("settings_dashboard")
            )
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def update_password(request, user_id=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    settings_data = ensure_user_settings(target_user)
    if request.method == "POST":
        form = SettingsPasswordForm(target_user, request.POST)
        if form.is_valid():
            user = form.save()
            if target_user == request.user:
                update_session_auth_hash(request, user)
            settings_data["security_settings"].last_password_change = timezone.now()
            settings_data["security_settings"].save(
                update_fields=["last_password_change", "updated_at"]
            )
            messages.success(request, "Password changed successfully.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def update_notification_preferences(request, user_id=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    settings_data = ensure_user_settings(target_user)
    preferences = settings_data["notification_preferences"]
    if request.method == "POST":
        form = NotificationPreferenceForm(request.POST, instance=preferences)
        if form.is_valid():
            form.save()
            messages.success(request, "Notification preferences updated successfully.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def update_appearance_preferences(request, user_id=None):
    from loans.models import SiteAppearance
    from django.http import HttpResponseForbidden
    site = SiteAppearance.objects.filter(pk=1).first()
    if site and not site.allow_personal_themes and not request.user.is_staff:
        return HttpResponseForbidden("Your admin manages workspace appearance.")
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    settings_data = ensure_user_settings(target_user)
    preferences = settings_data["appearance_preferences"]
    if request.method == "POST":
        form = AppearancePreferenceForm(request.POST, instance=preferences)
        if form.is_valid():
            if preferences.color_palette == "inherit":
                preferences.color_palette = site.default_palette if site else "green"
            form.save()
            messages.success(request, "Appearance preferences updated successfully.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def update_privacy_settings(request, user_id=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    settings_data = ensure_user_settings(target_user)
    privacy_settings = settings_data["privacy_settings"]
    if request.method == "POST":
        form = PrivacySettingForm(request.POST, instance=privacy_settings)
        if form.is_valid():
            form.save()
            messages.success(request, "Privacy settings updated successfully.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def add_bank_account(request, user_id=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    if request.method == "POST":
        form = BankAccountForm(request.POST)
        if form.is_valid():
            bank_account = form.save(commit=False)
            bank_account.user = target_user
            if bank_account.is_default:
                BankAccount.objects.filter(user=target_user).update(is_default=False)
            bank_account.save()
            messages.success(request, "Bank account added successfully.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def delete_bank_account(request, user_id=None, pk=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    bank = BankAccount.objects.filter(pk=pk, user=target_user).first()
    if not bank:
        messages.error(request, "Bank account not found.")
    else:
        bank.delete()
        messages.success(request, "Bank account removed successfully.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def set_default_bank_account(request, user_id=None, pk=None):
    target_user, error_redirect = _resolve_target_user(request, user_id)
    if error_redirect:
        return error_redirect

    bank = BankAccount.objects.filter(pk=pk, user=target_user).first()
    if not bank:
        messages.error(request, "Bank account not found.")
    else:
        BankAccount.objects.filter(user=target_user).update(is_default=False)
        bank.is_default = True
        bank.save(update_fields=["is_default", "updated_at"])
        messages.success(request, "Default bank account updated.")
    return (
        redirect("settings_dashboard_user", user_id=target_user.id)
        if user_id
        else redirect("settings_dashboard")
    )


@login_required
def protected_media(request, path):
    from loans.models import UserProfile
    from django.db.models import Q

    candidates = [
        LoanDocument.objects.filter(file=path),
        SupportTicket.objects.filter(attachment=path),
        SupportMessage.objects.filter(attachment=path),
        UserProfile.objects.filter(photo=path),
    ]
    ownership = ["loan__user", "user", "ticket__user", "user"]
    fields = ["file", "attachment", "attachment", "photo"]
    for records, owner, field in zip(candidates, ownership, fields):
        if not request.user.is_staff:
            records = records.filter(**{owner: request.user})
        record = records.first()
        if record:
            asset = getattr(record, field)
            try:
                response = FileResponse(asset.open("rb"))
            except FileNotFoundError:
                raise Http404("File not found.")
            response["X-Content-Type-Options"] = "nosniff"
            response["Cache-Control"] = "private, no-store"
            return response
    raise Http404("File not found.")
