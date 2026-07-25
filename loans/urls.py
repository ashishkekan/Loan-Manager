"""URL routes for the loans app."""

from django.urls import path

from .views import (
    LoanCompareView,
    LoanCreateView,
    LoanDeleteView,
    LoanDetailView,
    LoanListView,
    add_note,
    delete_document,
    delete_note,
    export_loan_csv,
    upload_document,
)

urlpatterns = [
    # List & Create
    path("loans/", LoanListView.as_view(), name="loan_list"),
    path("loans/create/", LoanCreateView.as_view(), name="create_loan"),
    # Compare
    path("loans/compare/", LoanCompareView.as_view(), name="loan_compare"),
    # Detail, Delete & Actions
    path("loans/<int:pk>/", LoanDetailView.as_view(), name="loan_detail"),
    path("loans/<int:pk>/delete/", LoanDeleteView.as_view(), name="delete_loan"),
    # Notes
    path("loans/<int:loan_id>/note/add/", add_note, name="add_note"),
    path(
        "loans/<int:loan_id>/note/<int:note_id>/delete/",
        delete_note,
        name="delete_note",
    ),
    # Documents
    path("loans/<int:loan_id>/docs/upload/", upload_document, name="upload_document"),
    path(
        "loans/<int:loan_id>/docs/<int:doc_id>/delete/",
        delete_document,
        name="delete_document",
    ),
    # Export CSV
    path("loans/<int:loan_id>/export/csv/", export_loan_csv, name="export_csv"),
]
