"""Daily, disbursement-wise interest accrual and recovery ledger."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from loans.models import LoanInterestAccrual

ZERO = Decimal("0.00")


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class AccruedInterestService:
    """Calculate daily simple interest on the actual released principal.

    The end date is exclusive. For a receipt received on 10 September, the
    ledger accrues through 9 September, matching a bank's outstanding-day
    calculation and avoiding double charging on the receipt date.
    """

    @classmethod
    def _principal_reductions_before(cls, loan, day):
        payment_reduction = (
            loan.payments.filter(
                status__in=["paid", "partial"], payment_date__lt=day
            ).aggregate(total=Sum("principal_component"))["total"]
            or ZERO
        )
        prepayment_reduction = (
            loan.prepayments.filter(
                status="paid", prepayment_date__lt=day
            ).aggregate(total=Sum("principal_component"))["total"]
            or ZERO
        )
        return Decimal(payment_reduction) + Decimal(prepayment_reduction)

    @classmethod
    def _tranche_balances(cls, loan, day):
        reductions = cls._principal_reductions_before(loan, day)
        balances = []
        for disbursement in loan.disbursements.filter(
            status="released", disbursement_date__lte=day
        ).order_by("disbursement_date", "id"):
            balance = Decimal(disbursement.amount)
            reduction = min(balance, reductions)
            balance -= reduction
            reductions -= reduction
            if balance > ZERO:
                balances.append((disbursement, money(balance)))
            if reductions <= ZERO:
                reductions = ZERO
        return balances

    @classmethod
    def _baseline_date(cls, loan):
        """Avoid charging old, already-posted loans a second time on upgrade."""
        if loan.interest_accruals.exists():
            return None
        last_payment = (
            loan.payments.filter(
                status__in=["paid", "partial"], payment_date__isnull=False
            )
            .order_by("-payment_date", "-id")
            .first()
        )
        return last_payment.payment_date if last_payment else None

    @classmethod
    def accrue_until(cls, loan, until_date):
        """Persist missing daily rows up to, but excluding, ``until_date``."""
        if loan.bank_interest_postings.exists():
            return
        if not loan.disbursements.filter(status="released").exists():
            return
        first = (
            loan.disbursements.filter(status="released")
            .order_by("disbursement_date", "id")
            .first()
            .disbursement_date
        )
        baseline = cls._baseline_date(loan)
        start = max(first, baseline + timedelta(days=1)) if baseline else first
        day = start
        while day < until_date:
            balances = cls._tranche_balances(loan, day)
            existing = set(
                loan.interest_accruals.filter(accrual_date=day).values_list(
                    "disbursement_id", flat=True
                )
            )
            rows = []
            for disbursement, balance in balances:
                if disbursement.pk in existing:
                    continue
                interest = money(
                    balance * Decimal(loan.interest_rate) / Decimal("100") / Decimal("365")
                )
                rows.append(
                    LoanInterestAccrual(
                        loan=loan,
                        disbursement=disbursement,
                        accrual_date=day,
                        principal_balance=balance,
                        disbursed_amount=disbursement.amount,
                        annual_interest_rate=loan.interest_rate,
                        interest_amount=interest,
                    )
                )
            if rows:
                LoanInterestAccrual.objects.bulk_create(rows)
            day += timedelta(days=1)

    @classmethod
    def pending_interest(cls, loan, through_date):
        statement_postings = loan.bank_interest_postings.filter(
            status__in=["pending", "part_paid"], posting_date__lt=through_date
        )
        if statement_postings.exists():
            return money(sum((p.outstanding_amount for p in statement_postings), ZERO))
        cls.accrue_until(loan, through_date)
        pending = loan.interest_accruals.filter(
            status__in=["pending", "part_paid"], accrual_date__lt=through_date
        ).aggregate(total=Sum("interest_amount"))["total"] or ZERO
        recovered = loan.interest_accruals.filter(
            status__in=["pending", "part_paid"], accrual_date__lt=through_date
        ).aggregate(total=Sum("recovered_amount"))["total"] or ZERO
        return money(
            max(ZERO, Decimal(pending) - Decimal(recovered))
        )

    @classmethod
    def pending_breakdown(cls, loan, through_date=None, emi_date=None):
        """Return one live, disbursement-wise row for each open interest tranche.

        The ledger remains daily and auditable, but the borrower-facing loan
        page should not print one row for every day.  Grouping here also keeps
        the displayed amount equal to the unrecovered amount, rather than the
        gross amount already recovered through a payment.
        """
        through_date = through_date or (timezone.localdate() + timedelta(days=1))
        statement_postings = loan.bank_interest_postings.filter(
            status__in=["pending", "part_paid"], posting_date__lt=through_date
        ).order_by("posting_date", "id")
        if statement_postings.exists():
            return [
                {
                    "label": "Bank posted interest",
                    "disbursement": None,
                    "from_date": p.period_start or p.posting_date,
                    "to_date": p.period_end or p.posting_date,
                    "days": None,
                    "interest_amount": p.outstanding_amount,
                    "emi_date": emi_date,
                }
                for p in statement_postings
                if p.outstanding_amount > ZERO
            ]
        cls.accrue_until(loan, through_date)
        entries = (
            loan.interest_accruals.filter(
                status__in=["pending", "part_paid"],
                accrual_date__lt=through_date,
            )
            .select_related("disbursement")
            .order_by("disbursement_id", "accrual_date", "id")
        )
        grouped = {}
        for entry in entries:
            outstanding = entry.outstanding_amount
            if outstanding <= ZERO:
                continue
            row = grouped.setdefault(
                entry.disbursement_id,
                {
                    "disbursement": entry.disbursement,
                    "from_date": entry.accrual_date,
                    "to_date": entry.accrual_date,
                    "days": 0,
                    "interest_amount": ZERO,
                    "emi_date": emi_date,
                },
            )
            row["from_date"] = min(row["from_date"], entry.accrual_date)
            row["to_date"] = max(row["to_date"], entry.accrual_date)
            row["days"] += 1
            row["interest_amount"] = money(
                row["interest_amount"] + outstanding
            )
        return list(grouped.values())

    @classmethod
    def recover(cls, loan, amount, payment=None, recovered_on=None, prepayment=None):
        """Apply an EMI's interest component oldest-accrual-first."""
        remaining = money(amount)
        statement_postings = loan.bank_interest_postings.filter(
            status__in=["pending", "part_paid"], posting_date__lt=recovered_on
        ).order_by("posting_date", "id")
        if statement_postings.exists():
            for posting in statement_postings:
                if remaining <= ZERO:
                    break
                applied = min(posting.outstanding_amount, remaining)
                posting.recovered_amount = money(posting.recovered_amount + applied)
                posting.status = (
                    "recovered" if posting.outstanding_amount <= ZERO else "part_paid"
                )
                posting.payment = payment
                posting.prepayment = prepayment
                posting.save(
                    update_fields=[
                        "recovered_amount", "status", "payment", "prepayment", "updated_at"
                    ]
                )
                remaining = money(remaining - applied)
            return money(amount - remaining)
        entries = loan.interest_accruals.filter(
            status__in=["pending", "part_paid"], accrual_date__lt=recovered_on
        ).order_by("accrual_date", "id")
        for entry in entries:
            if remaining <= ZERO:
                break
            open_amount = entry.outstanding_amount
            applied = min(open_amount, remaining)
            entry.recovered_amount = money(entry.recovered_amount + applied)
            entry.status = "recovered" if entry.outstanding_amount <= ZERO else "part_paid"
            entry.recovered_on = recovered_on
            entry.payment = payment
            entry.prepayment = prepayment
            entry.save(
                update_fields=[
                    "recovered_amount", "status", "recovered_on", "payment",
                    "prepayment", "updated_at"
                ]
            )
            remaining = money(remaining - applied)
        return money(amount - remaining)

    @classmethod
    def restore_recovery(cls, payment=None, prepayment=None):
        entries = (
            payment.interest_accruals.all()
            if payment is not None
            else prepayment.interest_accruals.all()
        )
        for entry in entries:
            entry.recovered_amount = ZERO
            entry.status = "pending"
            entry.recovered_on = None
            entry.payment = None
            entry.prepayment = None
            entry.save(
                update_fields=[
                    "recovered_amount", "status", "recovered_on", "payment",
                    "prepayment", "updated_at"
                ]
            )
        bank_entries = (
            payment.bank_interest_postings.all()
            if payment is not None
            else prepayment.bank_interest_postings.all()
        )
        for entry in bank_entries:
            entry.recovered_amount = ZERO
            entry.status = "pending"
            entry.payment = None
            entry.prepayment = None
            entry.save(
                update_fields=[
                    "recovered_amount", "status", "payment", "prepayment", "updated_at"
                ]
            )

    @classmethod
    def calculate_total_debit(cls, loan, emi_date=None):
        from loans.utils import generate_full_schedule

        rows = [row for row in generate_full_schedule(loan) if not row["is_paid"]]
        row = next((r for r in rows if r["due_date"] == emi_date), None) if emi_date else None
        row = row or (rows[0] if rows else None)
        accrued = cls.pending_interest(loan, emi_date) if emi_date else ZERO
        amount = max(row["total_debit"], accrued) if row else accrued
        return {
            "regular_emi": amount,
            "regular_interest": accrued or (row["interest"] if row else ZERO),
            "total_debit": amount,
            "outstanding_disbursed": loan.remaining_balance,
        }
