from django.urls import path

from marketplace.views import MarketplaceView, SetupProfileView, invest_in_loan

urlpatterns = [
    path("profile/setup/", SetupProfileView.as_view(), name="setup_profile"),
    path("marketplace/", MarketplaceView.as_view(), name="marketplace"),
    path("loans/<int:loan_id>/invest/", invest_in_loan, name="invest_in_loan"),
]
