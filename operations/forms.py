from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from loans.models import LoanDocument

from .access import loans_for
from .models import *


class ReceiptForm(forms.ModelForm):
    class Meta:
        model = Receipt
        fields = [
            "loan",
            "kind",
            "amount",
            "received_on",
            "reference",
            "proof",
            "charge",
        ]
        widgets = {"received_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["loan"].queryset = loans_for(user).filter(status="active")
        self.fields["charge"].queryset = Charge.objects.filter(
            loan__in=loans_for(user), status="due"
        )

    def clean(self):
        data = super().clean()
        loan, day = data.get("loan"), data.get("received_on")
        if loan and day and not loan.start_date <= day <= timezone.localdate():
            raise forms.ValidationError(
                "Payment date must be between loan start and today."
            )
        charge = data.get("charge")
        if data.get("kind") == "charge" and (
            not charge
            or charge.loan_id != getattr(loan, "id", None)
            or charge.amount != data.get("amount")
        ):
            raise forms.ValidationError(
                "Choose an unpaid charge on this loan and enter its exact amount."
            )
        if data.get("kind") != "charge" and charge:
            raise forms.ValidationError(
                "Charge should be blank for EMI and prepayment."
            )
        proof = data.get("proof")
        if proof:
            validate_upload(proof)
        return data


def validate_upload(file):
    if file.size > 5 * 1024 * 1024:
        raise forms.ValidationError("Maximum upload size is 5 MB.")
    extension = file.name.lower().rsplit(".", 1)[-1]
    if extension not in ("pdf", "jpg", "jpeg", "png"):
        raise forms.ValidationError("Only PDF, JPG and PNG files are accepted.")
    header = file.read(8)
    file.seek(0)
    expected = {
        "pdf": b"%PDF-",
        "png": b"\x89PNG\r\n\x1a\n",
        "jpg": b"\xff\xd8\xff",
        "jpeg": b"\xff\xd8\xff",
    }
    if not header.startswith(expected[extension]):
        raise forms.ValidationError(
            "File content does not match the selected document format."
        )


class ApplicationForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = [
            "organization",
            "branch",
            "loan_name",
            "loan_type",
            "amount",
            "interest_rate",
            "tenure_years",
            "frequency",
            "first_emi_date",
            "purpose",
        ]
        widgets = {"first_emi_date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        data = super().clean()
        if data.get("first_emi_date") and data["first_emi_date"] < timezone.localdate():
            raise forms.ValidationError("First EMI date cannot be in the past.")
        if data.get("branch") and data["branch"].organization != data.get(
            "organization"
        ):
            raise forms.ValidationError("Branch must belong to the selected company.")
        return data


class RevisionForm(forms.ModelForm):
    class Meta:
        model = LoanRevision
        fields = [
            "loan",
            "kind",
            "effective_from",
            "next_due",
            "rate",
            "periods",
            "reason",
        ]
        widgets = {
            k: forms.DateInput(attrs={"type": "date"})
            for k in ("effective_from", "next_due")
        }

    def clean(self):
        d = super().clean()
        if d.get("effective_from") and d["effective_from"] < timezone.localdate():
            raise forms.ValidationError("A revision cannot be backdated.")
        if (
            d.get("next_due")
            and d.get("effective_from")
            and d["next_due"] < d["effective_from"]
        ):
            raise forms.ValidationError(
                "Next due date must be on/after effective date."
            )
        return d


class FollowupForm(forms.ModelForm):
    class Meta:
        model = CollectionFollowup
        fields = [
            "loan",
            "agent",
            "next_followup",
            "promise_date",
            "promise_amount",
            "notes",
            "resolved",
        ]
        widgets = {
            k: forms.DateInput(attrs={"type": "date"})
            for k in ("next_followup", "promise_date")
        }


class PolicyForm(forms.ModelForm):
    class Meta:
        model = ChargePolicy
        fields = ["loan_type", "kind", "effective_from", "fixed_amount", "percentage"]
        widgets = {"effective_from": forms.DateInput(attrs={"type": "date"})}


class ChargeForm(forms.ModelForm):
    class Meta:
        model = Charge
        fields = ["loan", "policy", "reason"]


class DocumentForm(forms.ModelForm):
    class Meta:
        model = LoanDocument
        fields = ["loan", "title", "doc_type", "file", "previous_version", "expires_on"]
        widgets = {"expires_on": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        d = super().clean()
        if d.get("file"):
            validate_upload(d["file"])
        if d.get("previous_version") and d["previous_version"].loan_id != getattr(
            d.get("loan"), "id", None
        ):
            raise forms.ValidationError("Previous version must belong to this loan.")
        return d


class RelatedPartyForm(forms.ModelForm):
    class Meta:
        model = RelatedParty
        fields = ["loan", "kind", "name", "contact", "consent_date", "document"]
        widgets = {"consent_date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        d = super().clean()
        if d.get("document") and d["document"].loan_id != getattr(
            d.get("loan"), "id", None
        ):
            raise forms.ValidationError("Document must belong to this loan.")
        return d


class CollateralForm(forms.ModelForm):
    class Meta:
        model = Collateral
        fields = ["loan", "description", "valuation", "valued_on", "document"]
        widgets = {"valued_on": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        d = super().clean()
        if d.get("document") and d["document"].loan_id != getattr(
            d.get("loan"), "id", None
        ):
            raise forms.ValidationError("Document must belong to this loan.")
        return d


class ImportForm(forms.Form):
    file = forms.FileField(
        help_text="CSV or XLSX; max 2 MB and 1,000 rows. Preview before importing."
    )


class ReviewForm(forms.Form):
    decision = forms.ChoiceField(choices=[("approve", "Approve"), ("reject", "Reject")])
    reason = forms.CharField(widget=forms.Textarea)


class PlannerForm(forms.Form):
    budget = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        label="Extra budget per month",
    )
    strategy = forms.ChoiceField(
        choices=[
            ("avalanche", "Highest rate first"),
            ("snowball", "Smallest balance first"),
        ]
    )


class TransferForm(forms.Form):
    balance = forms.DecimalField(
        min_value=Decimal("0.01"), max_digits=15, decimal_places=2
    )
    current_rate = forms.DecimalField(min_value=0, max_value=50, decimal_places=2)
    proposed_rate = forms.DecimalField(min_value=0, max_value=50, decimal_places=2)
    months = forms.IntegerField(min_value=1, max_value=600)
    transfer_fees = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)


from loans.models import LoanDisbursement


class DisbursementForm(forms.ModelForm):
    class Meta:
        model = LoanDisbursement
        fields = ["loan", "disbursement_date", "amount", "purpose", "remarks"]
        widgets = {"disbursement_date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        d = super().clean()
        loan = d.get("loan")
        if (
            loan
            and d.get("disbursement_date")
            and not loan.start_date <= d["disbursement_date"] <= timezone.localdate()
        ):
            raise forms.ValidationError("Date must be between loan start and today.")
        if d.get("amount") is not None and d["amount"] <= 0:
            raise forms.ValidationError("Amount must be positive.")
        return d


class ApplicationDocumentForm(forms.ModelForm):
    class Meta:
        model = ApplicationDocument
        fields = ["kind", "file"]

    def clean_file(self):
        file = self.cleaned_data["file"]
        validate_upload(file)
        return file
