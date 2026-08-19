"""Views for the admin-only Reports section."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone

from loans.models import Loan
from loans.report_exports import export_report
from loans.reports import (
    get_available_banks,
    get_available_users,
    get_loan_portfolio_qs,
    get_overdue_qs,
    get_overdue_summary,
    get_payment_collection_qs,
    get_payment_summary,
    get_performance_data,
    get_report_filters,
    get_reports_kpis,
    get_user_summary_qs,
)
from loans.utils import add_periods, get_period_details
from payments.models import Payment

REPORTS_PER_PAGE = 25

VALID_REPORTS = [
    "loan_portfolio",
    "payment_collection",
    "overdue",
    "user_summary",
    "performance",
]
VALID_FORMATS = ["excel", "csv", "pdf"]


def _staff_required(user):
    return user.is_authenticated and user.is_staff


@login_required
def admin_reports(request):
    """Main admin reports page — KPIs, filters, and tabbed report data."""
    if not request.user.is_staff:
        messages.error(request, "You do not have permission to access Reports.")
        return redirect("dashboard")

    f = get_report_filters(request)

    for error in f["errors"]:
        messages.error(request, error)

    active_report = request.GET.get("report", "loan_portfolio")
    if active_report not in VALID_REPORTS:
        active_report = "loan_portfolio"

    kpis = get_reports_kpis(f)

    context = {
        "kpis": kpis,
        "filters": f,
        "active_report": active_report,
        "users": get_available_users(),
        "banks": get_available_banks(),
        "loan_types": Loan.LOAN_TYPE_CHOICES,
        "loan_statuses": Loan.STATUS_CHOICES,
        "payment_statuses": Payment.STATUS_CHOICES,
        "payment_modes": Payment.PAYMENT_MODE_CHOICES,
        "overdue_buckets": [
            ("1-30", "1–30 Days"),
            ("31-60", "31–60 Days"),
            ("61-90", "61–90 Days"),
            ("90+", "90+ Days"),
        ],
    }

    if active_report == "loan_portfolio":
        qs = get_loan_portfolio_qs(f)
        paginator = Paginator(qs, REPORTS_PER_PAGE)
        page_obj = paginator.get_page(request.GET.get("page"))
        for loan in page_obj.object_list:
            _, ppy = get_period_details(loan.emi_frequency)
            loan.computed_end_date = add_periods(
                loan.schedule_start_date,
                loan.tenure_years * ppy,
                loan.emi_frequency,
            )
        context["page_obj"] = page_obj

    elif active_report == "payment_collection":
        qs = get_payment_collection_qs(f)
        paginator = Paginator(qs, REPORTS_PER_PAGE)
        page_obj = paginator.get_page(request.GET.get("page"))
        context["page_obj"] = page_obj
        context["report_summary"] = get_payment_summary(f)

    elif active_report == "overdue":
        qs = get_overdue_qs(f)
        paginator = Paginator(qs, REPORTS_PER_PAGE)
        page_obj = paginator.get_page(request.GET.get("page"))
        today = timezone.localdate()
        for p in page_obj.object_list:
            p.computed_days_overdue = (today - p.due_date).days
        context["page_obj"] = page_obj
        context["report_summary"] = get_overdue_summary(f)

    elif active_report == "user_summary":
        qs = get_user_summary_qs(f)
        paginator = Paginator(qs, REPORTS_PER_PAGE)
        page_obj = paginator.get_page(request.GET.get("page"))
        context["page_obj"] = page_obj

    elif active_report == "performance":
        data, group_label = get_performance_data(f)
        context["performance_data"] = data
        context["group_label"] = group_label

    return render(request, "loans/admin_reports.html", context)


@login_required
def export_admin_report(request, report_type, format):
    """Export a report to Excel, CSV, or PDF — respecting active filters."""
    if not request.user.is_staff:
        messages.error(request, "You do not have permission to export reports.")
        return redirect("dashboard")

    if report_type not in VALID_REPORTS:
        messages.error(request, "Invalid report type.")
        return redirect("admin_reports")

    if format not in VALID_FORMATS:
        messages.error(request, "Invalid export format.")
        return redirect("admin_reports")

    f = get_report_filters(request)

    for error in f["errors"]:
        messages.error(request, error)

    try:
        return export_report(report_type, format, f)
    except Exception as exc:
        messages.error(request, f"Export failed: {exc}")
        return redirect("admin_reports")
