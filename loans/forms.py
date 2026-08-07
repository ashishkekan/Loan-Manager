"""Forms for creating and managing loans."""

import os
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.utils import timezone

from loans.models import (
    AppearancePreference,
    BankAccount,
    Loan,
    LoanDisbursement,
    LoanDocument,
    LoanNote,
    NotificationPreference,
    PrivacySetting,
    SupportMessage,
    SupportTicket,
    UserProfile,
)

User = get_user_model()


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


class SupportTicketForm(forms.ModelForm):
    class Meta:
        model = SupportTicket
        fields = ["loan", "category", "subject", "message", "attachment"]
        widgets = {
            "loan": forms.Select(attrs={"class": "form-input"}),
            "category": forms.Select(attrs={"class": "form-input"}),
            "subject": forms.TextInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "Enter your issue title",
                }
            ),
            "message": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 6,
                    "placeholder": "Describe your problem...",
                }
            ),
            "attachment": forms.FileInput(attrs={"class": "form-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields["loan"].queryset = Loan.objects.filter(user=self.user).order_by(
                "loan_name"
            )
            self.fields["loan"].required = False
        self.fields["loan"].empty_label = "General / No Specific Loan"

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return attachment
        max_size = 10 * 1024 * 1024
        if attachment.size > max_size:
            raise ValidationError("Maximum attachment size is 10MB.")
        allowed_extensions = {
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
            ".doc",
            ".docx",
        }
        import os

        extension = os.path.splitext(attachment.name)[1].lower()
        if extension not in allowed_extensions:
            raise ValidationError("Allowed files: PDF, JPG, JPEG, PNG, DOC and DOCX.")
        return attachment


class SupportReplyForm(forms.ModelForm):
    class Meta:
        model = SupportMessage
        fields = ["message", "attachment"]
        widgets = {
            "message": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 4,
                    "placeholder": "Type your reply...",
                }
            ),
            "attachment": forms.FileInput(attrs={"class": "form-input"}),
        }

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return attachment
        if attachment.size > 10 * 1024 * 1024:
            raise ValidationError("Maximum attachment size is 10MB.")
        import os

        allowed_extensions = {
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
            ".doc",
            ".docx",
        }
        extension = os.path.splitext(attachment.name)[1].lower()
        if extension not in allowed_extensions:
            raise ValidationError("Allowed files: PDF, JPG, JPEG, PNG, DOC and DOCX.")
        return attachment


class SettingsProfileForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-input",
                "placeholder": "First Name",
            }
        ),
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-input",
                "placeholder": "Last Name",
            }
        ),
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={
                "class": "form-input",
                "placeholder": "Email Address",
            }
        ),
    )

    class Meta:
        model = UserProfile
        fields = [
            "phone",
            "photo",
            "dob",
            "address",
            "city",
            "state",
            "pincode",
            "occupation",
            "annual_income",
        ]
        widgets = {
            "phone": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Phone Number"}
            ),
            "photo": forms.FileInput(attrs={"class": "form-input"}),
            "dob": forms.DateInput(attrs={"class": "form-input", "type": "date"}),
            "address": forms.Textarea(
                attrs={"class": "form-input", "rows": 3, "placeholder": "Address"}
            ),
            "city": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "City"}
            ),
            "state": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "State"}
            ),
            "pincode": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Pincode"}
            ),
            "occupation": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Occupation"}
            ),
            "annual_income": forms.NumberInput(
                attrs={"class": "form-input", "placeholder": "Annual Income"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name
            self.fields["email"].initial = self.instance.user.email


class SettingsPasswordForm(PasswordChangeForm):
    old_password = forms.CharField(
        label="Current Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-input",
                "placeholder": "Current Password",
            }
        ),
    )
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-input",
                "placeholder": "New Password",
            }
        ),
    )
    new_password2 = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-input",
                "placeholder": "Confirm New Password",
            }
        ),
    )


class NotificationPreferenceForm(forms.ModelForm):
    class Meta:
        model = NotificationPreference
        fields = [
            "email_emi",
            "email_payment",
            "email_updates",
            "email_promotional",
            "inapp_emi",
            "inapp_support",
            "inapp_documents",
            "inapp_loan_updates",
            "sms_emi",
            "sms_otp",
            "sms_payment",
        ]


class AppearancePreferenceForm(forms.ModelForm):
    class Meta:
        model = AppearancePreference
        fields = ["theme", "language", "currency", "date_format"]
        widgets = {
            "theme": forms.Select(attrs={"class": "form-input"}),
            "language": forms.Select(attrs={"class": "form-input"}),
            "currency": forms.TextInput(attrs={"class": "form-input"}),
            "date_format": forms.Select(attrs={"class": "form-input"}),
        }


class PrivacySettingForm(forms.ModelForm):
    class Meta:
        model = PrivacySetting
        fields = [
            "hide_balance",
            "hide_loan_amount",
            "hide_emi_values",
            "analytics",
            "marketing",
        ]


class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = [
            "bank_name",
            "account_holder",
            "account_number",
            "ifsc",
            "is_default",
        ]
        widgets = {
            "bank_name": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Bank Name"}
            ),
            "account_holder": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Account Holder"}
            ),
            "account_number": forms.PasswordInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "Account Number",
                    "render_value": False,
                }
            ),
            "ifsc": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "IFSC Code"}
            ),
        }
