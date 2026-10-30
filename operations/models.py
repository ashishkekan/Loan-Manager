import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from loans.models import Loan

from .palettes import COLOR_CHOICES

USER = settings.AUTH_USER_MODEL
POSITIVE = [MinValueValidator(Decimal("0.01"))]


class AuditEvent(models.Model):
    actor = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL)
    model = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    action = models.CharField(max_length=32)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-id"]
        default_permissions = ("view",)


class Application(models.Model):
    STATES = [
        (v, v.title()) for v in ("draft", "submitted", "review", "approved", "rejected")
    ]
    applicant = models.ForeignKey(USER, on_delete=models.PROTECT)
    organization = models.ForeignKey(
        "Organization", null=True, blank=True, on_delete=models.PROTECT
    )
    branch = models.ForeignKey(
        "Branch", null=True, blank=True, on_delete=models.PROTECT
    )
    loan_name = models.CharField(max_length=200)
    loan_type = models.CharField(max_length=20, choices=Loan.LOAN_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(50)],
    )
    tenure_years = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(50)]
    )
    frequency = models.CharField(
        max_length=20, choices=Loan.EMI_FREQUENCY_CHOICES, default="monthly"
    )
    first_emi_date = models.DateField()
    purpose = models.TextField()
    identity_checked = models.BooleanField(default=False)
    income_checked = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=STATES, default="draft")
    remarks = models.TextField(blank=True)
    reviewer = models.ForeignKey(
        USER,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_applications",
    )
    loan = models.OneToOneField(
        "loans.Loan", null=True, blank=True, on_delete=models.PROTECT
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ("approve_application", "Approve loan applications"),
            ("approve_disbursement", "Approve disbursements"),
        ]


class Receipt(models.Model):
    STATES = [(v, v.title()) for v in ("pending", "verified", "rejected", "reversed")]
    number = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="receipts"
    )
    submitted_by = models.ForeignKey(USER, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    received_on = models.DateField()
    reference = models.CharField(max_length=120, unique=True)
    kind = models.CharField(
        max_length=20,
        choices=[
            ("emi", "EMI / part EMI"),
            ("prepayment", "Principal prepayment"),
            ("charge", "Charge payment"),
        ],
        default="emi",
    )
    proof = models.FileField(upload_to="payment_proofs/%Y/%m/", blank=True)
    status = models.CharField(max_length=16, choices=STATES, default="pending")
    reviewer = models.ForeignKey(
        USER,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="verified_receipts",
    )
    remarks = models.TextField(blank=True)
    payment = models.ForeignKey(
        "payments.Payment", null=True, blank=True, on_delete=models.PROTECT
    )
    prepayment = models.ForeignKey(
        "payments.Prepayment", null=True, blank=True, on_delete=models.PROTECT
    )
    charge = models.ForeignKey(
        "operations.Charge", null=True, blank=True, on_delete=models.PROTECT
    )
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True)

    class Meta:
        permissions = [("verify_receipt", "Verify payments and reconcile bank entries")]


class Reversal(models.Model):
    receipt = models.ForeignKey(
        Receipt, on_delete=models.PROTECT, related_name="reversal_requests"
    )
    requested_by = models.ForeignKey(USER, on_delete=models.PROTECT)
    reason = models.TextField()
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="pending",
    )
    reviewer = models.ForeignKey(
        USER, null=True, on_delete=models.PROTECT, related_name="reviewed_reversals"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [("approve_reversal", "Approve payment reversals")]


class BankEntry(models.Model):
    reference = models.CharField(max_length=120, unique=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=POSITIVE)
    received_on = models.DateField()
    uploaded_by = models.ForeignKey(USER, on_delete=models.PROTECT)
    receipt = models.OneToOneField(
        Receipt, null=True, blank=True, on_delete=models.PROTECT
    )
    created_at = models.DateTimeField(auto_now_add=True)


class ChargePolicy(models.Model):
    loan_type = models.CharField(max_length=20, choices=Loan.LOAN_TYPE_CHOICES)
    kind = models.CharField(
        max_length=20,
        choices=[
            ("processing", "Processing"),
            ("late", "Late payment"),
            ("foreclosure", "Foreclosure"),
        ],
    )
    effective_from = models.DateField()
    fixed_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    created_by = models.ForeignKey(USER, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["loan_type", "kind", "effective_from"],
                name="unique_charge_policy",
            )
        ]


