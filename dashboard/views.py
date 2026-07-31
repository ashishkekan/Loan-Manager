from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Avg, DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.views.generic import TemplateView

from loans.models import Loan
from loans.utils import add_periods, generate_projected_schedule
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
        context["active_loans"] = loans.filter(status="active").count()
        context["closed_loans"] = loans.filter(status="closed").count()
        context["auto_debit_loans"] = loans.filter(auto_debit=True).count()
        context["manual_loans"] = loans.filter(auto_debit=False).count()
        context["outstanding"] = (
            loans.aggregate(total=Sum("remaining_balance"))["total"] or 0
        )
        context["interest_paid"] = (
            loans.aggregate(total=Sum("total_interest_paid"))["total"] or 0
        )
        total_payable = sum(float(l.total_payable) for l in loans)

        context["total_payable"] = total_payable
        context["projected_interest"] = total_payable - float(
            aggregates["total_amount"]
        )
        context["avg_interest_rate"] = (
            loans.aggregate(avg=Avg("interest_rate"))["avg"] or 0
        )

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
        principal_paid = 0
        for loan in loans:
            principal_paid += loan.amount - loan.remaining_balance
        context["principal_paid"] = principal_paid
        context["total_paid"] = principal_paid + context["interest_paid"]
        context["total_prepayments"] = (
            Prepayment.objects.filter(loan__user=user).aggregate(total=Sum("amount"))[
                "total"
            ]
            or 0
        )
        context["total_prepayments"] = (
            Prepayment.objects.filter(loan__user=user).aggregate(total=Sum("amount"))[
                "total"
            ]
            or 0
        )
        context["interest_saved"] = (
            Prepayment.objects.filter(loan__user=user).aggregate(
                total=Sum("interest_saved")
            )["total"]
            or 0
        )
        upcoming = []
        today = timezone.now().date()
        for loan in loans.filter(status="active"):
            next_num = loan.payments.filter(status="paid").count()
            due = add_periods(loan.schedule_start_date, next_num, loan.emi_frequency)
            upcoming.append((due, loan))
        upcoming.sort(key=lambda x: x[0])
        context["upcoming_emi"] = upcoming[0] if upcoming else None
        if upcoming:
            context["next_emi_date"] = upcoming[0][0]
        else:
            context["next_emi_date"] = None
        total_payments = Payment.objects.filter(loan__user=user, status="paid").count()
        context["total_payments"] = total_payments
        return context
