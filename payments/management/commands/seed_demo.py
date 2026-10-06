"""Create sample accounts only in the isolated local workspace."""

from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import BaseCommand, CommandError
from django.utils import timezone
from loans.models import Loan, LoanDisbursement, LoanNote
from loans.accounting import refresh_balance
from loans.utils import calculate_emi
from payments.services import process_emi_payment


class Command(BaseCommand):
    help = "Seed local.sqlite3 with demonstration data. Never accesses deployment data."

    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE != "loan_manager.local_settings" or str(
            settings.DATABASES["default"]["NAME"]
        ) != str(settings.BASE_DIR / "local.sqlite3"):
            raise CommandError(
                "Demo seeding requires manage_local.py and local.sqlite3."
            )
        today = timezone.localdate()
        for username, staff in [("demo", False), ("demo_admin", True)]:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": "Alex" if not staff else "Morgan",
                    "last_name": "Taylor",
                    "email": username + "@example.test",
                    "is_staff": staff,
                },
            )
            if created:
                user.set_password("NexusDemo!2026")
                user.save()
            if staff:
                continue
            for name, kind, amount, rate in [
                ("Home renovation", "home", 650000, "8.5"),
                ("Vehicle finance", "car", 320000, "9.2"),
                ("Professional development", "education", 180000, "7.4"),
            ]:
                loan, created = Loan.objects.get_or_create(
                    user=user,
                    loan_name=name,
                    defaults={
                        "loan_type": kind,
                        "amount": amount,
                        "interest_rate": Decimal(rate),
                        "tenure_years": 5,
                        "emi": calculate_emi(amount, rate, 5),
                        "start_date": today - timedelta(days=100),
                        "first_emi_date": today - timedelta(days=90),
                        "auto_debit": True,
                    },
                )
                if created:
                    LoanDisbursement.objects.create(
                        loan=loan, amount=amount, disbursement_date=loan.start_date
                    )
                    refresh_balance(loan)
                    process_emi_payment(
                        loan, request_key=f"demo-{loan.pk}-1", expected_period=1
                    )
                    process_emi_payment(
                        loan, request_key=f"demo-{loan.pk}-2", expected_period=2
                    )
                    LoanNote.objects.create(
                        loan=loan,
                        content="Demonstration record. Review released funds and repayment history here.",
                    )
        self.stdout.write(
            "Demo workspace ready. Accounts: demo / demo_admin. Initial password for newly created accounts: NexusDemo!2026"
        )
