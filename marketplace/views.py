import hashlib
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.db import transaction
from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, TemplateView

from accounts.models import Profile
from loans.models import Investment, Loan, LoanDocument
from marketplace.forms import InvestForm, ProfileSetupForm


class SetupProfileView(LoginRequiredMixin, CreateView):
    model = Profile
    form_class = ProfileSetupForm
    template_name = "marketplace/setup_profile.html"

    def get_object(self, queryset=None):
        return self.request.user.profile

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user.profile
        return kwargs

    def form_valid(self, form):
        messages.success(
            self.request, "Profile updated! You can now access the marketplace."
        )
        return super().form_valid(form)

    def get_success_url(self):
        if self.request.POST.get("role") == "lender":
            return reverse_lazy("marketplace")
        return reverse_lazy("create_loan")


class MarketplaceView(LoginRequiredMixin, ListView):
    model = Loan
    template_name = "marketplace/marketplace.html"
    context_object_name = "opportunities"
    paginate_by = 9

    def get_queryset(self):
        return Loan.objects.filter(is_public=True, status="active").order_by(
            "-interest_rate"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        context["is_lender"] = profile.role == "lender" and profile.kyc_verified
        context["profile_complete"] = profile.role != "guest"
        for loan in context["opportunities"]:
            loan.confirmation = signing.dumps(
                {
                    "loan": loan.pk,
                    "user": self.request.user.pk,
                    "nonce": uuid.uuid4().hex,
                },
                salt="investment",
            )
        return context


@login_required
@require_POST
def invest_in_loan(request, loan_id):
    token = request.POST.get("confirmation", "")
    try:
        data = signing.loads(token, salt="investment", max_age=86400)
        if data["loan"] != loan_id or data["user"] != request.user.pk:
            raise ValueError("Invalid investment confirmation.")
        key = hashlib.sha256(token.encode()).hexdigest()
        form = InvestForm(request.POST)
        if not form.is_valid():
            raise ValueError("Enter a valid positive investment amount.")
        amount = form.cleaned_data["amount"]
        with transaction.atomic():
            loan = get_object_or_404(
                Loan.objects.select_for_update(), pk=loan_id, is_public=True
            )
            profile = Profile.objects.select_for_update().get(user=request.user)
            if Investment.objects.filter(request_key=key, lender=request.user).exists():
                messages.info(request, "This investment is already recorded.")
                return redirect("marketplace")
            if profile.role != "lender" or not profile.kyc_verified:
                raise ValueError("Complete lender verification before investing.")
            if loan.user_id == request.user.pk or loan.status != "active":
                raise ValueError("This loan is not eligible for investment.")
            if (
                amount <= 0
                or amount > loan.amount - loan.funded_amount
                or amount > profile.available_funds
            ):
                raise ValueError(
                    "Amount exceeds available funds or remaining funding capacity."
                )
            Investment.objects.create(
                loan=loan, lender=request.user, amount=amount, request_key=key
            )
            loan.funded_amount += amount
            loan.save(update_fields=["funded_amount"])
            profile.available_funds -= amount
            profile.save(update_fields=["available_funds"])
        messages.success(
            request,
            "Investment recorded against your available platform funds. No bank transfer was initiated.",
        )
    except (signing.BadSignature, ValueError, KeyError) as exc:
        messages.error(
            request,
            (
                str(exc)
                if isinstance(exc, ValueError)
                else "Confirmation expired. Refresh the marketplace."
            ),
        )
    return redirect("marketplace")
