"""Views for the loans app — CRUD operations on Loan objects."""

from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages

from .models import Loan
from .forms import LoanForm
from .utils import calculate_emi


class LoanListView(LoginRequiredMixin, ListView):
    """Display all loans for the logged-in user."""
    model = Loan
    template_name = 'loans/loan_list.html'
    context_object_name = 'loans'

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user).order_by('-created_at')


class LoanCreateView(LoginRequiredMixin, CreateView):
    """Create a new loan with automatic EMI calculation."""
    model = Loan
    form_class = LoanForm
    template_name = 'loans/create_loan.html'

    def form_valid(self, form):
        form.instance.user = self.request.user
        amount = form.cleaned_data['amount']
        rate = form.cleaned_data['interest_rate']
        tenure = form.cleaned_data['tenure_years']

        form.instance.emi = calculate_emi(amount, rate, tenure)
        form.instance.remaining_balance = amount
        return super().form_valid(form)

    def get_success_url(self):
        messages.success(self.request, f'"{self.object.loan_name}" created successfully!')
        return reverse_lazy('loan_detail', kwargs={'pk': self.object.pk})


class LoanDetailView(LoginRequiredMixin, DetailView):
    """Detailed view of a single loan with payments, progress, and charts."""
    model = Loan
    template_name = 'loans/loan_detail.html'
    context_object_name = 'loan'

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        loan = self.object

        # Paid EMI payments
        context['paid_payments'] = loan.payments.filter(
            status='paid'
        ).order_by('payment_number')

        # Prepayments
        context['prepayments'] = loan.prepayments.all().order_by('-prepayment_date')

        # Calculate totals
        total_principal_paid = loan.amount - loan.remaining_balance
        context['total_principal_paid'] = total_principal_paid
        context['total_paid'] = total_principal_paid + loan.total_interest_paid
        context['progress'] = loan.progress_percent

        # Projected schedule for charts (from current balance)
        from .utils import generate_projected_schedule
        projected = generate_projected_schedule(loan)
        context['projected_schedule_json'] = self._schedule_to_chart_data(projected)

        # Pie chart: principal vs interest (from actual payments)
        context['pie_data_json'] = self._pie_chart_data(loan)

        return context

    def _schedule_to_chart_data(self, schedule):
        """Convert amortization schedule to JSON-friendly chart data."""
        labels = [f"M{r['month']}" for r in schedule[:60]]  # Cap at 60 months for readability
        balances = [float(r['balance']) for r in schedule[:60]]
        return {'labels': labels, 'balances': balances}

    def _pie_chart_data(self, loan):
        """Prepare principal vs interest data for pie chart."""
        principal_paid = float(loan.amount - loan.remaining_balance)
        interest_paid = float(loan.total_interest_paid)
        return {
            'principal': round(principal_paid, 2),
            'interest': round(interest_paid, 2),
        }


class LoanDeleteView(LoginRequiredMixin, DeleteView):
    """Delete a loan and all associated data."""
    model = Loan
    success_url = reverse_lazy('loan_list')

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Loan deleted successfully.')
        return super().delete(request, *args, **kwargs)
