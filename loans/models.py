"""Loan model — core entity with health scoring, types, and notes."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from decimal import ROUND_HALF_UP
from loans.utils import add_periods, calculate_remaining_periods, get_period_details


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

        target = (
            current_balance / Decimal("100000")
        ).to_integral_value(rounding=ROUND_HALF_UP) * Decimal("100000")

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
    """Uploaded documents for a loan (Agreement, ID proof, etc.)."""

    DOC_TYPES = [
        ("agreement", "Loan Agreement"),
        ("id_proof", "ID Proof"),
        ("income_proof", "Income Proof"),
        ("property_papers", "Property Papers"),
        ("other", "Other"),
    ]
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=200)
    doc_type = models.CharField(max_length=20, choices=DOC_TYPES, default="other")
    file = models.FileField(upload_to="loan_documents/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.loan.loan_name})"


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
