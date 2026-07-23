"""Loan model — core entity of the system."""

from django.db import models
from django.conf import settings


class Loan(models.Model):
    """Represents a single loan taken by a user."""

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('closed', 'Closed'),
        ('default', 'Default'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='loans'
    )
    loan_name = models.CharField(max_length=200, help_text="e.g. Home Loan, Car Loan")
    amount = models.DecimalField(
        max_digits=15, decimal_places=2,
        help_text="Original principal amount"
    )
    interest_rate = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Annual interest rate in percent"
    )
    tenure_years = models.PositiveIntegerField(help_text="Loan tenure in years")
    emi = models.DecimalField(
        max_digits=15, decimal_places=2,
        help_text="Calculated monthly EMI"
    )
    start_date = models.DateField(help_text="Date of first EMI")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='active'
    )
    remaining_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    total_interest_paid = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.loan_name} — {self.user.get_full_name()}"

    @property
    def total_payable(self):
        """Total amount payable over full tenure (projected)."""
        return self.emi * self.tenure_years * 12

    @property
    def total_interest_projected(self):
        """Total interest projected over full tenure."""
        return self.total_payable - self.amount

    @property
    def progress_percent(self):
        """Percentage of principal repaid."""
        if self.amount <= 0:
            return 0
        paid = float(self.amount - self.remaining_balance)
        return round(min(paid / float(self.amount) * 100, 100), 1)

    @property
    def months_elapsed(self):
        """Number of EMI payments made so far."""
        return self.payments.filter(status='paid').count()

    @property
    def months_remaining(self):
        """Approximate remaining months based on current balance."""
        from .utils import calculate_remaining_months
        return calculate_remaining_months(
            self.remaining_balance, self.interest_rate, self.emi
        )
