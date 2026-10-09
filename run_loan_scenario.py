"""Migrate, seed and serve one disposable RAM database in the same process."""
import os
import sqlite3

os.environ["LOAN_LOCAL_MODE"] = "1"
os.environ["DJANGO_SETTINGS_MODULE"] = "loan_manager.scenario_settings"
import django
django.setup()
from django.conf import settings
from django.core.management import call_command

# Keep the shared memory DB alive when request connections close.
keeper = sqlite3.connect(settings.DATABASES["default"]["NAME"], uri=True)
call_command("migrate", interactive=False, verbosity=0)
call_command("seed_july_scenario")
call_command("runserver", "127.0.0.1:8012", use_reloader=False, use_threading=False)