class Charge(models.Model):
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="charges"
    )
    policy = models.ForeignKey(ChargePolicy, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=POSITIVE)
    reason = models.TextField()
    status = models.CharField(
        max_length=16,
        choices=[("due", "Due"), ("paid", "Paid"), ("waived", "Waived")],
        default="due",
    )
    created_by = models.ForeignKey(USER, on_delete=models.PROTECT)
    waiver_reason = models.TextField(blank=True)
    waiver_by = models.ForeignKey(
        USER, null=True, on_delete=models.PROTECT, related_name="requested_waivers"
    )
    reviewer = models.ForeignKey(
        USER, null=True, on_delete=models.PROTECT, related_name="approved_waivers"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [("approve_waiver", "Approve charge waivers")]


class LoanRevision(models.Model):
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="revisions"
    )
    kind = models.CharField(
        max_length=20,
        choices=[
            ("rate", "Rate change"),
            ("restructure", "Restructure"),
            ("holiday", "Payment holiday"),
        ],
    )
    effective_from = models.DateField()
    next_due = models.DateField(
        help_text="First due date under the new schedule. Holiday interest is capitalised at the old rate."
    )
    rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(50)],
    )
    periods = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(600)]
    )
    reason = models.TextField()
    requested_by = models.ForeignKey(USER, on_delete=models.PROTECT)
    reviewer = models.ForeignKey(
        USER, null=True, on_delete=models.PROTECT, related_name="approved_revisions"
    )
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("applied", "Applied"),
            ("rejected", "Rejected"),
        ],
        default="pending",
    )
    before = models.JSONField(default=dict)
    schedule = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [("approve_revision", "Approve loan rate and schedule changes")]


class CollectionFollowup(models.Model):
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="followups"
    )
    agent = models.ForeignKey(USER, on_delete=models.PROTECT)
    next_followup = models.DateField()
    promise_date = models.DateField(null=True, blank=True)
    promise_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True, validators=POSITIVE
    )
    notes = models.TextField()
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class DocumentReview(models.Model):
    document = models.ForeignKey(
        "loans.LoanDocument", on_delete=models.PROTECT, related_name="reviews"
    )
    reviewer = models.ForeignKey(USER, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=16, choices=[("verified", "Verified"), ("rejected", "Rejected")]
    )
    comments = models.TextField()
    expires_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class KYCReview(models.Model):
    user = models.ForeignKey(USER, on_delete=models.PROTECT)
    reviewer = models.ForeignKey(
        USER, on_delete=models.PROTECT, related_name="kyc_reviews"
    )
    document = models.ForeignKey("loans.LoanDocument", on_delete=models.PROTECT)
    approved = models.BooleanField(default=False)
    comments = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class Reminder(models.Model):
    loan = models.ForeignKey("loans.Loan", on_delete=models.PROTECT)
    key = models.CharField(max_length=180, unique=True)
    channel = models.CharField(
        max_length=8, choices=[("inapp", "In app"), ("email", "Email"), ("sms", "SMS")]
    )
    destination = models.CharField(max_length=254, blank=True)
    message = models.TextField()
    status = models.CharField(max_length=12, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True)


class Identity(models.Model):
    user = models.OneToOneField(USER, on_delete=models.CASCADE)
    verified_email = models.EmailField(blank=True)


class Challenge(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=16)
    digest = models.CharField(max_length=128)
    email = models.EmailField()
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, db_index=True)
    successful = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class LoginSession(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE)
    session_key = models.CharField(max_length=40)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked = models.BooleanField(default=False)


class Organization(models.Model):
    name = models.CharField(max_length=160, unique=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Branch(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    name = models.CharField(max_length=160)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], name="unique_org_branch"
            )
        ]

    def __str__(self):
        return f"{self.organization}: {self.name}"


class Membership(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"], name="unique_membership"
            )
        ]


