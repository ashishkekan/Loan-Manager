"""Forms for creating and managing loans."""

import os
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from loans.models import Loan, LoanDisbursement, LoanDocument, LoanNote


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


class LoanDisbursementForm(forms.ModelForm):
    class Meta:
        model = LoanDisbursement
        fields = [
            "disbursement_date",
            "amount",
            "purpose",
            "remarks",
            "status",
        ]
        widgets = {
            "disbursement_date": forms.DateInput(
                attrs={
                    "class": "form-input",
                    "type": "date",
                }
            ),
            "amount": forms.NumberInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "500000",
                    "min": "1",
                    "step": "0.01",
                }
            ),
            "purpose": forms.Select(
                attrs={
                    "class": "form-input",
                }
            ),
            "remarks": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 3,
                    "placeholder": "Add disbursement remarks...",
                }
            ),
            "status": forms.Select(
                attrs={
                    "class": "form-input",
                }
            ),
        }

    def __init__(self, *args, loan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.loan = loan
        self.fields["amount"].help_text = "Actual amount released by bank."
        self.fields["disbursement_date"].help_text = (
            "Interest calculation will start from this date."
        )

    def clean_disbursement_date(self):
        value = self.cleaned_data["disbursement_date"]
        if self.loan and value < self.loan.start_date:
            raise ValidationError("Disbursement date cannot be before loan start date.")

        if value > timezone.now().date():
            raise ValidationError("Future disbursement is not allowed.")

        return value

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= Decimal("0"):
            raise ValidationError("Amount should be greater than zero.")
        return amount

    def clean(self):
        cleaned = super().clean()
        if not self.loan:
            return cleaned
        amount = cleaned.get("amount")
        status = cleaned.get("status")
        if amount is None:
            return cleaned
        previous_total = self.loan.total_disbursed_amount
        if self.instance.pk:
            previous_total -= self.instance.amount
        if status == "released":
            if previous_total + amount > self.loan.amount:
                remaining = self.loan.amount - previous_total
                raise ValidationError(
                    f"Remaining sanction amount is only ₹{remaining}."
                )
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.loan = self.loan
        if commit:
            obj.save()
        return obj


class LoanDocumentForm(forms.ModelForm):

    class Meta:
        model = LoanDocument
        fields = ["title", "doc_type", "file", "notes"]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Agreement Copy"}
            ),
            "doc_type": forms.Select(attrs={"class": "form-input"}),
            "file": forms.FileInput(
                attrs={
                    "class": "form-input",
                    "accept": ".pdf,.jpg,.jpeg,.png,.doc,.docx",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "placeholder": "Add notes about this document...",
                    "rows": 4,
                }
            ),
        }

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if not file:
            raise ValidationError("Please select a document.")
        max_size = 10 * 1024 * 1024
        if file.size > max_size:
            raise ValidationError("Maximum file size is 10 MB.")
        allowed_extensions = {
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
            ".doc",
            ".docx",
        }
        extension = os.path.splitext(file.name)[1].lower()
        if extension not in allowed_extensions:
            raise ValidationError("Only PDF, JPG, PNG, DOC and DOCX files are allowed.")
        allowed_content_types = {
            "application/pdf",
            "image/jpeg",
            "image/png",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        if file.content_type not in allowed_content_types:
            raise ValidationError("Invalid file type.")
        return file
