from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, OuterRef, Q, Subquery, Sum
from django.utils import timezone

from loans.models import BankAccount, Loan
from loans.utils import add_periods, get_period_details
from payments.models import Payment

User = get_user_model()


def get_report_filters(request):
    """Parse and validate common report filters from GET parameters."""
    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()

    from_date = None
    to_date = None
    errors = []

    if raw_from:
        try:
            from_date = date.fromisoformat(raw_from)
        except ValueError:
            errors.append("Invalid 'Date From' value.")
    if raw_to:
        try:
            to_date = date.fromisoformat(raw_to)
        except ValueError:
            errors.append("Invalid 'Date To' value.")

    if from_date and to_date and from_date > to_date:
        errors.append("'Date From' cannot be later than 'Date To'.")

    user_id = request.GET.get("user", "").strip()
    loan_type = request.GET.get("loan_type", "").strip()
    status = request.GET.get("status", "").strip()
    payment_status = request.GET.get("payment_status", "").strip()
    payment_mode = request.GET.get("payment_mode", "").strip()
    overdue_bucket = request.GET.get("overdue_days", "").strip()
    group_by = request.GET.get("group_by", "loan_type").strip()
    bank_name = request.GET.get("bank", "").strip()

    selected_user = None
    if user_id:
        try:
            uid = int(user_id)
            selected_user = User.objects.filter(pk=uid).first()
            if not selected_user:
                errors.append("Selected user not found.")
        except (ValueError, TypeError):
            errors.append("Invalid user selected.")

    if loan_type and loan_type not in dict(Loan.LOAN_TYPE_CHOICES):
        errors.append("Invalid loan type selected.")
        loan_type = ""

    if status and status not in dict(Loan.STATUS_CHOICES):
        errors.append("Invalid loan status selected.")
        status = ""

    if payment_status and payment_status not in dict(Payment.STATUS_CHOICES):
        payment_status = ""

    if payment_mode and payment_mode not in dict(Payment.PAYMENT_MODE_CHOICES):
        payment_mode = ""

    if group_by not in ("bank", "loan_type"):
        group_by = "loan_type"

    return {
        "from_date": raw_from,
        "to_date": raw_to,
        "from_date_obj": from_date,
        "to_date_obj": to_date,
        "user_id": user_id,
        "selected_user": selected_user,
        "loan_type": loan_type,
        "status": status,
        "payment_status": payment_status,
        "payment_mode": payment_mode,
        "overdue_bucket": overdue_bucket,
        "group_by": group_by,
        "bank_name": bank_name,
        "errors": errors,
        "is_filtered": bool(
            raw_from
            or raw_to
            or user_id
            or loan_type
            or status
            or payment_status
            or payment_mode
            or overdue_bucket
            or bank_name
        ),
    }


def _apply_loan_filters(qs, f):
    """Apply common filters to a Loan queryset (date → start_date)."""
    if f["from_date_obj"]:
        qs = qs.filter(start_date__gte=f["from_date_obj"])
    if f["to_date_obj"]:
        qs = qs.filter(start_date__lte=f["to_date_obj"])
    if f["user_id"]:
        try:
            qs = qs.filter(user_id=int(f["user_id"]))
        except (ValueError, TypeError):
            pass
    if f["loan_type"]:
        qs = qs.filter(loan_type=f["loan_type"])
    if f["status"]:
        qs = qs.filter(status=f["status"])
    return qs


def _apply_payment_filters(qs, f):
    """Apply common filters to a Payment queryset (date → payment_date)."""
    if f["from_date_obj"]:
        qs = qs.filter(payment_date__gte=f["from_date_obj"])
    if f["to_date_obj"]:
        qs = qs.filter(payment_date__lte=f["to_date_obj"])
    if f["user_id"]:
        try:
            qs = qs.filter(loan__user_id=int(f["user_id"]))
        except (ValueError, TypeError):
            pass
    if f["loan_type"]:
        qs = qs.filter(loan__loan_type=f["loan_type"])
    if f["status"]:
        qs = qs.filter(loan__status=f["status"])
    if f["payment_status"]:
        qs = qs.filter(status=f["payment_status"])
    if f["payment_mode"]:
        qs = qs.filter(payment_mode=f["payment_mode"])
    return qs


def _apply_overdue_filters(qs, f):
    """Apply common + overdue-specific filters to a Payment queryset."""
    if f["from_date_obj"]:
        qs = qs.filter(due_date__gte=f["from_date_obj"])
    if f["to_date_obj"]:
        qs = qs.filter(due_date__lte=f["to_date_obj"])
    if f["user_id"]:
        try:
            qs = qs.filter(loan__user_id=int(f["user_id"]))
        except (ValueError, TypeError):
            pass
    if f["loan_type"]:
        qs = qs.filter(loan__loan_type=f["loan_type"])
    if f["status"]:
        qs = qs.filter(loan__status=f["status"])
    today = timezone.localdate()
    bucket = f["overdue_bucket"]
    if bucket == "1-30":
        qs = qs.filter(due_date__gte=today - timedelta(days=30), due_date__lt=today)
    elif bucket == "31-60":
        qs = qs.filter(
            due_date__gte=today - timedelta(days=60),
            due_date__lt=today - timedelta(days=30),
        )
    elif bucket == "61-90":
        qs = qs.filter(
            due_date__gte=today - timedelta(days=90),
            due_date__lt=today - timedelta(days=60),
        )
    elif bucket == "90+":
        qs = qs.filter(due_date__lt=today - timedelta(days=90))
    return qs


