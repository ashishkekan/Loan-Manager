"""URL routes for the loans app."""

from django.urls import path

from loans.views import (
    LoanCompareView,
    LoanCreateView,
    LoanDeleteView,
    LoanDetailView,
    LoanDisbursementCreateView,
    LoanDisbursementDeleteView,
    LoanDisbursementDetailView,
    LoanDisbursementListView,
    LoanDisbursementUpdateView,
    LoanListView,
    LoanUpdateView,
    add_note,
    close_loan,
    delete_document,
    delete_note,
    documents_dashboard,
    download_document,
    export_loan_csv,
    mark_all_notifications_read,
    mark_notification_read,
    notifications_dashboard,
    upload_document,
    view_document,
)

urlpatterns = [
    path("loans/", LoanListView.as_view(), name="loan_list"),
    path("loans/create/", LoanCreateView.as_view(), name="create_loan"),
    path("loans/compare/", LoanCompareView.as_view(), name="loan_compare"),
    path("loans/<int:pk>/", LoanDetailView.as_view(), name="loan_detail"),
    path("loans/<int:pk>/delete/", LoanDeleteView.as_view(), name="delete_loan"),
    path("loan/<int:pk>/edit/", LoanUpdateView.as_view(), name="edit_loan"),
    path("loan/<int:pk>/close/", close_loan, name="close_loan"),
    path("loans/<int:loan_id>/export/csv/", export_loan_csv, name="export_csv"),
    path("loans/<int:loan_id>/note/add/", add_note, name="add_note"),
    path(
        "loans/<int:loan_id>/note/<int:note_id>/delete/",
        delete_note,
        name="delete_note",
    ),
    path(
        "loans/<int:loan_id>/documents/upload/", upload_document, name="upload_document"
    ),
    path(
        "loans/<int:loan_id>/documents/<int:doc_id>/delete/",
        delete_document,
        name="delete_document",
    ),
    path("documents/", documents_dashboard, name="documents_dashboard"),
    path("documents/upload/", upload_document, name="document_upload"),
    path("documents/<int:document_id>/view/", view_document, name="view_document"),
    path(
        "documents/<int:document_id>/download/",
        download_document,
        name="download_document",
    ),
    path(
        "documents/<int:document_id>/delete/", delete_document, name="document_delete"
    ),
    path(
        "loans/<int:loan_id>/disbursements/",
        LoanDisbursementListView.as_view(),
        name="loan_disbursement_list",
    ),
    path(
        "disbursement/<int:pk>/",
        LoanDisbursementDetailView.as_view(),
        name="loan_disbursement_detail",
    ),
    path(
        "loans/<int:loan_id>/disbursement/create/",
        LoanDisbursementCreateView.as_view(),
        name="create_disbursement",
    ),
    path(
        "disbursement/<int:pk>/edit/",
        LoanDisbursementUpdateView.as_view(),
        name="edit_disbursement",
    ),
    path(
        "disbursement/<int:pk>/delete/",
        LoanDisbursementDeleteView.as_view(),
        name="delete_disbursement",
    ),
    path("notifications/", notifications_dashboard, name="notifications_dashboard"),
    path(
        "<int:notification_id>/read/",
        mark_notification_read,
        name="mark_notification_read",
    ),
    path(
        "mark-all-read/",
        mark_all_notifications_read,
        name="mark_all_notifications_read",
    ),
]
