from django import forms

from accounts.models import Profile


class ProfileSetupForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["role", "phone", "pan_number", "annual_income", "credit_score"]
        widgets = {
            "role": forms.Select(attrs={"class": "form-input"}),
            "phone": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "9876543210"}
            ),
            "pan_number": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "ABCDE1234F"}
            ),
            "annual_income": forms.NumberInput(
                attrs={"class": "form-input", "placeholder": "1200000"}
            ),
            "credit_score": forms.NumberInput(
                attrs={"class": "form-input", "placeholder": "750"}
            ),
        }


class InvestForm(forms.ModelForm):
    class Meta:
        model = Investment
        fields = ["amount"]
        widgets = {
            "amount": forms.NumberInput(
                attrs={"class": "form-input", "placeholder": "Investment Amount"}
            )
        }
