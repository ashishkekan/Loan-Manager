"""URL routes for the payments app."""

from django.urls import path

from payments.views import (
    EMIScheduleView,
    TransactionLedgerView,
    download_statement,
    export_payment_excel,
    export_prepayment_excel,
    export_prepayment_pdf,
    export_schedule_excel,
    make_prepayment,
    pay_emi,
    payment_dashboard,
    prepayment_dashboard,
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
    path("payments/", payment_dashboard, name="payment_dashboard"),
    path("export/excel/", export_payment_excel, name="export_payment_excel"),
    path("statement/", download_statement, name="download_statement"),
    path("prepayments/", prepayment_dashboard, name="prepayment_dashboard"),
    path(
        "prepayments/export/excel/",
        export_prepayment_excel,
        name="export_prepayment_excel",
    ),
    path(
        "prepayments/export/pdf/", export_prepayment_pdf, name="export_prepayment_pdf"
    ),
]
