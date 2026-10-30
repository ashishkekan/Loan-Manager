"""Provider-neutral integration; unconfigured providers fail closed."""

import json
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.module_loading import import_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .access import can_operate
from .models import GatewayEvent, GatewayOrder, Mandate, Receipt
from .services import verify_receipt


def adapter():
    path = getattr(settings, "PAYMENT_ADAPTER", "")
    if not path:
        raise ValueError(
            "Payment provider is not configured. Submit payment proof for verification instead."
        )
    return import_string(path)()


@login_required
@require_POST
def checkout(request, pk):
    receipt = get_object_or_404(
        Receipt, pk=pk, submitted_by=request.user, status="pending", kind="emi"
    )
    try:
        provider = adapter()
        with transaction.atomic():
            receipt = Receipt.objects.select_for_update().get(pk=receipt.pk)
            if receipt.status != "pending":
                raise ValueError("Receipt is no longer pending.")
            order, _ = GatewayOrder.objects.get_or_create(receipt=receipt)
            # Adapter MUST reuse receipt.number as the provider idempotency key.
            result = provider.create_order(
                amount_minor=int(receipt.amount * 100),
                currency="INR",
                reference=str(receipt.number),
                idempotency_key=str(receipt.number),
            )
            order.provider_order = result["id"]
            order.save()
        from urllib.parse import urlparse

        url = result["checkout_url"]
        hosts = getattr(settings, "PAYMENT_CHECKOUT_HOSTS", [])
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in hosts:
            raise ValueError("Checkout host is not configured.")
        return render(
            request,
            "operations/checkout.html",
            {"checkout_url": url, "receipt": receipt},
        )
    except (ValueError, KeyError):
        return render(
            request,
            "operations/checkout.html",
            {
                "error": "Payment provider unavailable or unconfigured. No payment has been posted; use manual verification."
            },
            status=503,
        )


@csrf_exempt
@require_POST
def webhook(request):
    if len(request.body) > 256 * 1024:
        return HttpResponse(status=413)
    try:
        # Provider verifies the signature on raw bytes before returning normalized data.
        event = adapter().verify_webhook(body=request.body, headers=request.headers)
        if not isinstance(event, dict) or not event.get("id") or len(event["id"]) > 120:
            return HttpResponse(status=400)
        with transaction.atomic():
            # Unique event ID and loan locks protect repeated/concurrent deliveries.
            if GatewayEvent.objects.filter(event_id=event["id"]).exists():
                return HttpResponse(status=200)
            if event.get("kind") == "mandate":
                mandate = Mandate.objects.select_for_update().get(
                    provider_reference=event["mandate_id"]
                )
                if event["status"] not in ("active", "revoked", "expired"):
                    return HttpResponse(status=400)
                # Terminal states cannot be reactivated by delayed callbacks.
                if mandate.status not in ("revoked", "expired"):
                    mandate.status = event["status"]
                    mandate.save()
                GatewayEvent.objects.create(
                    event_id=event["id"], kind="mandate." + event["status"]
                )
                return HttpResponse(status=200)
            if event["status"] not in ("captured", "failed"):
                return HttpResponse(status=400)
            order = GatewayOrder.objects.select_for_update().get(
                provider_order=event["order_id"]
            )
            receipt = order.receipt
            if event["currency"] != "INR" or int(event["amount_minor"]) != int(
                receipt.amount * 100
            ):
                return HttpResponse(status=400)
            if event["status"] == "captured":
                if order.status != "paid":
                    service_user = get_user_model().objects.get(
                        pk=settings.PAYMENT_SERVICE_USER_ID, is_active=True
                    )
                    can_operate(service_user, receipt.loan)
                    verify_receipt(
                        receipt.pk,
                        service_user,
                        remarks="Provider-confirmed captured payment",
                    )
                    order.provider_payment = event["payment_id"]
                    order.status = "paid"
                    order.save()
            elif event["status"] == "failed" and order.status != "paid":
                order.status = "failed"
                order.save()
            GatewayEvent.objects.create(event_id=event["id"], kind=event["status"])
        return HttpResponse(status=200)
    except (
        ValueError,
        KeyError,
        GatewayOrder.DoesNotExist,
        Mandate.DoesNotExist,
        ValidationError,
    ):
        return HttpResponse(status=400)
    except IntegrityError:
        return HttpResponse(status=409)


