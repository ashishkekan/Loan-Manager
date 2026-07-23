"""Forms for recording EMI payments and prepayments."""

from django import forms
from .models import Prepayment


class PrepaymentForm(forms.ModelForm):
    """Form for making a prepayment towards loan principal."""

    class Meta:
        model = Prepayment
        fields = ['amount', 'prepayment_date']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-input', 'placeholder': 'Amount', 'min': '1', 'step': '0.01'
            }),
            'prepayment_date': forms.DateInput(attrs={
                'class': 'form-input', 'type': 'date'
            }),
        }

    def __init__(self, *args, **kwargs):
        self.loan = kwargs.pop('loan', None)
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if self.loan and amount:
            if amount > self.loan.remaining_balance:
                raise forms.ValidationError(
                    f"Prepayment cannot exceed remaining balance of ₹{self.loan.remaining_balance:,.2f}"
                )
            if amount <= 0:
                raise forms.ValidationError("Amount must be greater than zero.")
        return amount
