"""Development/test isolation. No .env or deployment database is used."""

import os

os.environ["LOAN_LOCAL_MODE"] = "1"
from .settings import *

SECRET_KEY = "local-development-only-not-for-deployment"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "local.sqlite3",
        "TEST": {"NAME": ":memory:"},
    }
}
MEDIA_ROOT = BASE_DIR / "local_media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
