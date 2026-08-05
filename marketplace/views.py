from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import DecimalField, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
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
    """Dashboard for Lenders to find investment opportunities."""

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


def invest_in_loan(request, loan_id):
    if request.method == "POST":
        loan = get_object_or_404(Loan, pk=loan_id, is_public=True)
        profile = request.user.profile

        if profile.role != "lender" or not profile.kyc_verified:
            messages.error(request, "Complete lender KYC to invest.")
            return redirect("marketplace")

        amount = float(request.POST.get("amount", 0))
        remaining_to_fund = float(loan.amount) - float(loan.funded_amount)

        if amount <= 0 or amount > remaining_to_fund:
            messages.error(
                request, f"Invalid amount. Max investable: ₹{remaining_to_fund:,.0f}"
            )
            return redirect("loan_detail", pk=loan_id)

        Investment.objects.create(loan=loan, lender=request.user, amount=amount)
        loan.funded_amount += amount
        if loan.funded_amount >= loan.amount:
            loan.status = "active"  # Fully funded
        loan.save()

        messages.success(
            request, f"Successfully invested ₹{amount:,.0f} in {loan.loan_name}!"
        )
    return redirect("loan_detail", pk=loan_id)
