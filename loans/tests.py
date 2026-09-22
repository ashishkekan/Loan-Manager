from datetime import date
from decimal import Decimal
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from loans.models import Loan, LoanDisbursement, LoanDocument, Notification
from loans.forms import LoanDisbursementForm
from loans.utils import calculate_emi, generate_full_schedule
from payments.models import Payment, Prepayment
from payments.services import process_emi_payment
from payments.forms import PrepaymentForm


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LoanFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "borrower", password="test-only-password"
        )
        self.other = get_user_model().objects.create_user(
            "other", password="test-only-password"
        )
        self.staff = get_user_model().objects.create_superuser(
            "admin", "admin@example.test", "test-only-password"
        )
        self.loan = Loan.objects.create(
            user=self.user,
            loan_name="Test loan",
            amount="12000",
            interest_rate="12",
            tenure_years=1,
            emi=calculate_emi(12000, 12, 1),
            start_date=date(2026, 1, 1),
            first_emi_date=date(2026, 1, 1),
            remaining_balance="12000",
            auto_debit=False,
        )
        self.disb = LoanDisbursement.objects.create(
            loan=self.loan, amount="12000", disbursement_date=date(2026, 1, 1)
        )
        self.loan.refresh_from_db()
        self.client.force_login(self.user)

    def test_core_pages_both_roles(self):
        paths = [
            "/",
            "/dashboard/",
            "/loans/",
            "/loans/create/",
            "/loans/compare/",
            f"/loans/{self.loan.pk}/",
            f"/loan/{self.loan.pk}/edit/",
            f"/loans/{self.loan.pk}/disbursements/",
            f"/disbursement/{self.disb.pk}/",
            f"/disbursement/{self.disb.pk}/edit/",
            f"/loans/{self.loan.pk}/schedule/",
            f"/loans/{self.loan.pk}/ledger/",
            "/payments/",
            "/prepayments/",
            "/documents/",
            "/notifications/",
            "/support/",
            "/support/create/",
            "/settings/",
            "/marketplace/",
            "/profile/setup/",
        ]
        for user in [self.user, self.staff]:
            self.client.force_login(user)
            for path in paths:
                with self.subTest(user=user.username, path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
        for path in ["/reports/", "/activity-logs/", "/banks/", "/admin/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_emi_accounting_and_notification(self):
        p = process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        self.loan.refresh_from_db()
        self.assertEqual(p.amount, p.principal_component + p.interest_component)
        self.assertEqual(
            self.loan.remaining_balance, Decimal("12000") - p.principal_component
        )
        self.assertFalse(
            Notification.objects.filter(title="Loan Fully Repaid").exists()
        )
        self.assertEqual(p.total_debit_amount, p.amount)

    def test_final_payment_records_actual_debit(self):
        self.loan.emi = Decimal("20000")
        self.loan.save()
        p = process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        self.loan.refresh_from_db()
        self.assertEqual(p.amount, Decimal("12120"))
        self.assertEqual(p.total_debit_amount, p.amount)
        self.assertEqual(self.loan.status, "closed")
        self.assertEqual(
            Notification.objects.filter(title="Loan Fully Repaid").count(), 1
        )
        self.assertIsNone(process_emi_payment(self.loan, payment_date=date(2026, 1, 1)))

    def test_no_early_payment_or_get_mutation(self):
        self.assertEqual(
            self.client.get(reverse("pay_emi", args=[self.loan.pk])).status_code, 405
        )
        process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        with self.assertRaises(ValueError):
            process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        self.assertEqual(Payment.objects.count(), 1)

    def test_undisbursed_loan_not_marked_paid(self):
        self.disb.delete()
        with self.assertRaises(ValueError):
            process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        self.assertFalse(Payment.objects.exists())

    def test_partial_release_preserves_undisbursed_balance(self):
        self.disb.amount = Decimal("100")
        self.disb.save()
        p = process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        self.loan.refresh_from_db()
        self.assertEqual(p.principal_component, Decimal("100"))
        self.assertEqual(self.loan.remaining_balance, Decimal("11900"))
        self.assertEqual(self.loan.status, "active")

    def test_frequency_total(self):
        for freq, ppy in [
            ("monthly", 12),
            ("quarterly", 4),
            ("half_yearly", 2),
            ("yearly", 1),
        ]:
            self.loan.emi_frequency = freq
            self.assertEqual(
                self.loan.total_payable, self.loan.emi * self.loan.tenure_years * ppy
            )

    def test_prepayment_zero_and_dates(self):
        for amount, day in [
            ("0", "2026-01-01"),
            ("-1", "2026-01-01"),
            ("1", "2025-12-31"),
            ("12001", "2026-01-01"),
        ]:
            self.assertFalse(
                PrepaymentForm(
                    {"amount": amount, "prepayment_date": day}, loan=self.loan
                ).is_valid()
            )

    def test_prepayment_closure_and_nonclosure(self):
        self.client.post(
            reverse("make_prepayment", args=[self.loan.pk]),
            {"amount": "100", "prepayment_date": "2026-01-01"},
        )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, Decimal("11900"))
        self.assertFalse(
            Notification.objects.filter(title="Loan Fully Repaid").exists()
        )
        self.client.post(
            reverse("make_prepayment", args=[self.loan.pk]),
            {"amount": "11900", "prepayment_date": "2026-01-01"},
        )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "closed")
        self.assertEqual(self.loan.closed_date, date(2026, 1, 1))

    def test_close_unpaid_rejected(self):
        self.client.post(
            reverse("close_loan", args=[self.loan.pk]), {"closing_date": "2026-01-01"}
        )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "active")

    def test_isolation(self):
        self.client.force_login(self.other)
        for name in ["loan_detail", "edit_loan"]:
            self.assertEqual(
                self.client.get(reverse(name, args=[self.loan.pk])).status_code, 404
            )
        for name in ["pay_emi", "make_prepayment", "add_note"]:
            self.assertEqual(
                self.client.post(reverse(name, args=[self.loan.pk]), {}).status_code,
                404,
            )
        self.client.logout()
        for name in [
            "pay_emi",
            "make_prepayment",
            "add_note",
            "invest_in_loan",
            "create_disbursement",
            "loan_disbursement_list",
        ]:
            self.assertEqual(
                self.client.post(reverse(name, args=[self.loan.pk]), {}).status_code,
                302,
            )

    def test_disbursement_crud_and_limit(self):
        self.disb.delete()
        data = {
            "amount": "6000",
            "disbursement_date": "2026-01-01",
            "purpose": "builder",
            "status": "released",
        }
        self.assertEqual(
            self.client.post(
                reverse("create_disbursement", args=[self.loan.pk]), data
            ).status_code,
            302,
        )
        disb = self.loan.disbursements.get()
        data["amount"] = "7000"
        self.assertEqual(
            self.client.post(
                reverse("edit_disbursement", args=[disb.pk]), data
            ).status_code,
            302,
        )
        disb.refresh_from_db()
        self.assertEqual(disb.amount, Decimal("7000"))
        self.assertEqual(
            self.client.post(
                reverse("delete_disbursement", args=[disb.pk])
            ).status_code,
            302,
        )
        self.assertFalse(self.loan.disbursements.exists())

    def test_pending_release_cannot_exceed_sanction(self):
        pending = LoanDisbursement.objects.create(
            loan=self.loan,
            amount="5000",
            status="pending",
            disbursement_date=date(2026, 1, 1),
        )
        form = LoanDisbursementForm(
            {
                "amount": "5000",
                "disbursement_date": "2026-01-01",
                "status": "released",
                "purpose": "builder",
            },
            instance=pending,
            loan=self.loan,
        )
        self.assertFalse(form.is_valid())

    def test_edit_owner_not_transferable(self):
        data = {
            "user": self.other.pk,
            "loan_name": "Edited",
            "amount": "13000",
            "interest_rate": "12",
            "tenure_years": "1",
            "start_date": "2026-01-01",
            "first_emi_date": "2026-01-01",
            "emi_frequency": "monthly",
            "loan_type": "home",
        }
        self.assertEqual(
            self.client.post(
                reverse("edit_loan", args=[self.loan.pk]), data
            ).status_code,
            302,
        )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.user, self.user)
        self.assertEqual(self.loan.remaining_balance, Decimal("13000"))

    def test_scheduler_repeat_is_idempotent(self):
        self.loan.auto_debit = True
        self.loan.save()
        call_command("process_auto_debits", stdout=StringIO(), stderr=StringIO())
        count = Payment.objects.count()
        self.assertGreater(count, 0)
        call_command("process_auto_debits", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(Payment.objects.count(), count)

    @override_settings(MEDIA_ROOT="/tmp/loan-review-test-media")
    def test_document_upload_download_and_delete_routes(self):
        response = self.client.post(
            reverse("document_upload"),
            {
                "loan": self.loan.pk,
                "title": "Test document",
                "doc_type": "agreement",
                "file": SimpleUploadedFile(
                    "test.pdf", b"%PDF-1.4 test", content_type="application/pdf"
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        doc = LoanDocument.objects.get()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse("download_document", args=[doc.pk])).status_code,
            404,
        )
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(reverse("download_document", args=[doc.pk])).status_code,
            200,
        )
        self.loan.refresh_from_db()
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.post(
                reverse("delete_document", args=[self.loan.pk, doc.pk])
            ).status_code,
            302,
        )
        self.assertFalse(LoanDocument.objects.exists())

    def test_investment_decimal_atomic_and_validation(self):
        self.loan.is_public = True
        self.loan.save()
        profile = self.other.profile
        profile.role = "lender"
        profile.kyc_verified = True
        profile.available_funds = Decimal("1000")
        profile.save()
        self.client.force_login(self.other)
        url = reverse("invest_in_loan", args=[self.loan.pk])
        for amount in ["NaN", "bad", "0", "-10", "1001", "0.001"]:
            self.assertEqual(self.client.post(url, {"amount": amount}).status_code, 302)
            self.assertFalse(self.loan.investments.exists())
        self.assertEqual(self.client.post(url, {"amount": "123.45"}).status_code, 302)
        self.loan.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(self.loan.funded_amount, Decimal("123.45"))
        self.assertEqual(profile.available_funds, Decimal("876.55"))

    def test_reports_exports_and_overdue_without_payment_rows(self):
        self.client.force_login(self.staff)
        for report in [
            "loan_portfolio",
            "payment_collection",
            "overdue",
            "user_summary",
            "performance",
        ]:
            with self.subTest(report=report):
                self.assertEqual(
                    self.client.get("/reports/", {"report": report}).status_code, 200
                )
                for fmt in ["csv", "excel", "pdf"]:
                    response = self.client.get(
                        reverse("export_admin_report", args=[report, fmt])
                    )
                    self.assertEqual(response.status_code, 200, (report, fmt))
                    self.assertIn("attachment", response["Content-Disposition"])
        from loans.reports import get_report_filters, get_overdue_qs, get_reports_kpis
        from django.test import RequestFactory

        filters = get_report_filters(RequestFactory().get("/reports/"))
        self.assertFalse(Payment.objects.exists())
        self.assertGreater(len(get_overdue_qs(filters)), 0)
        self.assertGreater(get_reports_kpis(filters)["total_overdue"], 0)

    def test_logout_other_devices_does_not_log_out_other_users(self):
        from django.test import Client
        from django.contrib.sessions.models import Session

        own_other = Client()
        own_other.force_login(self.user)
        unrelated = Client()
        unrelated.force_login(self.other)
        self.client.post(reverse("logout_all_devices"))
        self.assertFalse(
            Session.objects.filter(session_key=own_other.session.session_key).exists()
        )
        self.assertTrue(
            Session.objects.filter(session_key=unrelated.session.session_key).exists()
        )
        self.assertTrue(
            Session.objects.filter(session_key=self.client.session.session_key).exists()
        )

    def test_staff_can_reply_to_user_ticket(self):
        from loans.models import SupportTicket

        ticket = SupportTicket.objects.create(
            user=self.user,
            loan=self.loan,
            subject="Help",
            message="Test question",
            category="other",
        )
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("support_ticket_detail", args=[ticket.pk])
            ).status_code,
            404,
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("support_ticket_detail", args=[ticket.pk]),
            {"message": "Staff response"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ticket.messages.get().is_staff_reply)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.last_response_at)

    def test_projections_reconcile_actual_payments(self):
        payment = process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        self.loan.refresh_from_db()
        schedule = generate_full_schedule(self.loan)
        next_row = next(row for row in schedule if row["status"] != "paid")
        self.assertEqual(next_row["period"], 2)
        self.assertEqual(
            next_row["interest"],
            (self.loan.remaining_balance * Decimal("0.01")).quantize(Decimal("0.01")),
        )
        self.loan.emi = Decimal("20000")
        self.loan.save()
        process_emi_payment(self.loan, payment_date=date(2026, 2, 1))
        self.loan.refresh_from_db()
        self.assertTrue(
            all(row["status"] == "paid" for row in generate_full_schedule(self.loan))
        )

    def test_future_release_not_charged_in_old_emi(self):
        self.disb.disbursement_date = date(2026, 2, 1)
        self.disb.save()
        with self.assertRaises(ValueError):
            process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        p = process_emi_payment(self.loan, payment_date=date(2026, 2, 1))
        self.assertEqual(p.due_date, date(2026, 2, 1))
        self.assertEqual(p.payment_number, 2)

    def test_repayment_history_prevents_term_and_disbursement_rewrite(self):
        process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        data = {
            "amount": "100",
            "disbursement_date": "2026-01-01",
            "purpose": "builder",
            "status": "released",
        }
        self.assertEqual(
            self.client.post(
                reverse("edit_disbursement", args=[self.disb.pk]), data
            ).status_code,
            200,
        )
        self.disb.refresh_from_db()
        self.assertEqual(self.disb.amount, Decimal("12000"))
        self.client.post(reverse("delete_disbursement", args=[self.disb.pk]))
        self.assertTrue(LoanDisbursement.objects.filter(pk=self.disb.pk).exists())

    def test_pending_record_is_updated_without_skipping_emi(self):
        Payment.objects.create(
            loan=self.loan,
            payment_number=1,
            amount="1120",
            principal_component="1000",
            interest_component="120",
            balance_after="11000",
            due_date=date(2026, 1, 1),
            status="pending",
        )
        p = process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        self.assertEqual(p.payment_number, 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Payment.objects.get().status, "paid")

    def test_duplicate_manual_post_cannot_pay_next_overdue_emi(self):
        url = reverse("pay_emi", args=[self.loan.pk])
        self.client.post(url, {"payment_number": "1"})
        self.client.post(url, {"payment_number": "1"})
        self.assertEqual(Payment.objects.count(), 1)

    def test_csrf_protects_payment(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(
            client.post(
                reverse("pay_emi", args=[self.loan.pk]), {"payment_number": "1"}
            ).status_code,
            403,
        )
        self.assertFalse(Payment.objects.exists())

    def test_dashboard_does_not_double_count_prepayment(self):
        self.client.post(
            reverse("make_prepayment", args=[self.loan.pk]),
            {"amount": "100", "prepayment_date": "2026-01-01"},
        )
        response = self.client.get("/dashboard/")
        self.assertEqual(response.context["total_paid"], Decimal("100"))

    def test_anonymous_admin_users_redirects(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin_users")).status_code, 302)

    def test_registration_and_loan_creation_flow(self):
        self.client.logout()
        response = self.client.post(
            reverse("register"),
            {
                "username": "newborrower",
                "first_name": "New",
                "last_name": "Borrower",
                "email": "new@example.test",
                "password1": "A-good-demo-pass-728!",
                "password2": "A-good-demo-pass-728!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="newborrower")
        self.assertEqual(user.profile.role, "guest")
        data = {
            "loan_name": "New loan",
            "loan_type": "personal",
            "amount": "24000",
            "interest_rate": "0",
            "tenure_years": "1",
            "start_date": "2026-01-01",
            "first_emi_date": "2026-02-01",
            "emi_frequency": "quarterly",
        }
        response = self.client.post(reverse("create_loan"), data)
        self.assertEqual(response.status_code, 302)
        loan = Loan.objects.get(user=user)
        self.assertEqual(loan.emi, Decimal("6000"))
        self.assertEqual(loan.remaining_balance, Decimal("24000"))

    def test_loan_terms_locked_after_payment(self):
        process_emi_payment(self.loan, payment_date=date(2026, 1, 1))
        data = {
            "loan_name": "Renamed",
            "amount": "13000",
            "interest_rate": "12",
            "tenure_years": "1",
            "start_date": "2026-01-01",
            "first_emi_date": "2026-01-01",
            "emi_frequency": "monthly",
            "loan_type": "home",
        }
        self.assertEqual(
            self.client.post(
                reverse("edit_loan", args=[self.loan.pk]), data
            ).status_code,
            200,
        )
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.amount, Decimal("12000"))

    def test_notification_redirect_stays_on_site(self):
        notification = Notification.objects.create(
            user=self.user, title="Test", message="Test"
        )
        response = self.client.post(
            reverse("mark_notification_read", args=[notification.pk]),
            {"next": "https://untrusted.example/"},
        )
        self.assertNotIn("untrusted.example", response["Location"])

    def test_cross_user_settings_cannot_be_changed(self):
        response = self.client.post(
            reverse("update_profile_user", args=[self.other.pk]),
            {"first_name": "Changed"},
        )
        self.assertEqual(response.status_code, 302)
        self.other.refresh_from_db()
        self.assertNotEqual(self.other.first_name, "Changed")

    def test_private_media_has_no_public_url(self):
        self.client.logout()
        self.assertEqual(
            self.client.get("/media/loan_documents/2026/01/test.pdf").status_code, 404
        )

    def test_frequency_schedule_and_zero_interest(self):
        for frequency, periods in [
            ("monthly", 12),
            ("quarterly", 4),
            ("half_yearly", 2),
            ("yearly", 1),
        ]:
            self.loan.emi_frequency = frequency
            self.loan.interest_rate = Decimal("0")
            self.loan.emi = calculate_emi(self.loan.amount, 0, 1, frequency)
            rows = generate_full_schedule(self.loan)
            self.assertEqual(len(rows), periods)
            self.assertEqual(sum(row["principal"] for row in rows), Decimal("12000"))
            self.assertEqual(sum(row["interest"] for row in rows), Decimal("0"))
