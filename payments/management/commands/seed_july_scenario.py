"""The user's July 2026 staged home-loan example; synthetic identities only."""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import BaseCommand, CommandError
from django.db import transaction
from loans.accounting import refresh_balance, schedule
from loans.models import (Loan, LoanDisbursement, LoanNote, UserProfile, BankAccount,
                          NotificationPreference, AppearancePreference, PrivacySetting,
                          SecuritySetting, SupportTicket, SupportMessage)
from payments.services import process_emi_payment


class Command(BaseCommand):
    help = "Seed only the disposable July scenario RAM database."

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE != "loan_manager.scenario_settings" or "mode=memory" not in str(settings.DATABASES["default"]["NAME"]):
            raise CommandError("Use run_loan_scenario.py: this command requires the scenario RAM database.")
        if User.objects.exists():
            raise CommandError("Scenario requires a fresh database; restart the scenario runner.")
        for username, staff, first in [("july_borrower", False, "Aarav"), ("july_admin", True, "Scenario")]:
            user = User.objects.create_user(
                username, email=username + "@example.test", password="JulyLoan!2026",
                first_name=first, last_name="Test", is_staff=staff, is_superuser=staff,
            )
            profile = user.profile
            profile.role = "borrower" if not staff else "guest"
            profile.phone = "0000000000"
            profile.annual_income = Decimal("1200000")
            profile.kyc_verified = True
            profile.save()
            UserProfile.objects.create(user=user, phone="0000000000", dob=date(1990, 1, 15),
                address="Flat 101, Sample Residency (fictional test profile)", city="Pune",
                state="Maharashtra", pincode="411001", occupation="Software Engineer (test)",
                annual_income=1200000)
            for model in [NotificationPreference, AppearancePreference, PrivacySetting, SecuritySetting]:
                model.objects.get_or_create(user=user)
            if not staff:
                borrower = user
                BankAccount.objects.create(user=user, bank_name="Test Bank (no real account)",
                    account_holder=user.get_full_name(), account_number="000000001234",
                    ifsc="TEST0000001", is_default=True)
        loan = Loan.objects.create(user=borrower, loan_name="July 2026 Home Loan · Daily Interest",
            loan_type="home", amount=2000000, interest_rate=Decimal("7.30"),
            interest_basis="actual_365", tenure_years=20, emi=Decimal("15869.00"),
            start_date=date(2026, 7, 18), first_emi_date=date(2026, 9, 10), auto_debit=True)
        for number, when, amount, purpose in [
            (1, date(2026, 7, 30), "29000", "insurance"),
            (2, date(2026, 7, 30), "135742", "builder"),
            (3, date(2026, 8, 31), "359842", "builder"),
        ]:
            LoanDisbursement.objects.create(loan=loan, disbursement_number=number,
                disbursement_date=when, amount=Decimal(amount), purpose=purpose,
                remarks="User-provided scenario; third release assumed to builder.")
        refresh_balance(loan)
        payment = process_emi_payment(loan, payment_date=date(2026, 9, 10),
            request_key="july-scenario-september", expected_period=1, expected_amount="15869")
        LoanNote.objects.create(loan=loan, content=(
            "TEST SCENARIO: actual bank EMI Rs 15,869. Daily simple interest uses Actual/365; "
            "release date included, due date excluded; installment interest rounded to the nearest rupee (half up), displayed with two decimals. September 10 payment is simulated as received. "
            "Future 10th-of-month payments are projections, not bank debits. "
            "Total released Rs 524,584; undisbursed sanction Rs 1,475,416. "
            "July 30 to Aug 31: 32 days on Rs 164,742; Aug 31 to Sep 10: 10 days on Rs 524,584."
        ))
        ticket = SupportTicket.objects.create(user=borrower, loan=loan, category="loan",
            subject="Daily interest scenario verification", message="Confirm staged-release calculation.", status="resolved")
        SupportMessage.objects.create(ticket=ticket, user=borrower, message=ticket.message)
        SupportMessage.objects.create(ticket=ticket, user=User.objects.get(username="july_admin"),
            is_staff_reply=True, message="Simulation configured with Actual/365 and EMI Rs 15,869.")
        rows = schedule(loan)
        self.stdout.write(f"Loan {loan.pk}; September interest {payment.interest_component}; principal {payment.principal_component}; balance {payment.balance_after}")
        self.stdout.write(f"October 10 projected interest {rows[1]['interest']}; principal {rows[1]['principal']}; balance {rows[1]['balance']}")
        self.stdout.write("http://127.0.0.1:8012/ | july_borrower / july_admin | password: JulyLoan!2026")
