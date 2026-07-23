"""URL routes for the loans app."""

from django.urls import path
from .views import LoanListView, LoanCreateView, LoanDetailView, LoanDeleteView

urlpatterns = [
    path('loans/', LoanListView.as_view(), name='loan_list'),
    path('loans/create/', LoanCreateView.as_view(), name='create_loan'),
    path('loans/<int:pk>/', LoanDetailView.as_view(), name='loan_detail'),
    path('loans/<int:pk>/delete/', LoanDeleteView.as_view(), name='delete_loan'),
]
