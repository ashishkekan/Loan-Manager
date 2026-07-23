from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Profile(models.Model):
    """Extended user profile with roles, KYC, and lending capacity."""

    ROLE_CHOICES = [
        ("guest", "Guest"),
        ("borrower", "Borrower"),
        ("lender", "Lender"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="guest")
    phone = models.CharField(max_length=15, blank=True, null=True)

    # Lender specific fields
    available_funds = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    kyc_verified = models.BooleanField(default=False)
    pan_number = models.CharField(max_length=10, blank=True, null=True)

    # Borrower specific fields
    credit_score = models.PositiveIntegerField(
        default=750, help_text="CIBIL score approximation"
    )
    annual_income = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.get_role_display()})"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()
