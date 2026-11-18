from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from datetime import timedelta

from django.utils import timezone

from loans.models import Loan
from loans.services import AccruedInterestService
from operations.models import LoanRevision
from operations.reminders import deliver_reminders, queue_reminders
from operations.services import apply_revision


class Command(BaseCommand):
    help = "Apply approved effective revisions and deliver due/expiry reminders; never marks bank payments paid."

    def handle(self, *args, **options):
        failures = 0
        accruals = 0
        accrual_until = timezone.localdate() + timedelta(days=1)
        for loan in Loan.objects.filter(status="active").iterator():
            before = loan.interest_accruals.count()
            AccruedInterestService.accrue_until(loan, accrual_until)
            accruals += loan.interest_accruals.count() - before
        for pk in LoanRevision.objects.filter(
            status="approved", effective_from__lte=timezone.localdate()
        ).values_list("pk", flat=True):
            try:
                apply_revision(pk)
            except ValidationError as exc:
                failures += 1
                self.stderr.write(f"Revision {pk}: {'; '.join(exc.messages)}")
        from operations.gateway import queue_recurring_payments

        queue_recurring_payments()
        queue_reminders()
        deliver_reminders()
        self.stdout.write(
            f"Operations processed. Daily accruals: {accruals}. "
            f"Blocked revisions: {failures}."
        )
