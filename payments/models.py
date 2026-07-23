"""Payment and Prepayment models — track EMI payments and extra payments."""

from django.db import models
from django.conf import settings


class Payment(models.Model):
    """
    Records a single EMI payment with its principal/interest breakdown
    and the balance remaining after the payment.
    """

    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('pending', 'Pending'),
        ('overdue', 'Overdue'),
    ]

    loan = models.ForeignKey(
        'loans.Loan',
        on_delete=models.CASCADE,
        related_name='payments'
    )
    payment_number = models.PositiveIntegerField(
        help_text="Sequential EMI number (1, 2, 3...)"
    )
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    principal_component = models.DecimalField(max_digits=15, decimal_places=2)
    interest_component = models.DecimalField(max_digits=15, decimal_places=2)
    balance_after = models.DecimalField(
        max_digits=15, decimal_places=2,
        help_text="Remaining balance after this payment"
    )
    due_date = models.DateField()
    payment_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['payment_number']
        unique_together = ['loan', 'payment_number']

    def __str__(self):
        return f"EMI #{self.payment_number} — {self.loan.loan_name}"


class Prepayment(models.Model):
    """
    Records an extra payment made towards the loan principal,
    reducing the outstanding balance and saving future interest.
    """

    loan = models.ForeignKey(
        'loans.Loan',
        on_delete=models.CASCADE,
        related_name='prepayments'
    )
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    prepayment_date = models.DateField()
    months_reduced = models.PositiveIntegerField(
        default=0,
        help_text="Estimated number of months reduced from tenure"
    )
    interest_saved = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="Estimated total interest saved"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-prepayment_date']

    def __str__(self):
        return f"Prepayment ₹{self.amount} — {self.loan.loan_name}"
