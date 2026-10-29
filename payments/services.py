"""Legacy entry point deliberately cannot fabricate a received payment."""


def process_emi_payment(
    loan, payment_date=None, payment_mode="manual", payment_type="emi"
):
    raise ValueError(
        "Submit a receipt and have a separate authorised user verify it. Auto-debit is not bank confirmation."
    )
