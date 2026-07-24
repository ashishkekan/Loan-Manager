"""Forms for creating and managing loans."""

from django import forms

from .models import Loan, LoanNote


class LoanForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = [
            "loan_name",
            "loan_type",
            "amount",
            "interest_rate",
            "tenure_years",
            "start_date",
        ]
        widgets = {
            "loan_name": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "e.g. SBI Home Loan"}
            ),
            "loan_type": forms.Select(attrs={"class": "form-input"}),
            "amount": forms.NumberInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "5000000",
                    "min": "1",
                    "step": "0.01",
                }
            ),
            "interest_rate": forms.NumberInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "8.5",
                    "min": "0",
                    "max": "50",
                    "step": "0.01",
                }
            ),
            "tenure_years": forms.NumberInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "20",
                    "min": "1",
                    "max": "50",
                }
            ),
            "start_date": forms.DateInput(
                attrs={"class": "form-input", "type": "date"}
            ),
        }

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount and amount <= 0:
            raise forms.ValidationError("Loan amount must be greater than zero.")
        return amount

    def clean_interest_rate(self):
        rate = self.cleaned_data.get("interest_rate")
        if rate is not None and rate < 0:
            raise forms.ValidationError("Interest rate cannot be negative.")
        return rate

    def clean_tenure_years(self):
        tenure = self.cleaned_data.get("tenure_years")
        if tenure and tenure < 1:
            raise forms.ValidationError("Tenure must be at least 1 year.")
        return tenure


class LoanNoteForm(forms.ModelForm):
    class Meta:
        model = LoanNote
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 3,
                    "placeholder": "Add a note about this loan...",
                }
            )
        }
