"""Loan model — core entity with health scoring, types, and notes."""

import os
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from loans.utils import (
    add_periods,
    calculate_remaining_periods,
    get_period_details,
)


class Loan(models.Model):
    """Represents a single loan taken by a user."""

    STATUS_CHOICES = [
        ("active", "Active"),
        ("closed", "Closed"),
        ("default", "Default"),
    ]
    LOAN_TYPE_CHOICES = [
        ("home", "Home Loan"),
        ("car", "Car Loan"),
        ("education", "Education Loan"),
        ("personal", "Personal Loan"),
        ("business", "Business Loan"),
        ("gold", "Gold Loan"),
        ("other", "Other"),
    ]

    EMI_FREQUENCY_CHOICES = [
        ("monthly", "Monthly"),
        ("quarterly", "Quarterly"),
        ("half_yearly", "Half Yearly"),
        ("yearly", "Yearly"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loans"
    )
    loan_name = models.CharField(max_length=200)
    loan_type = models.CharField(
        max_length=20, choices=LOAN_TYPE_CHOICES, default="home"
    )
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2)
    tenure_years = models.PositiveIntegerField()
    emi = models.DecimalField(max_digits=15, decimal_places=2)
    start_date = models.DateField()
    first_emi_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date on which first EMI will be deducted.",
    )

    emi_frequency = models.CharField(
        max_length=20,
        choices=EMI_FREQUENCY_CHOICES,
        default="monthly",
    )
    auto_debit = models.BooleanField(
        default=True,
        help_text="Automatically deduct EMI on due date.",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    closed_date = models.DateField(null=True, blank=True)
    remaining_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_interest_paid = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )

    # Marketplace fields
    is_public = models.BooleanField(
        default=False, help_text="Make visible to lenders for investment"
    )
    funded_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Loan"
        verbose_name_plural = "Loans"

    def __str__(self):
        return f"{self.loan_name} — {self.user.get_full_name()}"

    def save(self, *args, **kwargs):
        if not self.first_emi_date:
            self.first_emi_date = self.start_date
        super().save(*args, **kwargs)

    @property
    def total_payable(self):
        return self.emi * self.tenure_years * 12

    @property
    def total_interest_projected(self):
        return self.total_payable - self.amount

    @property
    def total_paid_emis(self):
        return self.payments.filter(status="paid").count()

    @property
    def total_prepayment_amount(self):
        return self.prepayments.aggregate(total=Sum("amount"))["total"] or 0

    @property
    def progress_percent(self):
        if self.amount <= 0:
            return 0
        paid = float(self.amount - self.remaining_balance)
        return round(min(paid / float(self.amount) * 100, 100), 1)

    @property
    def months_elapsed(self):
        return self.payments.filter(status="paid").count()

    @property
    def months_remaining(self):
        return calculate_remaining_periods(
            self.remaining_balance,
            self.interest_rate,
            self.emi,
            self.emi_frequency,
        )

    @property
    def is_overdue(self):
        if self.status != "active":
            return False
        next_num = self.months_elapsed + 1
        next_due = add_periods(
            self.schedule_start_date,
            next_num - 1,
            self.emi_frequency,
        )
        return next_due < timezone.now().date()

    @property
    def overdue_days(self):
        if not self.is_overdue:
            return 0
        next_num = self.months_elapsed + 1
        next_due = add_periods(
            self.schedule_start_date,
            next_num - 1,
            self.emi_frequency,
        )
        return (timezone.now().date() - next_due).days

    @property
    def health_score(self):
        score = 100
        if self.is_overdue:
            score -= min(self.overdue_days, 30)
            if self.overdue_days > 30:
                score -= 20
        rate = float(self.interest_rate)
        if rate > 15:
            score -= 10
        elif rate > 12:
            score -= 5
        prepay_count = self.prepayments.count()
        if prepay_count > 0:
            score = min(score + 5, 100)
        expected_balance = self._expected_balance_at_month(self.months_elapsed)
        if expected_balance and self.remaining_balance < expected_balance:
            score = min(score + 5, 100)
        return max(0, min(score, 100))

    @property
    def health_label(self):
        s = self.health_score
        if s >= 80:
            return ("Excellent", "success")
        if s >= 60:
            return ("Good", "primary")
        if s >= 40:
            return ("Fair", "warn")
        return ("Poor", "danger")

    def _expected_balance_at_month(self, month):
        _, periods_per_year = get_period_details(self.emi_frequency)
        R = (
            Decimal(str(self.interest_rate))
            / Decimal(str(periods_per_year))
            / Decimal("100")
        )
        balance = Decimal(str(self.amount))
        for _ in range(month):
            interest = balance * R
            principal = Decimal(str(self.emi)) - interest
            balance -= principal
            if balance <= 0:
                return Decimal("0")
        return balance

    @property
    def type_icon(self):
        icons = {
            "home": "fa-house",
            "car": "fa-car",
            "education": "fa-graduation-cap",
            "personal": "fa-user",
            "business": "fa-briefcase",
            "gold": "fa-coins",
            "other": "fa-file-invoice",
        }
        return icons.get(self.loan_type, "fa-file-invoice")

    @property
    def type_color(self):
        colors = {
            "home": "#28a745",
            "car": "#3b82f6",
            "education": "#8b5cf6",
            "personal": "#f59e0b",
            "business": "#ef4444",
            "gold": "#f59e0b",
            "other": "#64748b",
        }
        return colors.get(self.loan_type, "#64748b")

    @property
    def schedule_start_date(self):
        """
        Backward compatibility.

        Old loans don't have first_emi_date.

        Therefore existing calculations will still work.
        """
        return self.first_emi_date or self.start_date

    @property
    def goal_tracker(self):
        """
        Loan Goal Tracker
        """
        current_balance = Decimal(str(self.remaining_balance))

        if current_balance <= 0:
            return {
                "target": Decimal("0.00"),
                "progress": 100,
                "remaining": Decimal("0.00"),
                "achieved": True,
            }

        target = (current_balance / Decimal("100000")).to_integral_value(
            rounding=ROUND_HALF_UP
        ) * Decimal("100000")

        if target >= current_balance:
            target -= Decimal("100000")

        target = max(target, Decimal("0"))

        paid_towards_goal = current_balance - target

        if current_balance > 0:
            progress = round(
                float((paid_towards_goal / current_balance) * 100),
                1,
            )
        else:
            progress = 100

        return {
            "target": target,
            "progress": max(0, min(progress, 100)),
            "remaining": paid_towards_goal,
            "achieved": current_balance <= target,
        }

    @property
    def total_disbursed_amount(self):
        from django.db.models import Sum

        return self.disbursements.filter(status="released").aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.00")

    @property
    def remaining_sanction_amount(self):
        return self.amount - self.total_disbursed_amount

    @property
    def total_pending_accrued_interest(self):
        from django.db.models import Sum

        return self.accrued_interests.filter(status="pending").aggregate(
            total=Sum("interest_amount")
        )["total"] or Decimal("0.00")

    @property
    def total_recovered_accrued_interest(self):
        from django.db.models import Sum

        return self.accrued_interests.filter(status="recovered").aggregate(
            total=Sum("interest_amount")
        )["total"] or Decimal("0.00")

    @property
    def disbursement_percentage(self):
        if self.amount <= 0:
            return Decimal("0")
        return ((self.total_disbursed_amount / self.amount) * Decimal("100")).quantize(
            Decimal("0.01")
        )

    @property
    def total_disbursement_count(self):
        return self.disbursements.filter(status="released").count()

    @property
    def pending_accrued_interest_count(self):
        return self.accrued_interests.filter(status="pending").count()

    @property
    def recovered_accrued_interest_count(self):
        return self.accrued_interests.filter(status="recovered").count()

    @property
    def has_pending_accrued_interest(self):
        return self.accrued_interests.filter(status="pending").exists()

    @classmethod
    def has_pending_interest(cls, loan, emi_date):
        return LoanAccruedInterest.objects.filter(
            loan=loan, emi_date=emi_date, status="pending"
        ).exists()

    @classmethod
    def get_recovered_interest(cls, loan):
        return LoanAccruedInterest.objects.filter(
            loan=loan, status="recovered"
        ).aggregate(total=Sum("recovered_amount"))["total"] or Decimal("0.00")


