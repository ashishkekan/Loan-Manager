"""URL routes for the payments app."""

from django.urls import path

from payments.views import (
    EMIScheduleView,
    TransactionLedgerView,
    export_schedule_excel,
    make_prepayment,
    pay_emi,
)

urlpatterns = [
    path("loans/<int:loan_id>/pay-emi/", pay_emi, name="pay_emi"),
    path("loans/<int:loan_id>/prepay/", make_prepayment, name="make_prepayment"),
    path(
        "loans/<int:loan_id>/schedule/", EMIScheduleView.as_view(), name="emi_schedule"
    ),
    path(
        "loans/<int:loan_id>/schedule/excel/",
        export_schedule_excel,
        name="export_excel",
    ),
    path(
        "loans/<int:loan_id>/ledger/",
        TransactionLedgerView.as_view(),
        name="transaction_ledger",
    ),
]
