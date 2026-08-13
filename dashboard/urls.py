"""URL routes for the dashboard app."""

from django.urls import path

from dashboard.views import AdminUsersView, DashboardView

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("users/", AdminUsersView.as_view(), name="admin_users"),
]
