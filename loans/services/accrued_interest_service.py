from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from loans.models import LoanAccruedInterest, LoanDisbursement
from loans.utils import add_periods, get_period_details


class AccruedInterestService:
    DAYS_IN_YEAR = Decimal("365")

    @classmethod
    def calculate_interest(cls, amount, annual_rate, days):
        interest = (
            Decimal(str(amount)) * Decimal(str(annual_rate)) * Decimal(str(days))
        ) / (Decimal("100") * cls.DAYS_IN_YEAR)
        return interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def get_next_emi_date(cls, loan, reference_date):
        emi_date = loan.first_emi_date
        if reference_date < emi_date:
            return emi_date
        while emi_date <= reference_date:
            emi_date = add_periods(
                emi_date,
                1,
                loan.emi_frequency,
            )
        return emi_date

    @classmethod
    def build_interest_entry(cls, loan, disbursement, from_date, to_date, emi_date):
        days = (to_date - from_date).days + 1

        interest = cls.calculate_interest(
            disbursement.amount,
            loan.interest_rate,
            days,
        )

        return LoanAccruedInterest.objects.create(
            loan=loan,
            disbursement=disbursement,
            from_date=from_date,
            to_date=to_date,
            emi_date=emi_date,
            days=days,
            annual_interest_rate=loan.interest_rate,
            disbursed_amount=disbursement.amount,
            interest_amount=interest,
        )

    @classmethod
    @transaction.atomic
    def generate_for_disbursement(cls, disbursement):
        disbursement = (
            LoanDisbursement.objects.select_for_update()
            .select_related("loan")
            .get(pk=disbursement.pk)
        )
        if disbursement.status != "released" or disbursement.is_interest_processed:
            return []
        loan = disbursement.loan
        if disbursement.amount <= Decimal("0"):
            return []
        if loan.interest_rate <= Decimal("0"):
            return []
        next_emi = cls.get_next_emi_date(loan, disbursement.disbursement_date)
        if next_emi <= disbursement.disbursement_date:
            return []
        from_date = disbursement.disbursement_date
        to_date = next_emi - timedelta(days=1)
        exists = (
            LoanAccruedInterest.objects.select_for_update()
            .filter(
                loan=loan,
                disbursement=disbursement,
                emi_date=next_emi,
            )
            .exclude(status="cancelled")
            .exists()
        )
        if exists:
            return []
        if to_date < from_date:
            return []
        days = max((to_date - from_date).days + 1, 0)
        if days == 0:
            return []
        interest = cls.calculate_interest(disbursement.amount, loan.interest_rate, days)
        entry = cls.build_interest_entry(
            loan=loan,
            disbursement=disbursement,
            from_date=from_date,
            to_date=to_date,
            emi_date=next_emi,
        )
        disbursement.is_interest_processed = True
        disbursement.save(update_fields=["is_interest_processed"])
        return [entry]

    @classmethod
    @transaction.atomic
    def generate_for_loan(cls, loan):
        created = []
        disbursements = LoanDisbursement.objects.select_related("loan").filter(
            loan=loan,
            status="released",
            is_interest_processed=False,
        )
        for disbursement in disbursements:
            created.extend(cls.generate_for_disbursement(disbursement))
        return created

    @classmethod
    def get_pending_interest(cls, loan, emi_date):
        return LoanAccruedInterest.objects.filter(
            loan=loan, emi_date=emi_date, status="pending"
        ).order_by("id")

    @classmethod
    def get_pending_interest_amount(cls, loan, emi_date):
        total = Decimal("0.00")
        for item in cls.get_pending_interest(loan, emi_date):
            total += item.pending_amount
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def calculate_total_debit(cls, loan, emi_date):
        additional = cls.get_pending_interest_amount(loan, emi_date)
        total = (loan.emi + additional).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        _, periods_per_year = get_period_details(loan.emi_frequency)
        period_rate = (
            Decimal(str(loan.interest_rate))
            / Decimal(str(periods_per_year))
            / Decimal("100")
        )
        regular_interest = (loan.remaining_balance * period_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return {
            "regular_emi": loan.emi,
            "regular_interest": regular_interest,
            "additional_interest": additional,
            "total_debit": total,
        }

    @classmethod
    @transaction.atomic
    def mark_interest_recovered(cls, loan, emi_date, payment):
        entries = list(
            LoanAccruedInterest.objects.select_for_update().filter(
                loan=loan,
                emi_date=emi_date,
                status="pending",
            )
        )
        if not entries:
            return 0
        from django.utils import timezone

        now = timezone.now()

        for entry in entries:
            entry.recovered_amount = entry.pending_amount
            entry.status = "recovered"
            entry.recovered_on = emi_date
            entry.emi_payment = payment
            entry.updated_at = now

        LoanAccruedInterest.objects.bulk_update(
            entries,
            fields=[
                "status",
                "recovered_amount",
                "recovered_on",
                "emi_payment",
                "updated_at",
            ],
        )
        return len(entries)