def get_reports_kpis(f):
    """Return the 4 top-level KPI values shown on the reports page."""
    loan_qs = _apply_loan_filters(Loan.objects.all(), f)

    total_loans = loan_qs.count()
    total_disbursed = loan_qs.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    total_outstanding = loan_qs.filter(status="active").aggregate(
        t=Sum("remaining_balance")
    )["t"] or Decimal("0")

    overdue_qs = Payment.objects.filter(status="overdue")
    if f["from_date_obj"]:
        overdue_qs = overdue_qs.filter(due_date__gte=f["from_date_obj"])
    if f["to_date_obj"]:
        overdue_qs = overdue_qs.filter(due_date__lte=f["to_date_obj"])
    if f["user_id"]:
        try:
            overdue_qs = overdue_qs.filter(loan__user_id=int(f["user_id"]))
        except (ValueError, TypeError):
            pass
    if f["loan_type"]:
        overdue_qs = overdue_qs.filter(loan__loan_type=f["loan_type"])

    total_overdue = overdue_qs.aggregate(t=Sum("total_debit_amount"))["t"] or Decimal(
        "0"
    )

    return {
        "total_loans": total_loans,
        "total_disbursed": total_disbursed,
        "total_outstanding": total_outstanding,
        "total_overdue": total_overdue,
        "active_loans": loan_qs.filter(status="active").count(),
        "closed_loans": loan_qs.filter(status="closed").count(),
    }


def get_loan_portfolio_qs(f):
    """Return a filtered Loan queryset for the portfolio report."""
    return _apply_loan_filters(Loan.objects.select_related("user"), f).order_by(
        "-created_at"
    )


def get_payment_collection_qs(f):
    """Return a filtered Payment queryset for the collection report."""
    return _apply_payment_filters(
        Payment.objects.select_related("loan", "loan__user"), f
    ).order_by("-payment_date", "-payment_number")


def get_payment_summary(f):
    """Return aggregate summary for the payment collection report."""
    qs = _apply_payment_filters(Payment.objects.all(), f)
    today = timezone.localdate()
    return {
        "total_payments": qs.count(),
        "total_collected": qs.filter(status="paid").aggregate(
            t=Sum("total_debit_amount")
        )["t"]
        or Decimal("0"),
        "pending_amount": qs.filter(status="pending").aggregate(
            t=Sum("total_debit_amount")
        )["t"]
        or Decimal("0"),
        "overdue_amount": qs.filter(status="overdue").aggregate(
            t=Sum("total_debit_amount")
        )["t"]
        or Decimal("0"),
        "successful_payments": qs.filter(status="paid").count(),
        "failed_payments": 0,
        "upcoming_payments": qs.filter(status="pending", due_date__gte=today).count(),
    }


def get_overdue_qs(f):
    """Return a filtered Payment queryset for the overdue report."""
    qs = Payment.objects.select_related("loan", "loan__user").filter(status="overdue")
    return _apply_overdue_filters(qs, f).order_by("due_date")


def get_overdue_summary(f):
    """Return aggregate summary for the overdue report."""
    today = timezone.localdate()
    base = Payment.objects.filter(status="overdue")
    base = _apply_overdue_filters(base, f)

    return {
        "total_overdue_loans": base.values("loan_id").distinct().count(),
        "total_overdue_emis": base.count(),
        "total_overdue_amount": base.aggregate(t=Sum("total_debit_amount"))["t"]
        or Decimal("0"),
        "bucket_1_30": base.filter(
            due_date__gte=today - timedelta(days=30), due_date__lt=today
        ).count(),
        "bucket_31_60": base.filter(
            due_date__gte=today - timedelta(days=60),
            due_date__lt=today - timedelta(days=30),
        ).count(),
        "bucket_61_90": base.filter(
            due_date__gte=today - timedelta(days=90),
            due_date__lt=today - timedelta(days=60),
        ).count(),
        "bucket_90_plus": base.filter(due_date__lt=today - timedelta(days=90)).count(),
    }


