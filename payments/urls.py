"""URL routes for the payments app."""

from django.urls import path
from .views import pay_emi, make_prepayment, EMIScheduleView

urlpatterns = [
    path('loans/<int:loan_id>/pay-emi/', pay_emi, name='pay_emi'),
    path('loans/<int:loan_id>/prepay/', make_prepayment, name='make_prepayment'),
    path('loans/<int:loan_id>/schedule/', EMIScheduleView.as_view(), name='emi_schedule'),
]
