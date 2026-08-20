"""
Daily command to process automatic EMI payments.

Run using:

python manage.py process_auto_debits
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from loans.models import Loan
from loans.utils import add_periods
from payments.services import process_emi_payment


class Command(BaseCommand):
    help = "Automatically process due EMI payments."

    def handle(self, *args, **options):
        today = timezone.now().date()

        self.stdout.write("")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Auto Debit Started : {today}")
        self.stdout.write("=" * 70)

        processed = 0
        skipped = 0
        failed = 0

        loans = Loan.objects.filter(
            status="active",
            auto_debit=True,
        ).order_by("id")

        for loan in loans:
            if loan.status == "closed":
                break
            try:
                schedule_start = loan.schedule_start_date
                if schedule_start > today:
                    skipped += 1
                    continue

                paid_count = loan.payments.filter(status="paid").count()
                next_due_date = add_periods(
                    schedule_start, paid_count, loan.emi_frequency
                )

                if next_due_date > today:
                    skipped += 1
                    continue

                while True:
                    paid_count = loan.payments.filter(status="paid").count()
                    next_due_date = add_periods(
                        loan.schedule_start_date, paid_count, loan.emi_frequency
                    )
                    if next_due_date > today:
                        break
                    payment = process_emi_payment(
                        loan, payment_mode="auto_debit", payment_type="emi"
                    )
                    if payment is None:
                        break
                    processed += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[PAID] "
                            f"{loan.loan_name} | "
                            f"EMI #{payment.payment_number} | "
                            f"₹{payment.amount}"
                        )
                    )
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"[FAILED] Loan #{loan.id} " f"({loan.loan_name}) " f"{exc}"
                    )
                )

        self.stdout.write("")
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.SUCCESS(f"Processed : {processed}"))
        self.stdout.write(self.style.WARNING(f"Skipped  : {skipped}"))
        self.stdout.write(self.style.ERROR(f"Failed   : {failed}"))
        self.stdout.write("=" * 70)
