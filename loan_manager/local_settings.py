"""Isolated local review runtime; never reads DATABASE_URL or the existing databases."""

import os
from pathlib import Path

from .settings import *

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "operations-demo.sqlite3",
    }
}
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
SECRET_KEY = "local-review-runtime-not-for-deployment"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
MEDIA_ROOT = BASE_DIR / "operations-demo-media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
PAYMENT_ADAPTER = ""
PAYMENT_RECURRING_ENABLED = False
SMS_ADAPTER = ""
# Keep review cookies separate from other Django apps running on localhost.
SESSION_COOKIE_NAME = "nexusloan_review_session"
CSRF_COOKIE_NAME = "nexusloan_review_csrf"
