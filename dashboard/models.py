from django.contrib.auth.models import User
from django.db import models

from loans.models import Loan


class ActivityLog(models.Model):
    ACTIONS = [
        ("loan_created", "Loan Created"),
        ("loan_closed", "Loan Closed"),
        ("emi_paid", "EMI Paid"),
        ("prepayment", "Prepayment"),
        ("loan_updated", "Loan Updated"),
        ("auto_debit", "Auto Debit"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="activities")
    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activities",
    )
    action = models.CharField(max_length=40, choices=ACTIONS)
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
