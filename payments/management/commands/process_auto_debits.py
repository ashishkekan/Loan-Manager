from django.core.management.base import BaseCommand
from django.utils import timezone

from loans.accounting import schedule
from loans.models import Loan, Notification


class Command(BaseCommand):
    help = (
        "Create due-payment reminders. Never marks money collected or contacts a bank."
    )

    def handle(self, *args, **options):
        count = 0
        for loan in Loan.objects.filter(status="active", auto_debit=True):
            for row in schedule(loan):
                if not row["is_paid"] and row["due_date"] <= timezone.localdate():
                    _, created = Notification.objects.get_or_create(
                        user=loan.user,
                        loan=loan,
                        title=f"Installment {row['period']} due",
                        notification_type="payment",
                        defaults={
                            "message": f"₹{row['total_debit']:,.2f} is due for {loan.loan_name}. Record payment only after confirming receipt. No automatic debit has been made."
                        },
                    )
                    count += created
        self.stdout.write(
            f"Created {count} reminders. No payments recorded; no money debited."
        )
