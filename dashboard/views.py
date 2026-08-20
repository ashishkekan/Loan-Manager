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
        context = super().get_context_data(**kwargs)
        user = self.request.user
        loans = Loan.objects.filter(user=user)
        if user.is_staff:
            self._build_admin_context(context)
        else:
            self._build_user_context(context, loans, user)
        return context

    def _build_admin_context(self, context):
        today = timezone.now().date()
        all_loans = Loan.objects.select_related("user").all()
        active_loans = all_loans.filter(status="active")
        closed_loans = all_loans.filter(status="closed")
        all_payments = Payment.objects.filter(status="paid")
        all_prepayments = Prepayment.objects.all()
        loan_stats = all_loans.aggregate(
            total_amount=Coalesce(
                Sum("amount"),
                0,
                output_field=DecimalField(),
            ),
            outstanding=Coalesce(
                Sum("remaining_balance"),
                0,
                output_field=DecimalField(),
            ),
            interest_paid=Coalesce(
                Sum("total_interest_paid"),
                0,
                output_field=DecimalField(),
            ),
            average_rate=Coalesce(
                Avg("interest_rate"),
                0,
                output_field=DecimalField(),
            ),
        )
        payment_stats = all_payments.aggregate(
            total_collected=Coalesce(
                Sum("amount"),
                0,
                output_field=DecimalField(),
            ),
            principal_collected=Coalesce(
                Sum("principal_component"),
                0,
                output_field=DecimalField(),
            ),
            interest_collected=Coalesce(
                Sum("interest_component"),
                0,
                output_field=DecimalField(),
            ),
        )
        prepayment_stats = all_prepayments.aggregate(
            total_prepaid=Coalesce(
                Sum("amount"),
                0,
                output_field=DecimalField(),
            ),
            interest_saved=Coalesce(
                Sum("interest_saved"),
                0,
                output_field=DecimalField(),
            ),
            months_saved=Coalesce(
                Sum("months_reduced"),
                0,
                output_field=DecimalField(),
            ),
        )
        total_principal = loan_stats["total_amount"] or 0
        outstanding = loan_stats["outstanding"] or 0
        principal_collected = payment_stats["principal_collected"] or 0
        total_collected = payment_stats["total_collected"] or 0
        context["admin_dashboard"] = True
        context["admin_total_users"] = User.objects.count()
        context["admin_active_users"] = User.objects.filter(is_active=True).count()
        context["admin_total_loans"] = all_loans.count()
        context["admin_active_loans"] = active_loans.count()
        context["admin_closed_loans"] = closed_loans.count()
        context["admin_total_amount"] = total_principal
        context["admin_outstanding"] = outstanding
        context["admin_principal_collected"] = principal_collected
        context["admin_total_collected"] = total_collected
        context["admin_interest_paid"] = loan_stats["interest_paid"] or 0
        context["admin_interest_collected"] = payment_stats["interest_collected"] or 0
        context["admin_average_rate"] = loan_stats["average_rate"] or 0
        context["admin_total_prepayments"] = prepayment_stats["total_prepaid"] or 0
        context["admin_interest_saved"] = prepayment_stats["interest_saved"] or 0
        context["admin_months_saved"] = prepayment_stats["months_saved"] or 0
        context["admin_auto_debit_loans"] = all_loans.filter(auto_debit=True).count()
        context["admin_manual_loans"] = all_loans.filter(auto_debit=False).count()
        context["admin_total_payments"] = all_payments.count()
        context["admin_loan_status_chart"] = {
            "labels": ["Active", "Closed"],
            "values": [
                active_loans.count(),
                closed_loans.count(),
            ],
        }
        context["admin_collection_chart"] = {
            "labels": ["Principal", "Interest"],
            "values": [
                float(payment_stats["principal_collected"] or 0),
                float(payment_stats["interest_collected"] or 0),
            ],
        }
        context["admin_recent_loans"] = all_loans.order_by("-created_at")[:8]
        context["admin_recent_payments"] = all_payments.select_related(
            "loan", "loan__user"
        ).order_by("-payment_date")[:8]
        context["admin_recent_prepayments"] = all_prepayments.select_related(
            "loan", "loan__user"
        ).order_by("-created_at")[:6]
        context["admin_recent_users"] = User.objects.order_by("-date_joined")[:6]
        context["activities"] = ActivityLog.objects.select_related(
            "user", "loan"
        ).order_by("-created_at")[:20]

    def _build_user_context(self, context, loans, user):
        aggregates = loans.aggregate(
            total_amount=Coalesce(
                Sum("amount"),
                0,
                output_field=DecimalField(),
            ),
            total_remaining=Coalesce(
                Sum("remaining_balance"),
                0,
                output_field=DecimalField(),
            ),
            total_interest=Coalesce(
                Sum("total_interest_paid"),
                0,
                output_field=DecimalField(),
            ),
        )
        active_loans = loans.filter(status="active")
        closed_loans = loans.filter(status="closed")
        monthly_emi = active_loans.aggregate(
            total=Coalesce(
                Sum("emi"),
                0,
                output_field=DecimalField(),
            )
        )["total"]
        context["total_loans"] = loans.count()
        context["active_loans"] = active_loans.count()
        context["closed_loans"] = closed_loans.count()
        context["auto_debit_loans"] = loans.filter(auto_debit=True).count()
        context["manual_loans"] = loans.filter(auto_debit=False).count()
        context["total_amount"] = aggregates["total_amount"]
        context["total_remaining"] = aggregates["total_remaining"]
        context["monthly_emi"] = monthly_emi
        context["total_interest_paid"] = aggregates["total_interest"]
        context["outstanding"] = aggregates["total_remaining"]
        context["interest_paid"] = aggregates["total_interest"]
        context["avg_interest_rate"] = (
            loans.aggregate(avg=Avg("interest_rate"))["avg"] or 0
        )
        total_payable = sum((loan.emi * loan.tenure_years * 12) for loan in loans)
        context["total_payable"] = total_payable
        context["projected_interest"] = total_payable - aggregates["total_amount"]
        prepay_stats = Prepayment.objects.filter(loan__user=user).aggregate(
            total_prepaid=Coalesce(
                Sum("amount"),
                0,
                output_field=DecimalField(),
            ),
            total_saved=Coalesce(
                Sum("interest_saved"),
                0,
                output_field=DecimalField(),
            ),
            total_months_saved=Coalesce(
                Sum("months_reduced"),
                0,
                output_field=DecimalField(),
            ),
        )
        context["total_prepaid"] = prepay_stats["total_prepaid"]
        context["total_interest_saved"] = prepay_stats["total_saved"]
        context["total_months_saved"] = prepay_stats["total_months_saved"]
        context["loans"] = loans.order_by("-created_at")
        active_loan = active_loans.first()
        if active_loan:
            projected = generate_projected_schedule(active_loan)
            context["balance_chart_json"] = {
                "labels": [f"M{row['period']}" for row in projected[:48]],
                "balances": [float(row["balance"]) for row in projected[:48]],
                "loan_name": active_loan.loan_name,
            }
        else:
            context["balance_chart_json"] = None
        total_principal_paid = (
            aggregates["total_amount"] - aggregates["total_remaining"]
        )
        context["pie_chart_json"] = {
            "principal": round(float(total_principal_paid), 2),
            "interest": round(float(aggregates["total_interest"]), 2),
        }
        context["recent_payments"] = (
            Payment.objects.filter(loan__user=user, status="paid")
            .select_related("loan")
            .order_by("-payment_date")[:8]
        )
        principal_paid = sum(loan.amount - loan.remaining_balance for loan in loans)
        total_prepayments = prepay_stats["total_prepaid"]
        total_paid = principal_paid + aggregates["total_interest"] + total_prepayments
        context["principal_paid"] = principal_paid
        context["total_prepayments"] = total_prepayments
        context["interest_saved"] = prepay_stats["total_saved"]
        context["total_paid"] = total_paid
        upcoming = []
        for loan in active_loans:
            next_num = loan.payments.filter(status="paid").count()
            due = add_periods(
                loan.schedule_start_date,
                next_num,
                loan.emi_frequency,
            )
            upcoming.append((due, loan))
        upcoming.sort(key=lambda item: item[0])
        context["upcoming_emi"] = upcoming[0] if upcoming else None
        context["next_emi_date"] = upcoming[0][0] if upcoming else None
        context["total_payments"] = Payment.objects.filter(
            loan__user=user, status="paid"
        ).count()
        context["activities"] = (
            ActivityLog.objects.filter(user=user)
            .select_related("loan")
            .order_by("-created_at")[:10]
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
                    Sum("loans__amount"),
                    0,
                    output_field=DecimalField(),
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
