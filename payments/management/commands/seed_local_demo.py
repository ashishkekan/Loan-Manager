"""Create fictional demo records only in the hardcoded isolated local database."""

import secrets
from pathlib import Path
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from loans.models import Loan, LoanDisbursement
from loans.utils import calculate_emi


class Command(BaseCommand):
    help = "Create local-only demo users and one fully released loan."

    @transaction.atomic
    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        if (
            settings.SETTINGS_MODULE != "loan_manager.local_settings"
            or database["ENGINE"] != "django.db.backends.sqlite3"
            or Path(database["NAME"]) != settings.BASE_DIR / "local.sqlite3"
        ):
            raise CommandError(
                "Demo seeding is allowed only with manage_local.py and local.sqlite3."
            )
        credentials = settings.BASE_DIR / ".local-demo-credentials.txt"
        User = get_user_model()
        if User.objects.filter(
            username__in=["localadmin", "localuser", "locallender"]
        ).exists():
            self.stdout.write(
                "Demo users already exist; passwords and records were not changed."
            )
            return
        lines = ["LOCAL DEMO ONLY — http://127.0.0.1:8011/accounts/login/"]
        users = {}
        for name in ["localadmin", "localuser", "locallender"]:
            password = secrets.token_urlsafe(18)
            user = User.objects.create_user(
                name, email=f"{name}@example.test", password=password, first_name=name
            )
            if name == "localadmin":
                user.is_staff = True
                user.is_superuser = True
                user.save(update_fields=["is_staff", "is_superuser"])
            profile = user.profile
            profile.role = "lender" if name == "locallender" else "borrower"
            if name == "locallender":
                profile.kyc_verified = True
                profile.available_funds = Decimal("25000")
            profile.save()
            users[name] = user
            lines.append(f"{name}: {password}")
        today = timezone.localdate()
        loan = Loan.objects.create(
            user=users["localuser"],
            loan_name="Local demo personal loan",
            loan_type="personal",
            amount=Decimal("120000"),
            interest_rate=Decimal("12"),
            tenure_years=1,
            emi=calculate_emi(120000, 12, 1),
            start_date=today,
            first_emi_date=today,
            remaining_balance=Decimal("120000"),
            auto_debit=False,
            is_public=True,
        )
        LoanDisbursement.objects.create(
            loan=loan, disbursement_date=today, amount=loan.amount, status="released"
        )
        credentials.touch(mode=0o600, exist_ok=False)
        credentials.write_text("\n".join(lines) + "\n")
        self.stdout.write(
            f"Demo created. Credentials are in {credentials.name} (local only)."
        )
