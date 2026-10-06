from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Avg, Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.views.generic import ListView, TemplateView

from dashboard.models import ActivityLog
from loans.models import Loan
from loans.utils import add_periods, generate_projected_schedule
from payments.models import Payment, Prepayment

User = get_user_model()


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kwargs):
        from loans.accounting import outstanding, schedule, ZERO

        context = super().get_context_data(**kwargs)
        loans = Loan.objects.select_related("user").all()
        if not self.request.user.is_staff:
            loans = loans.filter(user=self.request.user)
        loans = list(loans)
        payments = (
            Payment.objects.filter(loan__in=loans, status="paid")
            .select_related("loan")
            .order_by("-payment_date", "-pk")
        )
        prepays = Prepayment.objects.filter(loan__in=loans, status="paid")
        due = [row for loan in loans for row in schedule(loan) if not row["is_paid"]]
        due.sort(key=lambda row: row["due_date"])
        for loan in loans:
            loan.display_balance = outstanding(loan)
        context.update(
            loans=loans[:8],
            total_loans=len(loans),
            active_loans=sum(l.status == "active" for l in loans),
            total_remaining=sum((l.display_balance for l in loans), ZERO),
            total_released=sum((l.total_disbursed_amount for l in loans), ZERO),
            total_collected=(payments.aggregate(t=Sum("amount"))["t"] or ZERO)
            + (prepays.aggregate(t=Sum("amount"))["t"] or ZERO),
            total_overdue=sum((r["total_debit"] for r in due if r["is_overdue"]), ZERO),
            due_installments=due[:5],
            recent_payments=payments[:6],
            page_title="Overview",
        )
        return context


class AdminUsersView(LoginRequiredMixin, ListView):
    template_name = "dashboard/admin_users.html"
    context_object_name = "users"
    paginate_by = 15

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff:
            from django.shortcuts import redirect

            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = (
            User.objects.filter(is_staff=False)
            .select_related("profile")
            .annotate(
                total_loans=Count("loans", distinct=True),
                total_loan_amount=Coalesce(
                    Sum("loans__amount"), 0, output_field=DecimalField()
                ),
            )
            .order_by("-date_joined")
        )
        search = self.request.GET.get("q", "").strip()
        role = self.request.GET.get("role", "").strip()
        status = self.request.GET.get("status", "").strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
                | Q(profile__phone__icontains=search)
            )
        if role:
            queryset = queryset.filter(profile__role=role)
        if status == "active":
            queryset = queryset.filter(is_active=True)
        elif status == "inactive":
            queryset = queryset.filter(is_active=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        users = User.objects.filter(is_staff=False)
        context["total_users"] = users.count()
        context["active_users"] = users.filter(is_active=True).count()
        context["inactive_users"] = users.filter(is_active=False).count()
        context["search_query"] = self.request.GET.get("q", "")
        context["role_filter"] = self.request.GET.get("role", "")
        context["status_filter"] = self.request.GET.get("status", "")
        return context
