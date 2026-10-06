"""Run against an isolated local database, never the configured deployment DB."""

import os
import sys

if any(arg.startswith("--settings") for arg in sys.argv):
    raise SystemExit("Settings overrides are disabled for the isolated runner.")
os.environ["LOAN_LOCAL_MODE"] = "1"
os.environ["DJANGO_SETTINGS_MODULE"] = "loan_manager.local_settings"
from django.core.management import execute_from_command_line

execute_from_command_line(sys.argv)