class LoanNote(models.Model):
    """User can add notes/memos to their loans."""

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="notes")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note on {self.loan.loan_name}"


class LoanDocument(models.Model):
    """Uploaded documents for a loan."""

    DOC_TYPES = [
        ("agreement", "Loan Agreement"),
        ("sanction_letter", "Sanction Letter"),
        ("insurance", "Insurance"),
        ("property_papers", "Property Papers"),
        ("id_proof", "ID Proof"),
        ("income_proof", "Income Proof"),
        ("other", "Other"),
    ]
    VERIFICATION_STATUS = [
        ("pending", "Pending"),
        ("verified", "Verified"),
        ("rejected", "Rejected"),
    ]
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=200)
    doc_type = models.CharField(max_length=30, choices=DOC_TYPES, default="other")
    file = models.FileField(upload_to="loan_documents/%Y/%m/")
    file_size = models.PositiveIntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    verification_status = models.CharField(
        max_length=20, choices=VERIFICATION_STATUS, default="pending"
    )
    notes = models.TextField(blank=True)

    @property
    def file_extension(self):
        return os.path.splitext(self.file.name)[1].replace(".", "").upper()

    @property
    def icon(self):
        icons = {
            "PDF": "fa-file-pdf",
            "DOC": "fa-file-word",
            "DOCX": "fa-file-word",
            "JPG": "fa-file-image",
            "JPEG": "fa-file-image",
            "PNG": "fa-file-image",
        }
        return icons.get(self.file_extension, "fa-file")

    @property
    def icon_class(self):
        extension = self.file_extension.lower()
        if extension == "pdf":
            return "document-icon-pdf"
        if extension in ["doc", "docx"]:
            return "document-icon-word"
        if extension in ["jpg", "jpeg", "png"]:
            return "document-icon-image"
        return "document-icon-other"

    @property
    def formatted_size(self):
        if not self.file_size:
            return "Unknown"
        if self.file_size < 1024:
            return f"{self.file_size} B"
        if self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{self.file_size / (1024 * 1024):.1f} MB"

    def save(self, *args, **kwargs):
        if self.file:
            self.file_size = self.file.size
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} — {self.loan.loan_name}"


