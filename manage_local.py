"""Run management commands against the isolated local database only."""

import os
import sys

if __name__ == "__main__":
    if any(
        arg == "--settings" or arg.startswith("--settings=") for arg in sys.argv[1:]
    ):
        raise SystemExit(
            "manage_local.py does not allow overriding its isolated settings."
        )
    os.environ["DJANGO_SETTINGS_MODULE"] = "loan_manager.local_settings"
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
