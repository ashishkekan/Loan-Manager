from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.views.generic import TemplateView

from loans.models import Loan
from loans.utils import generate_projected_schedule
from payments.models import Payment, Prepayment


class DashboardView(LoginRequiredMixin, TemplateView):
    """Main dashboard with summary cards, charts, and recent activity."""

    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        loans = Loan.objects.filter(user=user)

        # ── Summary Stats ──
        total_loans = loans.count()
        active_loans = loans.filter(status="active").count()

        aggregates = loans.aggregate(
            total_amount=Coalesce(Sum("amount"), 0, output_field=DecimalField()),
            total_remaining=Coalesce(
                Sum("remaining_balance"), 0, output_field=DecimalField()
            ),
            total_interest=Coalesce(
                Sum("total_interest_paid"), 0, output_field=DecimalField()
            ),
        )

        monthly_emi = loans.filter(status="active").aggregate(
            total=Coalesce(Sum("emi"), 0, output_field=DecimalField())
        )["total"]

        context["total_loans"] = total_loans
        context["active_loans"] = active_loans
        context["total_amount"] = aggregates["total_amount"]
        context["total_remaining"] = aggregates["total_remaining"]
        context["total_interest_paid"] = aggregates["total_interest"]
        context["monthly_emi"] = monthly_emi

        # ── Prepayment Savings ──
        prepay_stats = Prepayment.objects.filter(loan__user=user).aggregate(
            total_prepaid=Coalesce(Sum("amount"), 0, output_field=DecimalField()),
            total_saved=Coalesce(Sum("interest_saved"), 0, output_field=DecimalField()),
            total_months_saved=Coalesce(
                Sum("months_reduced"), 0, output_field=DecimalField()
            ),
        )
        context["total_prepaid"] = prepay_stats["total_prepaid"]
        context["total_interest_saved"] = prepay_stats["total_saved"]
        context["total_months_saved"] = prepay_stats["total_months_saved"]

        # ── Loan List (for sidebar + dashboard cards) ──
        context["loans"] = loans.order_by("-created_at")

        # ── Balance Over Time Chart (first active loan) ──
        active_loan = loans.filter(status="active").first()
        if active_loan:
            projected = generate_projected_schedule(active_loan)
            labels = [f"M{r['period']}" for r in projected[:48]]
            balances = [float(r["balance"]) for r in projected[:48]]

            context["balance_chart_json"] = {
                "labels": labels,
                "balances": balances,
                "loan_name": active_loan.loan_name,
            }
        else:
            context["balance_chart_json"] = None

        # ── Principal vs Interest Pie Chart ──
        total_principal_paid = float(
            aggregates["total_amount"] - aggregates["total_remaining"]
        )
        total_interest = float(aggregates["total_interest"])

        context["pie_chart_json"] = {
            "principal": round(total_principal_paid, 2),
            "interest": round(total_interest, 2),
        }

        # ── Recent Payments (last 8 across all loans) ──
        context["recent_payments"] = (
            Payment.objects.filter(loan__user=user, status="paid")
            .select_related("loan")
            .order_by("-payment_date")[:8]
        )

        # ── Quick Stats for top section ──
        context["closed_loans"] = loans.filter(status="closed").count()
        context["total_payable"] = sum(
            float(l.emi * l.tenure_years * 12) for l in loans
        )

        return context