class Investment(models.Model):
    """Records when a lender invests in a borrower's loan."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("active", "Active"),
        ("returned", "Returned"),
    ]
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="investments")
    lender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="investments"
    )
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"₹{self.amount} by {self.lender.get_full_name()} in {self.loan.loan_name}"
        )


class LoanDisbursement(models.Model):
    PURPOSE_CHOICES = [
        ("builder", "Builder Payment"),
        ("insurance", "Insurance"),
        ("processing_fee", "Processing Fee"),
        ("legal", "Legal Charges"),
        ("technical", "Technical Charges"),
        ("registration", "Registration"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("released", "Released"),
        ("cancelled", "Cancelled"),
    ]
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.CASCADE, related_name="disbursements"
    )
    disbursement_number = models.PositiveIntegerField()
    disbursement_date = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    purpose = models.CharField(
        max_length=50, choices=PURPOSE_CHOICES, default="builder"
    )
    remarks = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="released")
    is_interest_processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "loan_disbursements"
        ordering = ["disbursement_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["loan", "disbursement_number"],
                name="unique_loan_disbursement_number",
            )
        ]
        indexes = [
            models.Index(fields=["loan"]),
            models.Index(fields=["status"]),
            models.Index(fields=["disbursement_date"]),
            models.Index(fields=["loan", "status"]),
            models.Index(fields=["loan", "disbursement_date"]),
        ]

    def save(self, *args, **kwargs):
        if not self.disbursement_number:
            last = (
                LoanDisbursement.objects.filter(loan=self.loan)
                .order_by("-disbursement_number")
                .first()
            )
            self.disbursement_number = 1 if not last else last.disbursement_number + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.loan.loan_name} - Disbursement #{self.disbursement_number}"

    @property
    def is_released(self):
        return self.status == "released"


class LoanAccruedInterest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("recovered", "Recovered"),
        ("cancelled", "Cancelled"),
    ]
    loan = models.ForeignKey(
        "loans.Loan", on_delete=models.CASCADE, related_name="accrued_interests"
    )
    disbursement = models.ForeignKey(
        "loans.LoanDisbursement",
        on_delete=models.CASCADE,
        related_name="interest_entries",
    )
    emi_payment = models.ForeignKey(
        "payments.Payment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accrued_interest_entries",
    )
    from_date = models.DateField()
    to_date = models.DateField()
    emi_date = models.DateField()
    days = models.PositiveIntegerField()
    annual_interest_rate = models.DecimalField(max_digits=7, decimal_places=4)
    disbursed_amount = models.DecimalField(max_digits=15, decimal_places=2)
    interest_amount = models.DecimalField(max_digits=15, decimal_places=2)
    recovered_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    recovered_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "loan_accrued_interest"
        ordering = ["emi_date", "id"]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "loan",
                    "disbursement",
                    "from_date",
                    "to_date",
                    "emi_date",
                ],
                name="unique_accrued_interest_cycle",
            ),
            models.CheckConstraint(
                check=models.Q(interest_amount__gte=0),
                name="loan_interest_positive",
            ),
            models.CheckConstraint(
                check=models.Q(days__gte=0),
                name="loan_interest_days_positive",
            ),
        ]

        indexes = [
            models.Index(fields=["loan"]),
            models.Index(fields=["status"]),
            models.Index(fields=["emi_date"]),
            models.Index(fields=["disbursement"]),
            models.Index(fields=["loan", "status"]),
            models.Index(fields=["loan", "emi_date"]),
            models.Index(fields=["loan", "disbursement"]),
            models.Index(fields=["loan", "emi_date", "status"]),  # ✅ yaha hona chahiye
        ]

    def __str__(self):
        return f"{self.loan.loan_name} - ₹{self.interest_amount}"

    @property
    def pending_amount(self):
        return self.interest_amount - self.recovered_amount

    @property
    def is_pending(self):
        return self.status == "pending"

    @property
    def is_recovered(self):
        return self.status == "recovered"


class Notification(models.Model):
    TYPE_CHOICES = [
        ("loan", "Loan"),
        ("payment", "Payment"),
        ("document", "Document"),
        ("reminder", "Reminder"),
        ("system", "System"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    loan = models.ForeignKey(
        "loans.Loan",
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="system",
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read"]),
            models.Index(fields=["user", "notification_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.title} - {self.user}"


class SupportTicket(models.Model):
    CATEGORY_CHOICES = [
        ("loan", "Loan Issue"),
        ("payment", "Payment Issue"),
        ("document", "Document Issue"),
        ("account", "Account Issue"),
        ("technical", "Technical Issue"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]
    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_tickets",
    )
    loan = models.ForeignKey(
        "Loan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    ticket_number = models.CharField(max_length=30, unique=True, editable=False)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    attachment = models.FileField(
        upload_to="support_tickets/%Y/%m/",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default="medium",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    last_response_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["ticket_number"]),
        ]

    def save(self, *args, **kwargs):
        if not self.ticket_number:
            super().save(*args, **kwargs)
            self.ticket_number = f"TKT-{self.pk:04d}"
            super().save(update_fields=["ticket_number"])
            return
        super().save(*args, **kwargs)

    def __str__(self):
        return f"#{self.ticket_number} - {self.subject}"


class SupportMessage(models.Model):
    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_messages",
    )
    message = models.TextField()
    attachment = models.FileField(
        upload_to="support_messages/%Y/%m/",
        null=True,
        blank=True,
    )
    is_staff_reply = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.ticket.ticket_number} - {self.user}"


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_profile",
    )
    phone = models.CharField(max_length=20, blank=True)
    photo = models.ImageField(upload_to="profile_photos/", blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    occupation = models.CharField(max_length=150, blank=True)
    annual_income = models.DecimalField(
        max_digits=15, decimal_places=2, blank=True, null=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} Profile"


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    email_emi = models.BooleanField(default=True)
    email_payment = models.BooleanField(default=True)
    email_updates = models.BooleanField(default=True)
    email_promotional = models.BooleanField(default=False)
    inapp_emi = models.BooleanField(default=True)
    inapp_support = models.BooleanField(default=True)
    inapp_documents = models.BooleanField(default=True)
    inapp_loan_updates = models.BooleanField(default=True)
    sms_emi = models.BooleanField(default=True)
    sms_otp = models.BooleanField(default=True)
    sms_payment = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} Notification Preferences"


class AppearancePreference(models.Model):
    THEME_CHOICES = [
        ("light", "Light"),
        ("dark", "Dark"),
        ("system", "System"),
    ]
    LANGUAGE_CHOICES = [
        ("en", "English"),
        ("hi", "Hindi"),
    ]
    DATE_FORMAT_CHOICES = [
        ("DD MMM YYYY", "07 Aug 2026"),
        ("DD/MM/YYYY", "07/08/2026"),
        ("MM/DD/YYYY", "08/07/2026"),
    ]
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="appearance_preferences",
    )
    theme = models.CharField(max_length=20, choices=THEME_CHOICES, default="system")
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default="en")
    currency = models.CharField(max_length=10, default="INR")
    date_format = models.CharField(
        max_length=30,
        choices=DATE_FORMAT_CHOICES,
        default="DD MMM YYYY",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} Appearance Preferences"


class PrivacySetting(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="privacy_settings",
    )
    hide_balance = models.BooleanField(default=False)
    hide_loan_amount = models.BooleanField(default=False)
    hide_emi_values = models.BooleanField(default=False)
    analytics = models.BooleanField(default=True)
    marketing = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} Privacy Settings"


class SecuritySetting(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="security_settings",
    )
    two_factor = models.BooleanField(default=False)
    last_password_change = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} Security Settings"


class BankAccount(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bank_accounts",
    )
    bank_name = models.CharField(max_length=150)
    account_holder = models.CharField(max_length=150)
    account_number = models.CharField(max_length=50)
    ifsc = models.CharField(max_length=20)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def masked_account_number(self):
        if len(self.account_number) <= 4:
            return self.account_number
        return f"•••• {self.account_number[-4:]}"

    def __str__(self):
        return f"{self.bank_name} - {self.masked_account_number}"
