from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from tempfile import TemporaryDirectory
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from loans.models import Loan, LoanDisbursement, LoanDocument, Notification
from loans.accounting import schedule, refresh_balance
from payments.models import Payment, Prepayment
from payments.services import process_emi_payment, process_prepayment


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LoanRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("borrower", password="test-only-pass")
        self.other = User.objects.create_user("other", password="test-only-pass")
        self.staff = User.objects.create_user(
            "staff", password="test-only-pass", is_staff=True
        )
        self.today = timezone.localdate()
        self.loan = Loan.objects.create(
            user=self.user,
            loan_name="Home loan",
            amount=1000,
            remaining_balance=1000,
            interest_rate=0,
            tenure_years=1,
            emi=600,
            start_date=self.today - timedelta(days=70),
            first_emi_date=self.today - timedelta(days=60),
        )
        self.release = LoanDisbursement.objects.create(
            loan=self.loan, amount=1000, disbursement_date=self.loan.start_date
        )
        self.client.force_login(self.user)

    def pay(self, key="one", period=1):
        return process_emi_payment(self.loan, request_key=key, expected_period=period)

    def test_dashboard_trend_scopes_settled_dated_payments(self):
        payment = self.pay()
        Prepayment.objects.create(loan=self.loan, amount=25, prepayment_date=self.today)
        Prepayment.objects.create(loan=self.loan, amount=75, prepayment_date=self.today, status="pending")
        response = self.client.get(reverse("dashboard"))
        trend = response.context["repayment_trend"]
        self.assertEqual(len(trend), 12)
        self.assertEqual(Decimal(trend[-1]["amount"]), payment.amount + 25)
        self.client.force_login(self.other)
        self.assertEqual(sum(Decimal(row["amount"]) for row in self.client.get(reverse("dashboard")).context["repayment_trend"]), 0)
        self.client.force_login(self.user)
        Payment.objects.filter(pk=payment.pk).update(payment_date=None)
        self.assertEqual(Decimal(self.client.get(reverse("dashboard")).context["repayment_trend"][-1]["amount"]), 25)

    def test_final_payment_matches_ledger(self):
        self.pay()
        final = self.pay("two", 2)
        self.assertEqual(final.amount, Decimal("400"))
        self.assertEqual(final.total_debit_amount, final.amount)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "closed")
        self.assertEqual(
            Notification.objects.filter(title="Loan Fully Repaid").count(), 1
        )

    def test_retry_is_idempotent(self):
        self.assertEqual(self.pay().pk, self.pay().pk)
        self.assertEqual(Payment.objects.count(), 1)

    def test_stale_installment_rejected(self):
        self.pay()
        with self.assertRaises(ValueError):
            self.pay("different", 1)

    def test_no_future_installment(self):
        self.loan.first_emi_date = self.today + timedelta(days=1)
        self.loan.save()
        with self.assertRaises(ValueError):
            self.pay()

    def test_get_cannot_record_payment(self):
        self.assertEqual(
            self.client.get(reverse("pay_emi", args=[self.loan.pk])).status_code, 405
        )
        self.assertFalse(Payment.objects.exists())

    def test_signed_http_confirmation_and_retry(self):
        response = self.client.get(reverse("loan_detail", args=[self.loan.pk]))
        token = response.context["payment_confirmation"]
        url = reverse("pay_emi", args=[self.loan.pk])
        self.client.post(url, {"confirmation": token})
        self.client.post(url, {"confirmation": token})
        self.assertEqual(Payment.objects.count(), 1)

    def test_missing_confirmation_cannot_record(self):
        self.client.post(reverse("pay_emi", args=[self.loan.pk]))
        self.assertFalse(Payment.objects.exists())

    def test_unreleased_loan_has_no_debt_or_schedule(self):
        self.release.delete()
        refresh_balance(self.loan)
        self.assertEqual(self.loan.remaining_balance, 0)
        self.assertEqual(schedule(self.loan), [])

    def test_future_release_not_charged_early(self):
        self.release.disbursement_date = self.today + timedelta(days=60)
        self.release.save()
        self.assertTrue(
            all(
                r["due_date"] >= self.release.disbursement_date
                for r in schedule(self.loan)
            )
        )

    def test_frequency_totals(self):
        for frequency, ppy in [
            ("monthly", 12),
            ("quarterly", 4),
            ("half_yearly", 2),
            ("yearly", 1),
        ]:
            self.loan.emi_frequency = frequency
            self.assertEqual(self.loan.total_payable, self.loan.emi * ppy)

    def test_zero_negative_prepayment_rejected(self):
        from payments.forms import PrepaymentForm

        for amount in ["0", "-1", "1001"]:
            self.assertFalse(
                PrepaymentForm(
                    {"amount": amount, "prepayment_date": self.today}, loan=self.loan
                ).is_valid()
            )

    def test_prepayment_atomic_and_repeat_safe(self):
        self.loan.first_emi_date = self.today + timedelta(days=10)
        self.loan.save()
        p = process_prepayment(
            self.loan, Decimal("200"), self.today, request_key="prepay"
        )
        again = process_prepayment(
            self.loan, Decimal("200"), self.today, request_key="prepay"
        )
        self.assertEqual(p.pk, again.pk)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, 800)

    def test_prepayment_rolls_back_on_failure(self):
        from unittest.mock import patch

        self.loan.first_emi_date = self.today + timedelta(days=1)
        self.loan.save()
        with patch("payments.services.refresh_balance", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                process_prepayment(
                    self.loan, Decimal("200"), self.today, request_key="fail"
                )
        self.assertFalse(Prepayment.objects.exists())

    def test_reminder_command_does_not_collect(self):
        self.loan.auto_debit = True
        self.loan.save()
        call_command("process_auto_debits", stdout=StringIO())
        count = Notification.objects.count()
        self.assertGreater(count, 0)
        call_command("process_auto_debits", stdout=StringIO())
        self.assertEqual(Notification.objects.count(), count)
        self.assertFalse(Payment.objects.exists())

    def test_logout_other_devices_scoped_to_user(self):
        from django.test import Client

        own = Client()
        own.force_login(self.user)
        other = Client()
        other.force_login(self.other)
        own_key = own.session.session_key
        other_key = other.session.session_key
        self.client.post(reverse("logout_all_devices"))
        self.assertFalse(Session.objects.filter(session_key=own_key).exists())
        self.assertTrue(Session.objects.filter(session_key=other_key).exists())

    def test_documents_require_ownership(self):
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            doc = LoanDocument.objects.create(
                loan=self.loan,
                title="Agreement",
                file=SimpleUploadedFile(
                    "agreement.pdf", b"%PDF-1.4 test", content_type="application/pdf"
                ),
            )
            response = self.client.get(reverse("view_document", args=[doc.pk]))
            self.assertEqual(response.status_code, 200)
            response.close()
            self.client.force_login(self.other)
            self.assertEqual(
                self.client.get(reverse("view_document", args=[doc.pk])).status_code,
                404,
            )
            self.assertEqual(self.client.get(doc.file.url).status_code, 404)

    def test_investment_atomic_and_idempotent(self):
        from loans.models import Investment

        self.loan.is_public = True
        self.loan.save()
        profile = self.other.profile
        profile.role = "lender"
        profile.kyc_verified = True
        profile.available_funds = 500
        profile.save()
        self.client.force_login(self.other)
        token = signing.dumps(
            {"loan": self.loan.pk, "user": self.other.pk, "nonce": "fixed"},
            salt="investment",
        )
        for _ in range(2):
            self.client.post(
                reverse("invest_in_loan", args=[self.loan.pk]),
                {"amount": "100", "confirmation": token},
            )
        self.assertEqual(Investment.objects.count(), 1)
        self.loan.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(self.loan.funded_amount, 100)
        self.assertEqual(profile.available_funds, 400)

    def test_all_populated_pages(self):
        self.pay()
        routes = [
            "dashboard",
            "loan_list",
            "create_loan",
            "payment_dashboard",
            "prepayment_dashboard",
            "documents_dashboard",
            "notifications_dashboard",
            "support_dashboard",
            "create_support_ticket",
            "settings_dashboard",
            "marketplace",
            "setup_profile",
            "loan_compare",
        ]
        scoped = [
            ("loan_detail", [self.loan.pk]),
            ("edit_loan", [self.loan.pk]),
            ("loan_disbursement_list", [self.loan.pk]),
            ("loan_disbursement_detail", [self.release.pk]),
            ("edit_disbursement", [self.release.pk]),
            ("create_disbursement", [self.loan.pk]),
            ("emi_schedule", [self.loan.pk]),
            ("transaction_ledger", [self.loan.pk]),
        ]
        for user in [self.user, self.staff]:
            self.client.force_login(user)
            for name, args in [(n, []) for n in routes] + scoped:
                with self.subTest(user=user.username, route=name):
                    self.assertEqual(
                        self.client.get(reverse(name, args=args)).status_code, 200
                    )
        for name in [
            "admin_users",
            "admin_banks",
            "activity_logs_dashboard",
            "admin_reports",
        ]:
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_exports(self):
        self.pay()
        for name, args in [
            ("export_excel", [self.loan.pk]),
            ("export_payment_excel", []),
            ("download_statement", []),
            ("export_prepayment_excel", []),
            ("export_prepayment_pdf", []),
        ]:
            with self.subTest(route=name):
                self.assertEqual(
                    self.client.get(reverse(name, args=args)).status_code, 200
                )
        self.client.force_login(self.staff)
        for kind in [
            "loan_portfolio",
            "payment_collection",
            "overdue",
            "user_summary",
            "performance",
        ]:
            for fmt in ["csv", "excel", "pdf"]:
                with self.subTest(kind=kind, fmt=fmt):
                    self.assertEqual(
                        self.client.get(
                            reverse("export_admin_report", args=[kind, fmt])
                        ).status_code,
                        200,
                    )

    def test_disbursement_create_updates_balance(self):
        self.release.delete()
        self.client.post(
            reverse("create_disbursement", args=[self.loan.pk]),
            {
                "amount": "500",
                "disbursement_date": self.today,
                "purpose": "builder",
                "status": "released",
                "remarks": "",
            },
        )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, 500)

    def test_pending_disbursement_edit_cannot_over_release(self):
        pending = LoanDisbursement.objects.create(
            loan=self.loan, amount=500, disbursement_date=self.today, status="pending"
        )
        response = self.client.post(
            reverse("edit_disbursement", args=[pending.pk]),
            {
                "amount": "500",
                "disbursement_date": self.today,
                "purpose": "builder",
                "status": "released",
            },
        )
        self.assertEqual(response.status_code, 200)
        pending.refresh_from_db()
        self.assertEqual(pending.status, "pending")

    def test_disbursement_edit_after_repayment_rejected(self):
        self.pay()
        self.client.post(
            reverse("edit_disbursement", args=[self.release.pk]),
            {
                "amount": "900",
                "disbursement_date": self.release.disbursement_date,
                "purpose": "builder",
                "status": "released",
            },
        )
        self.release.refresh_from_db()
        self.assertEqual(self.release.amount, 1000)

    def test_disbursement_delete_recalculates_balance(self):
        response = self.client.post(
            reverse("delete_disbursement", args=[self.release.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, 0)

    def test_recorded_loan_cannot_be_deleted(self):
        self.pay()
        self.client.post(reverse("delete_loan", args=[self.loan.pk]))
        self.assertTrue(Loan.objects.filter(pk=self.loan.pk).exists())

    def test_anonymous_and_cross_user_mutations(self):
        self.client.force_login(self.other)
        for route, args in [
            ("pay_emi", [self.loan.pk]),
            ("make_prepayment", [self.loan.pk]),
            ("delete_note", [self.loan.pk, 1]),
            ("create_disbursement", [self.loan.pk]),
        ]:
            with self.subTest(route=route):
                self.assertEqual(
                    self.client.post(reverse(route, args=args)).status_code, 404
                )
        self.client.logout()
        for route in ["loan_disbursement_list", "create_disbursement"]:
            self.assertEqual(
                self.client.get(reverse(route, args=[self.loan.pk])).status_code, 302
            )

    def test_nonzero_balance_cannot_close(self):
        self.client.post(
            reverse("close_loan", args=[self.loan.pk]), {"closing_date": self.today}
        )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "active")

    def test_overdue_reports_match_schedule(self):
        from django.test import RequestFactory
        from loans.reports import get_report_filters, get_overdue_qs, get_reports_kpis

        filters = get_report_filters(RequestFactory().get("/reports/"))
        expected = sum(r["total_debit"] for r in schedule(self.loan) if r["is_overdue"])
        self.assertEqual(sum(p.amount for p in get_overdue_qs(filters)), expected)
        self.assertEqual(get_reports_kpis(filters)["total_overdue"], expected)

    def test_changed_confirmation_amount_rejected(self):
        with self.assertRaises(ValueError):
            process_emi_payment(
                self.loan, request_key="changed", expected_period=1, expected_amount="1"
            )
        self.assertFalse(Payment.objects.exists())

    def test_csrf_is_required(self):
        from django.test import Client

        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        self.assertEqual(
            browser.post(reverse("pay_emi", args=[self.loan.pk])).status_code, 403
        )

    def test_quarterly_interest_and_principal(self):
        self.loan.emi_frequency = "quarterly"
        self.loan.interest_rate = 12
        self.loan.emi = 300
        self.loan.save()
        payment = self.pay()
        self.assertEqual(payment.interest_component, 30)
        self.assertEqual(payment.principal_component, 270)
        self.assertEqual(payment.balance_after, 730)

    def test_staged_release_excluded_from_earlier_interest(self):
        self.release.amount = 500
        self.release.save()
        LoanDisbursement.objects.create(
            loan=self.loan,
            amount=500,
            disbursement_date=self.today - timedelta(days=15),
        )
        self.loan.interest_rate = 12
        self.loan.save()
        payment = self.pay()
        self.assertEqual(payment.interest_component, 5)
        self.assertEqual(payment.principal_component, 500)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, 500)
        self.assertEqual(self.loan.status, "active")

    def test_full_prepayment_closes_without_removed_interest_model(self):
        self.loan.first_emi_date = self.today + timedelta(days=10)
        self.loan.save()
        process_prepayment(self.loan, Decimal("1000"), self.today, request_key="full")
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "closed")
        self.assertEqual(self.loan.current_balance, 0)

    def test_pending_legacy_installment_is_settled_in_place(self):
        pending = Payment.objects.create(
            loan=self.loan,
            payment_number=1,
            amount=600,
            principal_component=600,
            interest_component=0,
            balance_after=400,
            due_date=self.loan.first_emi_date,
            status="pending",
        )
        payment = self.pay()
        self.assertEqual(payment.pk, pending.pk)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(payment.status, "paid")

    def test_financial_terms_cannot_change_after_payment(self):
        from loans.forms import LoanForm

        self.pay()
        data = {
            "loan_name": self.loan.loan_name,
            "loan_type": "home",
            "amount": "1000",
            "interest_rate": "12",
            "tenure_years": 1,
            "start_date": self.loan.start_date,
            "first_emi_date": self.loan.first_emi_date,
            "emi_frequency": "monthly",
        }
        form = LoanForm(data, instance=self.loan, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("interest_rate", form.errors)

    def test_global_document_upload_and_staff_view(self):
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            response = self.client.post(
                reverse("document_upload"),
                {
                    "loan": self.loan.pk,
                    "title": "Agreement",
                    "doc_type": "agreement",
                    "file": SimpleUploadedFile(
                        "test.pdf", b"%PDF-1.4 example", content_type="application/pdf"
                    ),
                },
            )
            self.assertEqual(response.status_code, 302)
            doc = LoanDocument.objects.get(loan=self.loan)
            self.client.force_login(self.staff)
            response = self.client.get(reverse("view_document", args=[doc.pk]))
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_support_conversation_displays_message(self):
        from loans.models import SupportTicket, SupportMessage

        ticket = SupportTicket.objects.create(
            user=self.user,
            subject="Question about schedule",
            message="Please review this installment",
            category="other",
        )
        SupportMessage.objects.create(
            ticket=ticket, user=self.user, message="Please review this installment"
        )
        response = self.client.get(reverse("support_ticket_detail", args=[ticket.pk]))
        self.assertContains(response, "Please review this installment")
        self.assertNotContains(response, "Create Support Ticket")
