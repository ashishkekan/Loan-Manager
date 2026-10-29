"""URL routes for the accounts app."""

from django.urls import path

from accounts.views import CustomLoginView, register_view

urlpatterns = [
    path("accounts/login/", CustomLoginView.as_view(), name="login"),
    path("accounts/register/", register_view, name="register"),
]

from django.contrib.auth import views as auth_views
from operations.security import SecureLoginView

urlpatterns = [p for p in urlpatterns if p.name != "login"] + [
    path("accounts/login/", SecureLoginView.as_view(), name="login"),
    path(
        "accounts/password/reset/",
        auth_views.PasswordResetView.as_view(template_name="registration/form.html"),
        name="password_reset",
    ),
    path(
        "accounts/password/reset/done/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "accounts/password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/form.html"
        ),
        name="password_reset_confirm",
    ),
    path(
        "accounts/password/reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
]
