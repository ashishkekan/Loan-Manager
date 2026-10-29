from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.core.management import call_command
from loans.models import (
    Loan,
    LoanDisbursement,
    LoanDocument,
    Notification,
    Investment,
    SecuritySetting,
)
from loans.utils import generate_full_schedule, calculate_emi, calculate_foreclosure
from payments.models import Payment
from .models import *
from .services import (
    verify_receipt,
    approve_reversal,
    review_revision,
    review_application,
    waive_charge,
)
from .security import send_challenge, consume
from .access import loans_for
from .imports import validate_rows, commit_rows, read_rows
from .advanced import simulate_debts, provision_roles


class OperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.borrower = User.objects.create_user(
            "borrower", email="borrower@example.test", password="test-password-2026"
        )
        cls.checker = User.objects.create_superuser(
            "checker", email="checker@example.test", password="test-password-2026"
        )
        cls.maker = User.objects.create_superuser(
            "maker", email="maker@example.test", password="test-password-2026"
        )
        cls.other = User.objects.create_user(
            "other", email="other@example.test", password="test-password-2026"
        )
        cls.today = timezone.localdate()
        cls.loan = Loan.objects.create(
            user=cls.borrower,
            loan_name="Test loan",
            amount=12000,
            interest_rate=12,
            tenure_years=1,
            emi=Decimal("1066.19"),
            start_date=cls.today - timedelta(days=30),
            first_emi_date=cls.today,
            remaining_balance=0,
            auto_debit=True,
        )
        LoanDisbursement.objects.create(
            loan=cls.loan,
            amount=12000,
            disbursement_date=cls.today - timedelta(days=30),
        )
        cls.loan.refresh_from_db()

    def receipt(self, amount="1066.19", kind="emi", user=None, **extra):
        return Receipt.objects.create(
            loan=self.loan,
            submitted_by=user or self.borrower,
            amount=Decimal(amount),
            received_on=self.today,
            reference=f"REF-{Receipt.objects.count()+1}",
            kind=kind,
            **extra,
        )

    def test_disbursement_updates_balance(self):
        self.assertEqual(self.loan.remaining_balance, Decimal(12000))

    def test_quarterly_total(self):
        self.loan.emi_frequency = "quarterly"
        self.assertEqual(self.loan.total_payable, self.loan.emi * 4)
        self.assertEqual(self.loan.total_pending_accrued_interest, 0)
        self.assertFalse(self.loan.has_pending_accrued_interest)

    def test_scheduler_never_posts_payments(self):
        call_command("process_auto_debits", verbosity=0)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, Decimal(12000))
        self.assertEqual(Payment.objects.count(), 0)
        self.assertTrue(Reminder.objects.exists())

    def test_partial_interest_first_and_full_completion(self):
        a = self.receipt("50")
        verify_receipt(a.pk, self.checker)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, 12000)
        p = Payment.objects.get()
        self.assertEqual(p.status, "partial")
        self.assertEqual(p.interest_component, 50)
        schedule = generate_full_schedule(self.loan)
        self.assertEqual(schedule[0]["total_debit"], Decimal("1016.19"))
        b = self.receipt("1016.19")
        verify_receipt(b.pk, self.checker)
        p.refresh_from_db()
        self.loan.refresh_from_db()
        self.assertEqual(p.status, "paid")
        self.assertEqual(p.interest_component, 120)
        self.assertEqual(p.principal_component, Decimal("946.19"))
        self.assertEqual(self.loan.remaining_balance, Decimal("11053.81"))
        self.assertFalse(
            Notification.objects.filter(title="Loan Fully Repaid").exists()
        )

    def test_self_approval_forbidden(self):
        r = self.receipt(user=self.maker)
        with self.assertRaises(ValidationError):
            verify_receipt(r.pk, self.maker)
        self.assertEqual(Payment.objects.count(), 0)

    def test_unauthorized_verification(self):
        r = self.receipt()
        with self.assertRaises(PermissionDenied):
            verify_receipt(r.pk, self.other)

    def test_duplicate_verification_no_double_post(self):
        r = self.receipt()
        verify_receipt(r.pk, self.checker)
        with self.assertRaises(ValidationError):
            verify_receipt(r.pk, self.checker)
        self.assertEqual(Payment.objects.count(), 1)

    def test_overpayment_rolls_back(self):
        r = self.receipt("20000")
        with self.assertRaises(ValidationError):
            verify_receipt(r.pk, self.checker)
        self.assertFalse(Payment.objects.exists())
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, 12000)

    def test_rejection_does_not_change_balance(self):
        r = self.receipt()
        verify_receipt(r.pk, self.checker, False, "Unmatched reference")
        r.refresh_from_db()
        self.assertEqual(r.status, "rejected")
        self.assertFalse(Payment.objects.exists())

    def test_reversal_and_reposting(self):
        r = self.receipt()
        verify_receipt(r.pk, self.checker)
        reversal = Reversal.objects.create(
            receipt=r, requested_by=self.maker, reason="Incorrect bank reference"
        )
        approve_reversal(reversal.pk, self.checker)
        self.loan.refresh_from_db()
        r.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, 12000)
        self.assertEqual(r.status, "reversed")
        self.assertEqual(Payment.objects.get().amount, 0)
        verify_receipt(self.receipt().pk, self.checker)
        self.assertEqual(Payment.objects.get().status, "paid")

    def test_reversal_requires_latest_receipt(self):
        r = self.receipt("40")
        verify_receipt(r.pk, self.checker)
        verify_receipt(self.receipt("50").pk, self.checker)
        reversal = Reversal.objects.create(
            receipt=r, requested_by=self.maker, reason="Correction"
        )
        with self.assertRaises(ValidationError):
            approve_reversal(reversal.pk, self.checker)

    def test_prepayment_closure_and_noc(self):
        r = self.receipt("12000", kind="prepayment")
        verify_receipt(r.pk, self.checker)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "closed")
        self.assertEqual(self.loan.closed_date, self.today)
        self.client.force_login(self.borrower)
        response = self.client.get(
            reverse("operations:pdf", args=["noc", self.loan.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_noc_blocked_before_settlement(self):
        self.client.force_login(self.borrower)
        self.assertEqual(
            self.client.get(
                reverse("operations:pdf", args=["noc", self.loan.pk])
            ).status_code,
            403,
        )

    def test_receipt_pdf_owner_only(self):
        r = self.receipt()
        verify_receipt(r.pk, self.checker)
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("operations:pdf", args=["receipt", r.pk])
            ).status_code,
            404,
        )
        self.client.force_login(self.borrower)
        self.assertTrue(
            self.client.get(
                reverse("operations:pdf", args=["receipt", r.pk])
            ).content.startswith(b"%PDF")
        )

    def test_application_state_machine(self):
        app = Application.objects.create(
            applicant=self.borrower,
            loan_name="New loan",
            loan_type="personal",
            amount=5000,
            interest_rate=10,
            tenure_years=1,
            frequency="monthly",
            first_emi_date=self.today + timedelta(days=30),
            purpose="Education",
            status="submitted",
        )
        with self.assertRaises(ValidationError):
            review_application(app.pk, self.checker, "approved", "review", False, True)
        ApplicationDocument.objects.create(
            application=app, kind="identity", file="id.pdf"
        )
        ApplicationDocument.objects.create(
            application=app, kind="income", file="income.pdf"
        )
        review_application(
            app.pk, self.checker, "approved", "Documents reviewed", True, True
        )
        app.refresh_from_db()
        self.assertEqual(app.loan.remaining_balance, 0)
        with self.assertRaises(ValidationError):
            review_application(app.pk, self.checker, "approved", "again", True, True)

    def test_rate_revision_preserves_history(self):
        rev = LoanRevision.objects.create(
            loan=self.loan,
            kind="rate",
            effective_from=self.today,
            next_due=self.today,
            rate=9,
            periods=12,
            reason="Rate reduction",
            requested_by=self.maker,
        )
        review_revision(rev.pk, self.checker)
        rev.refresh_from_db()
        self.loan.refresh_from_db()
        self.assertEqual(rev.status, "applied")
        self.assertEqual(self.loan.interest_rate, 9)
        self.assertEqual(self.loan.schedule_version, 1)
        self.assertTrue(rev.schedule)
        self.assertTrue(rev.before["schedule"])

    def test_holiday_capitalizes_declared_interest(self):
        rev = LoanRevision.objects.create(
            loan=self.loan,
            kind="holiday",
            effective_from=self.today,
            next_due=self.today + timedelta(days=30),
            rate=12,
            periods=12,
            reason="Agreed holiday",
            requested_by=self.maker,
        )
        review_revision(rev.pk, self.checker)
        self.loan.refresh_from_db()
        self.assertGreater(self.loan.capitalized_interest, 0)
        self.assertEqual(
            generate_full_schedule(self.loan)[0]["due_date"],
            self.today + timedelta(days=30),
        )

    def test_partial_blocks_revision(self):
        verify_receipt(self.receipt("50").pk, self.checker)
        rev = LoanRevision.objects.create(
            loan=self.loan,
            kind="rate",
            effective_from=self.today,
            next_due=self.today,
            rate=9,
            periods=12,
            reason="Rate reduction",
            requested_by=self.maker,
        )
        with self.assertRaises(ValidationError):
            review_revision(rev.pk, self.checker)

    def test_charge_policy_and_waiver(self):
        p = ChargePolicy.objects.create(
            loan_type=self.loan.loan_type,
            kind="foreclosure",
            effective_from=self.today,
            fixed_amount=100,
            percentage=1,
            created_by=self.maker,
        )
        self.assertEqual(calculate_foreclosure(self.loan)["penalty"], 220)
        charge = Charge.objects.create(
            loan=self.loan,
            policy=p,
            amount=100,
            reason="Fee",
            created_by=self.maker,
            waiver_by=self.maker,
            waiver_reason="Approved exception",
        )
        with self.assertRaises(ValidationError):
            waive_charge(charge.pk, self.maker)
        waive_charge(charge.pk, self.checker)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "waived")

    def test_organization_and_branch_scope(self):
        org = Organization.objects.create(name="A")
        branch = Branch.objects.create(organization=org, name="One")
        other_branch = Branch.objects.create(organization=org, name="Two")
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        Membership.objects.create(user=staff, organization=org, branch=branch)
        self.loan.organization, self.loan.branch = org, other_branch
        self.loan.save()
        self.assertFalse(loans_for(staff).exists())
        self.loan.branch = branch
        self.loan.save()
        self.assertEqual(loans_for(staff).count(), 1)

    def test_import_preview_errors_and_duplicates(self):
        row = {
            "external_id": "L1",
            "loan_name": "Imported",
            "loan_type": "personal",
            "amount": "1000",
            "interest_rate": "10",
            "tenure_years": "1",
            "start_date": str(self.today),
            "first_emi_date": str(self.today),
            "emi_frequency": "monthly",
        }
        self.assertEqual(validate_rows([row], "loans", self.borrower), [])
        self.assertEqual(commit_rows([row], "loans", self.borrower), 1)
        self.assertTrue(validate_rows([row], "loans", self.borrower))
        self.assertEqual(
            Loan.objects.get(import_key=f"{self.borrower.pk}:L1").remaining_balance, 0
        )

    def test_invalid_import_all_or_nothing(self):
        rows = [
            {"reference": "A", "amount": "20", "received_on": str(self.today)},
            {"reference": "B", "amount": "-2", "received_on": str(self.today)},
        ]
        with self.assertRaises(ValidationError):
            commit_rows(rows, "bank", self.checker)
        self.assertFalse(BankEntry.objects.exists())

    def test_bank_reconciliation_exact_match(self):
        r = self.receipt()
        entry = BankEntry.objects.create(
            reference=r.reference,
            amount=r.amount,
            received_on=r.received_on,
            uploaded_by=self.checker,
        )
        self.client.force_login(self.checker)
        response = self.client.post(
            reverse("operations:reconciliation"), {"entry": entry.pk, "receipt": r.pk}
        )
        self.assertEqual(response.status_code, 302)
        r.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(r.status, "verified")
        self.assertEqual(entry.receipt_id, r.pk)

    def test_bank_mismatch_not_posted(self):
        r = self.receipt()
        entry = BankEntry.objects.create(
            reference=r.reference,
            amount=1,
            received_on=r.received_on,
            uploaded_by=self.checker,
        )
        self.client.force_login(self.checker)
        self.client.post(
            reverse("operations:reconciliation"), {"entry": entry.pk, "receipt": r.pk}
        )
        r.refresh_from_db()
        self.assertEqual(r.status, "pending")

    def test_otp_single_use_and_expiry(self):
        with patch("operations.security.secrets.randbelow", return_value=123456):
            challenge = send_challenge(self.borrower, "verify_email")
        self.assertTrue(mail.outbox)
        self.assertIsNone(consume(challenge.pk, "000000"))
        self.assertIsNotNone(consume(challenge.pk, "123456"))
        self.assertIsNone(consume(challenge.pk, "123456"))

    def test_2fa_login_requires_code(self):
        Identity.objects.create(user=self.borrower, verified_email=self.borrower.email)
        SecuritySetting.objects.create(user=self.borrower, two_factor=True)
        with patch("operations.security.secrets.randbelow", return_value=123456):
            response = self.client.post(
                reverse("login"),
                {"username": "borrower", "password": "test-password-2026"},
            )
        self.assertRedirects(response, reverse("operations:challenge"))
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.client.post(reverse("operations:challenge"), {"code": "123456"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_throttle(self):
        for _ in range(5):
            self.client.post(
                reverse("login"), {"username": "borrower", "password": "wrong"}
            )
        self.assertEqual(
            self.client.post(
                reverse("login"), {"username": "borrower", "password": "wrong"}
            ).status_code,
            429,
        )

    def test_foreign_receipt_submission_rejected(self):
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("operations:create", args=["receipts"]),
            {
                "loan": self.loan.pk,
                "amount": "5",
                "kind": "emi",
                "received_on": self.today,
                "reference": "OTHER",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Receipt.objects.exists())

    def test_lender_allocation(self):
        Investment.objects.create(loan=self.loan, lender=self.other, amount=6000)
        r = self.receipt()
        verify_receipt(r.pk, self.checker)
        a = LenderAllocation.objects.get()
        self.assertEqual(a.principal, Decimal("473.09"))
        self.assertEqual(a.interest, 60)

    def test_planner_extra_budget_improves_payoff(self):
        baseline = simulate_debts([self.loan], Decimal(0), "avalanche")
        extra = simulate_debts([self.loan], Decimal(500), "avalanche")
        self.assertLess(extra["months"], baseline["months"])
        self.assertLess(extra["interest"], baseline["interest"])

    def test_unconfigured_gateway_fails_closed(self):
        r = self.receipt()
        self.client.force_login(self.borrower)
        self.assertEqual(
            self.client.post(reverse("operations:checkout", args=[r.pk])).status_code,
            503,
        )
        self.assertFalse(Payment.objects.exists())

    def test_get_payment_does_not_mutate(self):
        self.client.force_login(self.borrower)
        self.assertEqual(
            self.client.get(reverse("pay_emi", args=[self.loan.pk])).status_code, 302
        )
        self.assertFalse(Payment.objects.exists())

    def test_audit_preserves_old_and_new(self):
        self.loan.loan_name = "Renamed"
        self.loan.save()
        event = AuditEvent.objects.filter(
            model="loans.loan", object_id=str(self.loan.pk)
        ).first()
        self.assertEqual(event.before["loan_name"], "Test loan")
        self.assertEqual(event.after["loan_name"], "Renamed")

    def test_owner_pages_render(self):
        self.client.force_login(self.borrower)
        paths = [
            "/operations/",
            "/operations/receipts/",
            "/operations/applications/new/",
            "/operations/receipts/new/",
            "/operations/documents/new/",
            "/operations/security/",
            "/operations/planner/",
            "/operations/transfer/",
            "/operations/portfolio/",
            "/operations/import/loans/",
            "/operations/deliveries/",
            "/loans/compare/",
            f"/loans/{self.loan.pk}/",
            "/payments/",
            "/dashboard/",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_admin_pages_render(self):
        self.client.force_login(self.checker)
        paths = [
            "/operations/team/",
            "/operations/audit/",
            "/operations/reconciliation/",
            "/operations/reversals/",
            "/operations/policies/new/",
            "/operations/charges/new/",
            "/operations/collections/new/",
            "/operations/revisions/new/",
            "/operations/collateral/new/",
            "/operations/parties/new/",
            "/reports/",
            "/dashboard/",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_navigation_role_visibility_and_active_report(self):
        from loan_manager.navigation import app_navigation
        from django.test import RequestFactory
        from django.urls import resolve

        request = RequestFactory().get("/reports/?report=overdue")
        request.resolver_match = resolve("/reports/")
        request.user = self.checker
        groups = app_navigation(request)["sidebar_groups"]
        reports = next(group for group in groups if group["label"] == "Reports")
        self.assertTrue(reports["active"])
        self.assertEqual([item["label"] for item in reports["items"] if item["active"]], ["Overdue loans"])
        request.user = self.borrower
        groups = app_navigation(request)["sidebar_groups"]
        self.assertNotIn("Reports", [group["label"] for group in groups])
        self.assertNotIn("Administration", [group["label"] for group in groups])
        operations = next(group for group in groups if group["label"] == "Loan Operations")
        self.assertNotIn("Product policies", [item["label"] for item in operations["items"]])
        self.assertNotIn("Bank reconciliation", [item["label"] for item in operations["items"]])

    def test_all_admin_navigation_destinations_render(self):
        self.client.force_login(self.checker)
        response = self.client.get("/operations/")
        for group in response.context["sidebar_groups"]:
            for item in group["items"]:
                with self.subTest(destination=item["url"]):
                    page = self.client.get(item["url"])
                    self.assertEqual(page.status_code, 200)
                    self.assertContains(page, 'aria-label="Main navigation"')

    def test_site_appearance_only_superuser_can_change(self):
        url = reverse("operations:appearance")
        self.assertEqual(self.client.post(url, {"color": "violet"}).status_code, 302)
        self.borrower.is_staff = True
        self.borrower.save(update_fields=["is_staff"])
        self.client.force_login(self.borrower)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"color": "violet"}).status_code, 403)
        self.assertFalse(SiteAppearance.objects.exists())
        self.client.force_login(self.checker)
        self.assertEqual(self.client.post(url, {"color": "invalid"}).status_code, 200)
        self.assertFalse(SiteAppearance.objects.exists())
        self.assertRedirects(self.client.post(url, {"color": "violet"}), url)
        setting = SiteAppearance.objects.get(pk=1)
        self.assertEqual(setting.color, "violet")
        self.assertEqual(setting.updated_by_id, self.checker.pk)
        self.client.force_login(self.borrower)
        self.assertContains(self.client.get("/operations/"), 'data-color="violet"')
        self.client.logout()
        self.assertContains(self.client.get("/"), 'data-color="violet"')
        self.assertContains(self.client.get("/accounts/login/"), 'data-color="violet"')
        self.client.force_login(self.checker)
        self.client.post(url, {"color": "green"})
        self.assertEqual(SiteAppearance.objects.count(), 1)
        self.assertContains(self.client.get("/"), 'data-theme="light"')

    def test_all_site_colors_use_light_surfaces(self):
        from .palettes import COLORS
        self.client.force_login(self.checker)
        self.assertEqual(len(COLORS), 16)
        for color in COLORS:
            with self.subTest(color=color):
                self.client.post(reverse("operations:appearance"), {"color": color})
                response = self.client.get("/")
                self.assertContains(response, 'data-theme="light"')
                self.assertContains(response, f'data-color="{color}"')
                self.assertContains(response, COLORS[color][1])
        self.assertNotContains(self.client.get(reverse("operations:appearance")), 'name="theme"')

    def test_manifest_and_worker(self):
        self.assertEqual(self.client.get("/manifest.webmanifest").status_code, 200)
        response = self.client.get("/sw.js")
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertNotIn(b"caches.open", response.content)

    def test_document_review_kyc_and_version_scope(self):
        doc = LoanDocument.objects.create(
            loan=self.loan,
            title="Identity",
            doc_type="id_proof",
            file=SimpleUploadedFile("identity.pdf", b"%PDF-1.4 test"),
        )
        self.client.force_login(self.checker)
        self.client.post(
            reverse("operations:action", args=["documents", doc.pk]),
            {"decision": "approve", "reason": "Identity matched"},
        )
        doc.refresh_from_db()
        self.assertEqual(doc.verification_status, "verified")
        self.client.post(
            reverse("operations:action", args=["documents", doc.pk]),
            {"decision": "kyc", "reason": "Reviewed identity"},
        )
        self.borrower.profile.refresh_from_db()
        self.assertTrue(self.borrower.profile.kyc_verified)
        self.assertEqual(DocumentReview.objects.count(), 1)

    def test_document_upload_rejects_executable(self):
        self.client.force_login(self.borrower)
        response = self.client.post(
            reverse("operations:create", args=["documents"]),
            {
                "loan": self.loan.pk,
                "title": "Unsafe",
                "doc_type": "other",
                "file": SimpleUploadedFile("bad.html", b"<script>alert(1)</script>"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LoanDocument.objects.exists())

    def test_unfunded_loan_is_not_paid_or_overdue(self):
        loan = Loan.objects.create(
            user=self.borrower,
            loan_name="Unfunded",
            amount=1000,
            interest_rate=0,
            tenure_years=1,
            emi=100,
            start_date=self.today - timedelta(days=10),
            remaining_balance=0,
        )
        self.assertEqual(loan.progress_percent, 0)
        self.assertFalse(loan.is_overdue)
        self.assertEqual(generate_full_schedule(loan), [])

    def test_receipt_creation_does_not_post(self):
        self.client.force_login(self.borrower)
        response = self.client.post(
            reverse("operations:create", args=["receipts"]),
            {
                "loan": self.loan.pk,
                "amount": "25",
                "kind": "emi",
                "received_on": self.today,
                "reference": "NEW",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Receipt.objects.get().status, "pending")
        self.assertFalse(Payment.objects.exists())

    def test_posted_disbursement_locked(self):
        verify_receipt(self.receipt().pk, self.checker)
        disbursement = self.loan.disbursements.get()
        disbursement.amount = 10000
        with self.assertRaises(ValidationError):
            disbursement.save()

    def test_disbursement_checker_flow(self):
        loan = Loan.objects.create(
            user=self.borrower,
            loan_name="New funding",
            amount=2000,
            interest_rate=0,
            tenure_years=1,
            emi=200,
            start_date=self.today,
            remaining_balance=0,
        )
        disb = LoanDisbursement.objects.create(
            loan=loan,
            amount=1000,
            disbursement_date=self.today,
            status="pending",
            requested_by=self.maker,
        )
        self.client.force_login(self.checker)
        response = self.client.post(
            reverse("operations:action", args=["disbursements", disb.pk]),
            {"decision": "approve", "reason": "Release verified"},
        )
        self.assertEqual(response.status_code, 302)
        loan.refresh_from_db()
        self.assertEqual(loan.remaining_balance, 1000)

    def test_staff_does_not_see_other_company_notifications(self):
        staff = get_user_model().objects.create_user("isolated", is_staff=True)
        Notification.objects.create(
            user=self.borrower,
            title="Private",
            message="Private",
            notification_type="loan",
        )
        self.client.force_login(staff)
        response = self.client.get("/dashboard/")
        self.assertNotContains(response, "Private")

    def test_password_reset_sends_link(self):
        response = self.client.post(
            reverse("password_reset"), {"email": self.borrower.email}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/password/reset/", mail.outbox[-1].body)

    def test_partial_total_in_summary(self):
        from loans.utils import get_schedule_summary

        verify_receipt(self.receipt("50").pk, self.checker)
        summary = get_schedule_summary(self.loan)
        self.assertEqual(summary["paid_amount"], 50)
        self.assertEqual(summary["interest_paid"], 50)
        self.assertEqual(summary["paid"], 0)

    def test_due_reminder_is_idempotent(self):
        from operations.reminders import queue_reminders, deliver_reminders

        queue_reminders()
        count = Reminder.objects.count()
        queue_reminders()
        self.assertEqual(Reminder.objects.count(), count)
        deliver_reminders()
        first = Notification.objects.count()
        deliver_reminders()
        self.assertEqual(Notification.objects.count(), first)

    def test_future_revision_does_not_apply_early(self):
        rev = LoanRevision.objects.create(
            loan=self.loan,
            kind="rate",
            effective_from=self.today + timedelta(days=1),
            next_due=self.today + timedelta(days=1),
            rate=9,
            periods=12,
            reason="Future rate",
            requested_by=self.maker,
        )
        review_revision(rev.pk, self.checker)
        self.loan.refresh_from_db()
        rev.refresh_from_db()
        self.assertEqual(self.loan.interest_rate, 12)
        self.assertEqual(rev.status, "approved")

    def test_csrf_on_receipt_creation(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.borrower)
        self.assertEqual(
            client.post(
                reverse("operations:create", args=["receipts"]), {}
            ).status_code,
            403,
        )

    def test_xlsx_import(self):
        from openpyxl import Workbook

        stream = BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["reference", "amount", "received_on"])
        sheet.append(["XLSX1", 100, str(self.today)])
        workbook.save(stream)
        rows = read_rows(SimpleUploadedFile("bank.xlsx", stream.getvalue()))
        self.assertEqual(validate_rows(rows, "bank", self.checker), [])

    def test_lender_payout_approval_and_bank_reference(self):
        Investment.objects.create(loan=self.loan, lender=self.other, amount=6000)
        verify_receipt(self.receipt().pk, self.checker)
        allocation = LenderAllocation.objects.get()
        self.client.force_login(self.maker)
        self.client.post(
            reverse("operations:portfolio"),
            {"allocation": allocation.pk, "action": "request"},
        )
        self.client.force_login(self.checker)
        self.client.post(
            reverse("operations:portfolio"),
            {"allocation": allocation.pk, "action": "approve"},
        )
        self.client.post(
            reverse("operations:portfolio"),
            {
                "allocation": allocation.pk,
                "action": "paid",
                "reference": "BANK-PAYOUT-1",
            },
        )
        self.assertEqual(Payout.objects.get().status, "paid")

    def test_collection_agent_cannot_approve_receipt(self):
        from django.contrib.auth.models import Group

        provision_roles()
        agent = get_user_model().objects.create_user("agent", is_staff=True)
        agent.groups.add(Group.objects.get(name="Collection Agent"))
        with self.assertRaises(PermissionDenied):
            verify_receipt(self.receipt().pk, agent)

    def test_invalid_action_is_400(self):
        self.client.force_login(self.borrower)
        self.assertEqual(
            self.client.post(
                reverse("operations:action", args=["receipts", 999]),
                {"decision": "invented"},
            ).status_code,
            400,
        )

    @override_settings(
        PAYMENT_ADAPTER="operations.tests.FakeProvider",
        PAYMENT_CHECKOUT_HOSTS=["pay.example.test"],
    )
    def test_gateway_captured_signature_and_duplicate(self):
        import json, hmac, hashlib

        r = self.receipt()
        GatewayOrder.objects.create(receipt=r, provider_order="order-test")
        event = {
            "id": "event-1",
            "order_id": "order-test",
            "currency": "INR",
            "amount_minor": 106619,
            "status": "captured",
            "payment_id": "payment-test",
        }
        raw = json.dumps(event).encode()
        sig = hmac.new(b"test-webhook-only", raw, hashlib.sha256).hexdigest()
        with override_settings(PAYMENT_SERVICE_USER_ID=self.checker.pk):
            self.assertEqual(
                self.client.post(
                    reverse("operations:webhook"),
                    raw,
                    content_type="application/json",
                    HTTP_X_SIGNATURE="bad",
                ).status_code,
                400,
            )
            self.assertEqual(
                self.client.post(
                    reverse("operations:webhook"),
                    raw,
                    content_type="application/json",
                    HTTP_X_SIGNATURE=sig,
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.post(
                    reverse("operations:webhook"),
                    raw,
                    content_type="application/json",
                    HTTP_X_SIGNATURE=sig,
                ).status_code,
                200,
            )
        r.refresh_from_db()
        self.assertEqual(r.status, "verified")
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(GatewayEvent.objects.count(), 1)

    @override_settings(PAYMENT_ADAPTER="operations.tests.FakeProvider")
    def test_gateway_wrong_amount_not_posted(self):
        import json, hmac, hashlib

        r = self.receipt()
        GatewayOrder.objects.create(receipt=r, provider_order="order-test")
        raw = json.dumps(
            {
                "id": "event-1",
                "order_id": "order-test",
                "currency": "INR",
                "amount_minor": 1,
                "status": "captured",
                "payment_id": "payment-test",
            }
        ).encode()
        sig = hmac.new(b"test-webhook-only", raw, hashlib.sha256).hexdigest()
        self.assertEqual(
            self.client.post(
                reverse("operations:webhook"),
                raw,
                content_type="application/json",
                HTTP_X_SIGNATURE=sig,
            ).status_code,
            400,
        )
        self.assertFalse(Payment.objects.exists())

    def test_onboarding_verified_funds_once(self):
        item = Onboarding.objects.create(
            user=self.other,
            kind="funds",
            amount=5000,
            reference="FUND-1",
            proof="bank.pdf",
        )
        self.client.force_login(self.checker)
        response = self.client.post(
            reverse("operations:onboarding"),
            {
                "review": item.pk,
                "decision": "approve",
                "comments": "Bank statement matched",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.other.profile.refresh_from_db()
        self.assertEqual(self.other.profile.available_funds, 5000)
        self.assertEqual(
            self.client.post(
                reverse("operations:onboarding"),
                {"review": item.pk, "decision": "approve", "comments": "again"},
            ).status_code,
            404,
        )
        self.other.profile.refresh_from_db()
        self.assertEqual(self.other.profile.available_funds, 5000)

    def test_onboarding_identity_without_borrower_loan(self):
        item = Onboarding.objects.create(
            user=self.other, kind="identity", reference="ID-1", proof="id.pdf"
        )
        self.client.force_login(self.checker)
        self.client.post(
            reverse("operations:onboarding"),
            {"review": item.pk, "decision": "approve", "comments": "Identity reviewed"},
        )
        self.other.profile.refresh_from_db()
        self.assertTrue(self.other.profile.kyc_verified)

    @override_settings(
        PAYMENT_ADAPTER="operations.tests.FakeProvider",
        PAYMENT_CHECKOUT_HOSTS=["pay.example.test"],
    )
    def test_mandate_pending_until_signed_callback(self):
        import json, hmac, hashlib

        self.client.force_login(self.borrower)
        response = self.client.post(
            reverse("operations:mandates"),
            {
                "loan": self.loan.pk,
                "maximum_amount": "2000",
                "expires_on": str(self.today + timedelta(days=365)),
                "consent": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        mandate = Mandate.objects.get()
        self.assertEqual(mandate.status, "pending")
        raw = json.dumps(
            {
                "id": "mandate-event",
                "kind": "mandate",
                "mandate_id": "mandate-test",
                "status": "active",
            }
        ).encode()
        sig = hmac.new(b"test-webhook-only", raw, hashlib.sha256).hexdigest()
        self.assertEqual(
            self.client.post(
                reverse("operations:webhook"),
                raw,
                content_type="application/json",
                HTTP_X_SIGNATURE=sig,
            ).status_code,
            200,
        )
        mandate.refresh_from_db()
        self.assertEqual(mandate.status, "active")

    @override_settings(
        PAYMENT_ADAPTER="operations.tests.FakeProvider", PAYMENT_RECURRING_ENABLED=True
    )
    def test_recurring_worker_queues_without_posting(self):
        from operations.gateway import queue_recurring_payments

        Mandate.objects.create(
            loan=self.loan,
            user=self.borrower,
            status="active",
            provider_reference="mandate-test",
            maximum_amount=2000,
            expires_on=self.today + timedelta(days=365),
        )
        queue_recurring_payments()
        queue_recurring_payments()
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(Receipt.objects.get().status, "pending")
        self.assertFalse(Payment.objects.exists())


class FakeProvider:
    def verify_webhook(self, body, headers):
        import hashlib, hmac, json

        expected = hmac.new(b"test-webhook-only", body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("X-Signature", "")):
            raise ValueError("Invalid signature")
        return json.loads(body)

    def create_order(self, **kwargs):
        return {"id": "test-order", "checkout_url": "https://pay.example.test/checkout"}

    def create_mandate(self, **kwargs):
        return {"id": "mandate-test", "consent_url": "https://pay.example.test/consent"}

    def revoke_mandate(self, **kwargs):
        return None

    def create_recurring_payment(self, **kwargs):
        return {"id": "recurring-test"}


class ChartPaletteTests(TestCase):
    def test_every_theme_has_distinct_readable_chart_colors(self):
        from .palettes import COLORS, chart_palette
        def luminance(color):
            channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
            channels = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
            return sum(c * weight for c, weight in zip(channels, [.2126, .7152, .0722]))
        for key, (_, primary) in COLORS.items():
            with self.subTest(theme=key):
                colors = chart_palette(key)["series"]
                self.assertEqual(colors[0], primary)
                self.assertEqual(len(set(colors)), 4)
                for color in colors:
                    self.assertGreaterEqual(1.05 / (luminance(color) + .05), 3)
        self.assertEqual(chart_palette("invalid"), chart_palette("green"))
