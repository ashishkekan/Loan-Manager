from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import DecimalField, Sum
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
        return context


@login_required
@require_POST
@transaction.atomic
def invest_in_loan(request, loan_id):
    loan = get_object_or_404(
        Loan.objects.select_for_update(), pk=loan_id, is_public=True, status="active"
    )
    if loan.disbursements.filter(status="released").exists() or loan.payments.exists():
        messages.error(
            request, "Funding closes once a loan is disbursed or repayments begin."
        )
        return redirect("marketplace")
    profile = Profile.objects.select_for_update().get(user=request.user)
    if (
        profile.role != "lender"
        or not profile.kyc_verified
        or loan.user_id == request.user.id
    ):
        messages.error(
            request,
            "Verified lender access is required; self-investment is not allowed.",
        )
        return redirect("marketplace")
    try:
        amount = Decimal(request.POST.get("amount", "0"))
        if (
            not amount.is_finite()
            or amount != amount.quantize(Decimal("0.01"))
            or amount <= 0
            or amount > loan.amount - loan.funded_amount
            or amount > profile.available_funds
        ):
            raise ValueError()
    except (InvalidOperation, ValueError):
        messages.error(
            request,
            "Enter a valid amount within available funds and the funding limit.",
        )
        return redirect("marketplace")
    Investment.objects.create(loan=loan, lender=request.user, amount=amount)
    loan.funded_amount += amount
    profile.available_funds -= amount
    loan.save(update_fields=["funded_amount"])
    profile.save(update_fields=["available_funds"])
    messages.success(request, "Investment recorded against available funds.")
    return redirect("marketplace")
