"""URL routes for the loans app."""

from django.urls import path

from .views import LoanCreateView, LoanDeleteView, LoanDetailView, LoanListView

urlpatterns = [
    path("loans/", LoanListView.as_view(), name="loan_list"),
    path("loans/create/", LoanCreateView.as_view(), name="create_loan"),
    path("loans/<int:pk>/", LoanDetailView.as_view(), name="loan_detail"),
    path("loans/<int:pk>/delete/", LoanDeleteView.as_view(), name="delete_loan"),
    path("loans/<int:loan_id>/docs/upload/", upload_document, name="upload_document"),
    path(
        "loans/<int:loan_id>/docs/<int:doc_id>/delete/",
        delete_document,
        name="delete_document",
    ),
]
