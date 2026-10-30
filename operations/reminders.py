from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from loans.models import Loan, LoanDocument, NotificationPreference
from loans.utils import create_notification, generate_full_schedule

from .models import Reminder


def queue_reminders(today=None):
    today = today or timezone.localdate()
    for loan in Loan.objects.filter(status="active"):
        preference, _ = NotificationPreference.objects.get_or_create(user=loan.user)
        due_rows = [
            r
            for r in generate_full_schedule(loan)
            if (r["due_date"] - today).days in settings.REMINDER_DAYS
            or (r["due_date"] < today and today.weekday() == 0)
        ]
        for row in due_rows:
            if row["is_paid"]:
                continue
            text = f"{loan.loan_name}: INR {row['total_debit']} due on {row['due_date']}. Record your payment reference for verification."
            channels = [
                ("inapp", preference.inapp_emi, ""),
                ("email", preference.email_emi, loan.user.email),
                (
                    "sms",
                    preference.sms_emi,
                    getattr(loan.user.profile, "phone", "") or "",
                ),
            ]
            for channel, enabled, destination in channels:
                if enabled:
                    Reminder.objects.get_or_create(
                        key=f"emi:{loan.pk}:{row['period']}:{today}:{channel}",
                        defaults={
                            "loan": loan,
                            "channel": channel,
                            "destination": destination,
                            "message": text,
                        },
                    )
        for doc in LoanDocument.objects.filter(
            loan=loan, expires_on__gte=today, expires_on__lte=today + timedelta(days=7)
        ):
            if preference.inapp_documents:
                Reminder.objects.get_or_create(
                    key=f"document:{doc.pk}:{doc.expires_on}",
                    defaults={
                        "loan": loan,
                        "channel": "inapp",
                        "message": f"Document {doc.title} expires on {doc.expires_on}. Upload a new version.",
                    },
                )


def deliver_reminders(limit=100):
    # At-least-once external delivery. Pass the key to SMS adapters for provider deduplication.
    ids = list(
        Reminder.objects.filter(status__in=["pending", "failed"], attempts__lt=5)
        .order_by("id")
        .values_list("id", flat=True)[:limit]
    )
    for pk in ids:
        with transaction.atomic():
            item = Reminder.objects.select_for_update().get(pk=pk)
            if item.status not in ("pending", "failed") or item.attempts >= 5:
                continue
            item.attempts += 1
            try:
                if item.channel == "inapp":
                    create_notification(
                        item.loan.user, "Loan reminder", item.message, "loan", item.loan
                    )
                elif item.channel == "email":
                    if not item.destination:
                        raise ValueError("No email address")
                    if not send_mail(
                        "Loan reminder",
                        item.message,
                        settings.DEFAULT_FROM_EMAIL,
                        [item.destination],
                    ):
                        raise ValueError("Email not accepted")
                else:
                    if not settings.SMS_ADAPTER or not item.destination:
                        raise ValueError("SMS adapter or phone is not configured")
                    import_string(settings.SMS_ADAPTER)(
                        destination=item.destination,
                        message=item.message,
                        idempotency_key=item.key,
                    )
                item.status = "sent"
                item.error = ""
                item.sent_at = timezone.now()
            except Exception:
                item.status = "failed"
                item.error = (
                    "Delivery failed; check destination and configured provider."
                )
            item.save()
