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
    loan_qs = _apply_loan_filters(Loan.objects.all(), f)

    total_loans = loan_qs.count()
    total_disbursed = sum(
        (loan.total_disbursed_amount for loan in loan_qs), Decimal("0")
    )
    total_outstanding = sum((loan.current_balance for loan in loan_qs), Decimal("0"))

    total_overdue = sum((p.amount for p in get_overdue_qs(f)), Decimal("0"))
    return {
        "total_loans": total_loans,
        "total_disbursed": total_disbursed,
        "total_outstanding": total_outstanding,
        "total_overdue": total_overdue,
        "active_loans": loan_qs.filter(status="active").count(),
        "closed_loans": loan_qs.filter(status="closed").count(),
    }


def get_loan_portfolio_qs(f):
    return _apply_loan_filters(Loan.objects.select_related("user"), f).order_by(
        "-created_at"
    )


def get_payment_collection_qs(f):
    return _apply_payment_filters(
        Payment.objects.select_related("loan", "loan__user"), f
    ).order_by("-payment_date", "-payment_number")


def get_payment_summary(f):
    qs = _apply_payment_filters(Payment.objects.all(), f)
    today = timezone.localdate()
    return {
        "total_payments": qs.count(),
        "total_collected": qs.filter(status="paid").aggregate(t=Sum("amount"))["t"]
        or Decimal("0"),
        "pending_amount": qs.filter(status="pending").aggregate(t=Sum("amount"))["t"]
        or Decimal("0"),
        "overdue_amount": qs.filter(status="overdue").aggregate(t=Sum("amount"))["t"]
        or Decimal("0"),
        "successful_payments": qs.filter(status="paid").count(),
        "failed_payments": 0,
        "upcoming_payments": qs.filter(status="pending", due_date__gte=today).count(),
    }


def get_overdue_qs(f):
    from loans.accounting import schedule

    result = []
    loans = Loan.objects.select_related("user").filter(status="active")
    if f["user_id"] and f["user_id"].isdigit():
        loans = loans.filter(user_id=int(f["user_id"]))
    if f["loan_type"]:
        loans = loans.filter(loan_type=f["loan_type"])
    if f["status"]:
        loans = loans.filter(status=f["status"])
    for loan in loans:
        for row in schedule(loan):
            if not row["is_overdue"]:
                continue
            due = row["due_date"]
            if f["from_date_obj"] and due < f["from_date_obj"]:
                continue
            if f["to_date_obj"] and due > f["to_date_obj"]:
                continue
            days = (timezone.localdate() - due).days
            bucket = f["overdue_bucket"]
            if bucket == "1-30" and not 1 <= days <= 30:
                continue
            if bucket == "31-60" and not 31 <= days <= 60:
                continue
            if bucket == "61-90" and not 61 <= days <= 90:
                continue
            if bucket == "90+" and days <= 90:
                continue
            result.append(
                Payment(
                    loan=loan,
                    payment_number=row["period"],
                    amount=row["total_debit"],
                    total_debit_amount=row["total_debit"],
                    due_date=due,
                    status="overdue",
                )
            )
    return sorted(result, key=lambda p: p.due_date)


def get_overdue_summary(f):
    rows = get_overdue_qs(f)
    days = [(timezone.localdate() - p.due_date).days for p in rows]
    return {
        "total_overdue_loans": len({p.loan_id for p in rows}),
        "total_overdue_emis": len(rows),
        "total_overdue_amount": sum((p.amount for p in rows), Decimal("0")),
        "bucket_1_30": sum(1 <= d <= 30 for d in days),
        "bucket_31_60": sum(31 <= d <= 60 for d in days),
        "bucket_61_90": sum(61 <= d <= 90 for d in days),
        "bucket_90_plus": sum(d > 90 for d in days),
    }


def get_user_summary_qs(f):
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
        .annotate(t=Sum("amount"))
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
    from loans.accounting import schedule

    users = list(qs)
    for user in users:
        user_loans = _apply_loan_filters(Loan.objects.filter(user=user), f)
        user.outstanding = sum(
            (loan.current_balance for loan in user_loans), Decimal("0")
        )
        user.total_repaid = sum(
            (
                (
                    loan.payments.filter(status="paid").aggregate(
                        t=Sum("principal_component")
                    )["t"]
                    or Decimal("0")
                )
                + loan.total_prepayment_amount
                for loan in user_loans
            ),
            Decimal("0"),
        )
        user.overdue_amount = sum(
            (
                row["total_debit"]
                for loan in user_loans
                for row in schedule(loan)
                if row["is_overdue"]
            ),
            Decimal("0"),
        )
    return users


def get_performance_data(f):
    from loans.accounting import schedule

    groups = {}
    for loan in _apply_loan_filters(Loan.objects.select_related("user"), f):
        if f["group_by"] == "bank":
            bank = (
                BankAccount.objects.filter(user=loan.user)
                .order_by("-is_default", "-created_at")
                .first()
            )
            label = bank.bank_name if bank else "Unknown bank"
            if f["bank_name"] and f["bank_name"] != label:
                continue
        else:
            label = loan.get_loan_type_display()
        row = groups.setdefault(
            label,
            {
                "label": label,
                "total_loans": 0,
                "total_disbursed": Decimal("0"),
                "total_repaid": Decimal("0"),
                "outstanding": Decimal("0"),
                "overdue": Decimal("0"),
                "avg_loan": Decimal("0"),
                "sanction": Decimal("0"),
            },
        )
        row["total_loans"] += 1
        row["total_disbursed"] += loan.total_disbursed_amount
        row["total_repaid"] += (
            loan.payments.filter(status="paid").aggregate(t=Sum("principal_component"))[
                "t"
            ]
            or Decimal("0")
        ) + loan.total_prepayment_amount
        row["outstanding"] += loan.current_balance
        row["overdue"] += sum(
            (r["total_debit"] for r in schedule(loan) if r["is_overdue"]), Decimal("0")
        )
        row["sanction"] += loan.amount
        row["avg_loan"] = row["sanction"] / row["total_loans"]
    return sorted(groups.values(), key=lambda r: r["total_disbursed"], reverse=True), (
        "Bank" if f["group_by"] == "bank" else "Loan type"
    )


def get_available_users():
    return User.objects.filter(is_active=True).order_by("first_name", "username")


def get_available_banks():
    return (
        BankAccount.objects.values_list("bank_name", flat=True)
        .distinct()
        .order_by("bank_name")
    )
