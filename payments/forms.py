"""Forms for recording EMI payments and prepayments."""

import datetime

from django import forms

from payments.models import Prepayment


class PrepaymentForm(forms.ModelForm):
    class Meta:
        model = Prepayment
        fields = ["amount", "prepayment_date"]
        widgets = {
            "amount": forms.NumberInput(
                attrs={
                    "class": "form-input",
                    "placeholder": "Amount",
                    "min": "1",
                    "step": "0.01",
                }
            ),
            "prepayment_date": forms.DateInput(
                attrs={"class": "form-input", "type": "date"}
            ),
        }

    def __init__(self, *args, **kwargs):
        self.loan = kwargs.pop("loan", None)
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Amount must be greater than zero.")
        if self.loan and amount is not None:
            from loans.services import AccruedInterestService

            outstanding = AccruedInterestService.calculate_total_debit(self.loan)[
                "outstanding_disbursed"
            ]
            if amount > min(self.loan.remaining_balance, outstanding):
                raise forms.ValidationError(
                    f"Prepayment cannot exceed remaining balance of ₹{self.loan.remaining_balance:,.2f}"
                )
            if amount <= 0:
                raise forms.ValidationError("Amount must be greater than zero.")
        return amount

    def clean_prepayment_date(self):
        prepayment_date = self.cleaned_data.get("prepayment_date")
        if self.loan and prepayment_date:
            from loans.utils import add_periods, next_emi_number

            next_due = add_periods(
                self.loan.schedule_start_date,
                next_emi_number(self.loan) - 1,
                self.loan.emi_frequency,
            )
            if next_due < prepayment_date:
                raise forms.ValidationError(
                    "Record overdue EMIs before a later prepayment."
                )
            if prepayment_date < self.loan.start_date:
                raise forms.ValidationError(
                    "Prepayment cannot be before loan start date."
                )
            if (
                self.loan.payments.filter(
                    status="paid", payment_date__gt=prepayment_date
                ).exists()
                or self.loan.prepayments.filter(
                    status="paid", prepayment_date__gt=prepayment_date
                ).exists()
            ):
                raise forms.ValidationError(
                    "Prepayment cannot precede a recorded payment."
                )
            if self.loan.disbursements.filter(
                status="released", disbursement_date__gt=prepayment_date
            ).exists():
                raise forms.ValidationError(
                    "Prepayment cannot precede a released disbursement."
                )
            if prepayment_date > datetime.datetime.today().date():
                raise forms.ValidationError(
                    "Prepayment date cannot be greater than today's date."
                )
        return prepayment_date
