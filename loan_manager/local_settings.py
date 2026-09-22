"""Isolated developer environment. Never reads .env or DATABASE_URL."""

import os
from pathlib import Path

# settings.py honors this switch before loading any environment file.
os.environ["LOAN_LOCAL_ONLY"] = "1"
from .settings import *  # noqa: F403,E402

SECRET_KEY = "local-development-only-not-for-deployment"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "local.sqlite3",
    }
}
MEDIA_ROOT = BASE_DIR / "local_media"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