def get_user_summary_qs(f):
    """Return an annotated User queryset for the user financial summary."""
    qs = User.objects.filter(is_active=True)

    if f["user_id"]:
        try:
            qs = qs.filter(pk=int(f["user_id"]))
        except (ValueError, TypeError):
            pass
    if f["from_date_obj"]:
        qs = qs.filter(date_joined__date__gte=f["from_date_obj"])
    if f["to_date_obj"]:
        qs = qs.filter(date_joined__date__lte=f["to_date_obj"])

    loan_base = Loan.objects.filter(user=OuterRef("pk"))
    if f["loan_type"]:
        loan_base = loan_base.filter(loan_type=f["loan_type"])
    if f["status"]:
        loan_base = loan_base.filter(status=f["status"])
    if f["from_date_obj"]:
        loan_base = loan_base.filter(start_date__gte=f["from_date_obj"])
    if f["to_date_obj"]:
        loan_base = loan_base.filter(start_date__lte=f["to_date_obj"])

    total_loans_sq = loan_base.values("user").annotate(c=Count("id")).values("c")[:1]
    active_sq = (
        loan_base.filter(status="active")
        .values("user")
        .annotate(c=Count("id"))
        .values("c")[:1]
    )
    completed_sq = (
        loan_base.filter(status="closed")
        .values("user")
        .annotate(c=Count("id"))
        .values("c")[:1]
    )
    borrowed_sq = loan_base.values("user").annotate(t=Sum("amount")).values("t")[:1]
    outstanding_sq = (
        loan_base.filter(status="active")
        .values("user")
        .annotate(t=Sum("remaining_balance"))
        .values("t")[:1]
    )

    pay_base = Payment.objects.filter(loan__user=OuterRef("pk"))
    if f["loan_type"]:
        pay_base = pay_base.filter(loan__loan_type=f["loan_type"])
    if f["status"]:
        pay_base = pay_base.filter(loan__status=f["status"])
    if f["from_date_obj"]:
        pay_base = pay_base.filter(loan__start_date__gte=f["from_date_obj"])
    if f["to_date_obj"]:
        pay_base = pay_base.filter(loan__start_date__lte=f["to_date_obj"])

    repaid_sq = (
        pay_base.filter(status="paid")
        .values("loan__user")
        .annotate(t=Sum("principal_component"))
        .values("t")[:1]
    )
    overdue_amt_sq = (
        pay_base.filter(status="overdue")
        .values("loan__user")
        .annotate(t=Sum("total_debit_amount"))
        .values("t")[:1]
    )

    qs = qs.annotate(
        total_loans=Subquery(total_loans_sq),
        active_loans=Subquery(active_sq),
        completed_loans=Subquery(completed_sq),
        total_borrowed=Subquery(borrowed_sq),
        total_repaid=Subquery(repaid_sq),
        outstanding=Subquery(outstanding_sq),
        overdue_amount=Subquery(overdue_amt_sq),
    )
    qs = qs.filter(total_loans__gt=0)
    qs = qs.order_by("-total_borrowed")
    return qs


def get_performance_data(f):
    """Return aggregated performance data grouped by bank or loan type.

    Uses per-loan subqueries for payment aggregates to avoid
    cross-product duplication.
    """
    group_by = f["group_by"]

    # Per-loan payment subqueries (no cross-product)
    paid_sq = (
        Payment.objects.filter(loan=OuterRef("pk"), status="paid")
        .values("loan")
        .annotate(t=Sum("principal_component"))
        .values("t")[:1]
    )
    overdue_sq = (
        Payment.objects.filter(loan=OuterRef("pk"), status="overdue")
        .values("loan")
        .annotate(t=Sum("total_debit_amount"))
        .values("t")[:1]
    )

    qs = Loan.objects.annotate(
        _repaid=Subquery(paid_sq),
        _overdue_amount=Subquery(overdue_sq),
    )
    qs = _apply_loan_filters(qs, f)

    if group_by == "bank":
        bank_sub = (
            BankAccount.objects.filter(user=OuterRef("user"))
            .order_by("-is_default", "-created_at")
            .values("bank_name")[:1]
        )
        qs = qs.annotate(_bank=Subquery(bank_sub))
        if f["bank_name"]:
            qs = qs.filter(_bank=f["bank_name"])
        group_field = "_bank"
        group_label = "Bank"
    else:
        group_field = "loan_type"
        group_label = "Loan Type"

    rows = (
        qs.values(group_field)
        .annotate(
            total_loans=Count("id"),
            total_disbursed=Sum("amount"),
            total_repaid=Sum("_repaid"),
            outstanding=Sum("remaining_balance", filter=Q(status="active")),
            overdue=Sum("_overdue_amount"),
            avg_loan=Avg("amount"),
        )
        .order_by("-total_disbursed")
    )

    results = []
    for row in rows:
        if group_by == "bank":
            label = row[group_field] or "Unknown Bank"
        else:
            label = dict(Loan.LOAN_TYPE_CHOICES).get(row[group_field], row[group_field])
        results.append(
            {
                "label": label,
                "total_loans": row["total_loans"],
                "total_disbursed": row["total_disbursed"] or Decimal("0"),
                "total_repaid": row["total_repaid"] or Decimal("0"),
                "outstanding": row["outstanding"] or Decimal("0"),
                "overdue": row["overdue"] or Decimal("0"),
                "avg_loan": row["avg_loan"] or Decimal("0"),
            }
        )
    return results, group_label


def get_available_users():
    return User.objects.filter(is_active=True).order_by("first_name", "username")


def get_available_banks():
    return (
        BankAccount.objects.values_list("bank_name", flat=True)
        .distinct()
        .order_by("bank_name")
    )
