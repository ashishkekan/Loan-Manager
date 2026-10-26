"""Disposable shared-memory SQLite workspace, never a disk database."""
from .local_settings import *
import tempfile

DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": "file:loan_july_scenario?mode=memory&cache=shared",
    "OPTIONS": {"uri": True},
    "TEST": {"NAME": ":memory:"},
}}
MEDIA_ROOT = tempfile.mkdtemp(prefix="loan-july-media-")
SESSION_COOKIE_NAME = "loan_july_session"
CSRF_COOKIE_NAME = "loan_july_csrf"
