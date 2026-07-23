"""URL routes for the accounts app."""

from django.urls import path
from .views import CustomLoginView, register_view

urlpatterns = [
    path('accounts/login/', CustomLoginView.as_view(), name='login'),
    path('accounts/register/', register_view, name='register'),
]
