"""Append-only application audit, without document contents or credentials."""

from contextvars import ContextVar
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver

actor = ContextVar("audit_actor", default=None)
reason = ContextVar("audit_reason", default="")
EXCLUDED = {
    "password",
    "pan_number",
    "account_number",
    "phone",
    "email",
    "address",
    "digest",
    "proof",
    "file",
    "before",
    "after",
    "schedule",
}
TRACKED = {
    "loans.loan",
    "loans.loandisbursement",
    "loans.loandocument",
    "payments.payment",
    "payments.prepayment",
    "operations.application",
    "operations.receipt",
    "operations.reversal",
    "operations.charge",
    "operations.chargepolicy",
    "operations.loanrevision",
    "operations.documentreview",
    "operations.kycreview",
    "operations.collectionfollowup",
    "operations.bankentry",
    "operations.onboarding",
    "operations.membership",
    "operations.payout",
    "operations.collateral",
    "operations.relatedparty",
    "operations.organization",
    "operations.branch",
    "operations.lenderallocation",
}


class ActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def process_exception(self, request, exception):
        from django.core.exceptions import ValidationError
        from django.db.models.deletion import ProtectedError
        from django.http import HttpResponseBadRequest
        from django.utils.html import escape

        if isinstance(exception, (ValidationError, ProtectedError)):
            message = (
                "; ".join(exception.messages)
                if isinstance(exception, ValidationError)
                else str(exception.args[0])
            )
            return HttpResponseBadRequest(
                "<h1>Action could not be completed</h1><p>"
                + escape(message)
                + "</p><p>Return to Loan Operations to correct the request.</p>"
            )

    def __call__(self, request):
        token = actor.set(request.user if request.user.is_authenticated else None)
        rt = reason.set(
            request.POST.get("reason", request.POST.get("remarks", ""))[:2000]
            if request.method == "POST"
            else ""
        )
        try:
            return self.get_response(request)
        finally:
            actor.reset(token)
            reason.reset(rt)


def snapshot(obj):
    if not obj:
        return {}
    return {
        f.name: (
            str(getattr(obj, f.attname))
            if getattr(obj, f.attname) is not None
            else None
        )
        for f in obj._meta.concrete_fields
        if f.name not in EXCLUDED
    }


@receiver(pre_save)
def remember(sender, instance, raw=False, **kwargs):
    if not raw and sender._meta.label_lower in TRACKED:
        instance._audit_before = (
            snapshot(sender.objects.filter(pk=instance.pk).first())
            if instance.pk
            else {}
        )


@receiver(post_save)
def record(sender, instance, created, raw=False, **kwargs):
    if not raw and sender._meta.label_lower in TRACKED:
        from .models import AuditEvent

        before, after = getattr(instance, "_audit_before", {}), snapshot(instance)
        if before != after:
            AuditEvent.objects.create(
                actor=actor.get(),
                model=sender._meta.label_lower,
                object_id=str(instance.pk),
                action="create" if created else "update",
                before=before,
                after=after,
                reason=reason.get(),
            )


@receiver(post_delete)
def deleted(sender, instance, **kwargs):
    if sender._meta.label_lower in TRACKED:
        from .models import AuditEvent

        AuditEvent.objects.create(
            actor=actor.get(),
            model=sender._meta.label_lower,
            object_id=str(instance.pk),
            action="delete",
            before=snapshot(instance),
            reason=reason.get(),
        )


@receiver(pre_save, sender="loans.LoanDisbursement")
def disbursement_before(sender, instance, **kwargs):
    from decimal import Decimal

    old = sender.objects.filter(pk=instance.pk).first() if instance.pk else None
    instance._released_before = (
        old.amount if old and old.status == "released" else Decimal("0")
    )


@receiver(post_save, sender="loans.LoanDisbursement")
def disbursement_balance(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from decimal import Decimal
    from django.db.models import Sum
    from loans.models import Loan

    loan = Loan.objects.get(pk=instance.loan_id)
    principal = loan.payments.filter(status__in=["paid", "partial"]).aggregate(
        v=Sum("principal_component")
    )["v"] or Decimal(0)
    prepaid = loan.prepayments.filter(status="paid").aggregate(v=Sum("amount"))[
        "v"
    ] or Decimal(0)
    loan.remaining_balance = max(
        Decimal(0),
        loan.total_disbursed_amount + loan.capitalized_interest - principal - prepaid,
    )
    loan.save(update_fields=["remaining_balance"])


from django.db.models.signals import pre_delete
from django.db.models.deletion import ProtectedError


@receiver(pre_delete)
def protect_financial_history(sender, instance, **kwargs):
    label = sender._meta.label_lower
    if label in ("payments.payment", "payments.prepayment", "loans.loandisbursement"):
        raise ProtectedError(
            "Financial history cannot be deleted. Use correction/reversal workflows.",
            [instance],
        )
    if label == "loans.loan" and (
        instance.payments.exists()
        or instance.prepayments.exists()
        or instance.disbursements.exists()
    ):
        raise ProtectedError("A funded loan cannot be deleted.", [instance])
