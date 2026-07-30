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
            "first_emi_date",
            "emi_frequency",
            "auto_debit",
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
            "first_emi_date": forms.DateInput(
                attrs={
                    "class": "form-input",
                    "type": "date",
                }
            ),
            "emi_frequency": forms.Select(
                attrs={
                    "class": "form-input",
                }
            ),
            "auto_debit": forms.CheckboxInput(
                attrs={
                    "class": "form-checkbox",
                }
            ),
        }
        labels = {
            "start_date": "Loan Creation Date",
            "first_emi_date": "First EMI Date",
            "emi_frequency": "EMI Frequency",
            "auto_debit": "Auto Debit EMI",
        }
        help_texts = {
            "start_date": "Date on which loan was created.",
            "first_emi_date": "EMI schedule will start from this date.",
            "auto_debit": "EMI will be automatically marked paid on every due date.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["auto_debit"].initial = True
        self.fields["emi_frequency"].initial = "monthly"
        if not self.instance.pk:
            self.fields["first_emi_date"].required = True

    def clean(self):
        cleaned_data = super().clean()
        loan_date = cleaned_data.get("start_date")
        first_emi = cleaned_data.get("first_emi_date")
        if loan_date and first_emi:
            if first_emi < loan_date:
                self.add_error(
                    "first_emi_date",
                    "First EMI date cannot be before loan creation date.",
                )
        return cleaned_data

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