class RelatedParty(models.Model):
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="related_parties"
    )
    kind = models.CharField(
        max_length=16,
        choices=[("co_borrower", "Co-borrower"), ("guarantor", "Guarantor")],
    )
    name = models.CharField(max_length=160)
    contact = models.CharField(max_length=160)
    consent_date = models.DateField()
    document = models.ForeignKey(
        "loans.LoanDocument", null=True, blank=True, on_delete=models.PROTECT
    )
    created_by = models.ForeignKey(USER, on_delete=models.PROTECT)


class Collateral(models.Model):
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="collateral"
    )
    description = models.TextField()
    valuation = models.DecimalField(
        max_digits=15, decimal_places=2, validators=POSITIVE
    )
    valued_on = models.DateField()
    document = models.ForeignKey(
        "loans.LoanDocument", null=True, blank=True, on_delete=models.PROTECT
    )
    release_requested_by = models.ForeignKey(USER, null=True, on_delete=models.PROTECT)
    released_by = models.ForeignKey(
        USER, null=True, on_delete=models.PROTECT, related_name="released_collateral"
    )
    release_reason = models.TextField(blank=True)
    released_at = models.DateTimeField(null=True)

    class Meta:
        permissions = [("release_collateral", "Approve collateral release")]


class LenderAllocation(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.PROTECT)
    investment = models.ForeignKey("loans.Investment", on_delete=models.PROTECT)
    principal = models.DecimalField(max_digits=15, decimal_places=2)
    interest = models.DecimalField(max_digits=15, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["receipt", "investment"], name="unique_lender_allocation"
            )
        ]


class Payout(models.Model):
    allocation = models.OneToOneField(LenderAllocation, on_delete=models.PROTECT)
    requested_by = models.ForeignKey(USER, on_delete=models.PROTECT)
    approved_by = models.ForeignKey(
        USER, null=True, on_delete=models.PROTECT, related_name="approved_payouts"
    )
    reference = models.CharField(max_length=120, unique=True, null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[("pending", "Pending"), ("approved", "Approved"), ("paid", "Paid")],
        default="pending",
    )
    paid_at = models.DateTimeField(null=True)

    class Meta:
        permissions = [("approve_payout", "Approve lender payouts")]


class GatewayOrder(models.Model):
    receipt = models.OneToOneField(Receipt, on_delete=models.PROTECT)
    provider_order = models.CharField(max_length=100, unique=True, null=True)
    provider_payment = models.CharField(max_length=100, unique=True, null=True)
    status = models.CharField(max_length=16, default="created")
    created_at = models.DateTimeField(auto_now_add=True)


class GatewayEvent(models.Model):
    event_id = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)


class ApplicationDocument(models.Model):
    application = models.ForeignKey(
        Application, on_delete=models.PROTECT, related_name="attachments"
    )
    kind = models.CharField(
        max_length=20,
        choices=[
            ("identity", "Identity proof"),
            ("income", "Income proof"),
            ("other", "Other"),
        ],
    )
    file = models.FileField(upload_to="application_documents/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)


class Mandate(models.Model):
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.PROTECT, related_name="mandates"
    )
    user = models.ForeignKey(USER, on_delete=models.PROTECT)
    provider_reference = models.CharField(max_length=120, unique=True, null=True)
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("active", "Active"),
            ("revoked", "Revoked"),
            ("expired", "Expired"),
        ],
        default="pending",
    )
    consented_at = models.DateTimeField(auto_now_add=True)
    maximum_amount = models.DecimalField(
        max_digits=15, decimal_places=2, validators=POSITIVE
    )
    expires_on = models.DateField()


class Onboarding(models.Model):
    user = models.ForeignKey(USER, on_delete=models.PROTECT)
    kind = models.CharField(
        max_length=16,
        choices=[
            ("identity", "Identity verification"),
            ("funds", "Lender funds deposit"),
        ],
    )
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    reference = models.CharField(max_length=120, unique=True)
    proof = models.FileField(upload_to="onboarding/%Y/%m/")
    status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("verified", "Verified"),
            ("rejected", "Rejected"),
        ],
        default="pending",
    )
    reviewer = models.ForeignKey(
        USER, null=True, on_delete=models.PROTECT, related_name="onboarding_reviews"
    )
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SiteAppearance(models.Model):
    """One site-wide appearance record, managed only by a superuser."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    color = models.CharField(max_length=16, choices=COLOR_CHOICES, default="green")
    updated_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1), name="single_site_appearance"
            )
        ]