@login_required
def mandates(request):
    from django import forms
    from django.contrib import messages
    from django.utils import timezone

    from .access import loans_for
    from .models import Mandate

    class MandateForm(forms.ModelForm):
        consent = forms.BooleanField(
            label="I authorise recurring payments up to this limit through the selected provider."
        )

        class Meta:
            model = Mandate
            fields = ["loan", "maximum_amount", "expires_on"]
            widgets = {"expires_on": forms.DateInput(attrs={"type": "date"})}

        def clean_expires_on(self):
            day = self.cleaned_data["expires_on"]
            if day <= timezone.localdate():
                raise forms.ValidationError("Expiry must be in the future.")
            return day

    form = MandateForm(request.POST or None)
    form.fields["loan"].queryset = loans_for(request.user).filter(
        user=request.user, status="active"
    )
    checkout_url = None
    if request.method == "POST":
        try:
            if request.POST.get("revoke"):
                mandate = get_object_or_404(
                    Mandate, user=request.user, pk=request.POST["revoke"]
                )
                if mandate.provider_reference:
                    adapter().revoke_mandate(reference=mandate.provider_reference)
                mandate.status = "revoked"
                mandate.save()
                messages.success(request, "Mandate revoked.")
            elif form.is_valid():
                provider = adapter()
                with transaction.atomic():
                    loan = form.cleaned_data["loan"]
                    from loans.models import Loan

                    Loan.objects.select_for_update().get(pk=loan.pk)
                    if Mandate.objects.filter(loan=loan, status="active").exists():
                        raise ValueError(
                            "An active mandate already exists for this loan."
                        )
                    mandate = Mandate.objects.filter(
                        loan=loan, status="pending"
                    ).first()
                    if mandate is None:
                        mandate = form.save(commit=False)
                        mandate.user = request.user
                        mandate.save()
                    elif (
                        mandate.maximum_amount != form.cleaned_data["maximum_amount"]
                        or mandate.expires_on != form.cleaned_data["expires_on"]
                    ):
                        raise ValueError(
                            "Retry with the original pending limits, or revoke the pending mandate first."
                        )
                # Persist the local identifier before calling the provider. Retrying the
                # same pending mandate reuses the provider's idempotency reference.
                result = provider.create_mandate(
                    reference=f"mandate-{mandate.pk}",
                    maximum_minor=int(mandate.maximum_amount * 100),
                    currency="INR",
                    expires_on=str(mandate.expires_on),
                )
                mandate.provider_reference = result["id"]
                mandate.save(update_fields=["provider_reference"])
                from urllib.parse import urlparse

                checkout_url = result["consent_url"]
                parsed = urlparse(checkout_url)
                if (
                    parsed.scheme != "https"
                    or parsed.hostname not in settings.PAYMENT_CHECKOUT_HOSTS
                ):
                    checkout_url = None
                    raise ValueError("Provider consent host is not configured.")
        except (ValueError, NotImplementedError, AttributeError):
            form.add_error(
                None,
                "Mandate provider unavailable or not configured. No recurring debit has been posted.",
            )
    return render(
        request,
        "operations/mandates.html",
        {
            "form": form,
            "items": Mandate.objects.filter(user=request.user),
            "consent_url": checkout_url,
        },
    )


def queue_recurring_payments():
    """Explicitly opt-in worker. Only provider callbacks can post the receipt."""
    from django.utils import timezone

    from loans.models import Loan
    from loans.utils import generate_full_schedule

    from .models import Mandate

    if not getattr(settings, "PAYMENT_RECURRING_ENABLED", False):
        return
    provider = adapter()
    for mid in Mandate.objects.filter(
        status="active", expires_on__gte=timezone.localdate()
    ).values_list("pk", flat=True):
        with transaction.atomic():
            mandate = Mandate.objects.select_for_update().get(pk=mid)
            loan = Loan.objects.select_for_update().get(pk=mandate.loan_id)
            if loan.status != "active":
                continue
            rows = [
                row
                for row in generate_full_schedule(loan)
                if not row["is_paid"] and row["due_date"] <= timezone.localdate()
            ]
            if not rows or loan.receipts.filter(status="pending").exists():
                continue
            row = rows[0]
            if row["total_debit"] > mandate.maximum_amount:
                continue
            ref = f"MANDATE-{mid}-{loan.schedule_version}-{row['period']}"
            if Receipt.objects.filter(reference=ref).exists():
                continue
            receipt = Receipt.objects.create(
                loan=loan,
                submitted_by=mandate.user,
                amount=row["total_debit"],
                received_on=timezone.localdate(),
                reference=ref,
            )
            result = provider.create_recurring_payment(
                mandate=mandate.provider_reference,
                amount_minor=int(receipt.amount * 100),
                currency="INR",
                idempotency_key=ref,
            )
            GatewayOrder.objects.create(receipt=receipt, provider_order=result["id"])
