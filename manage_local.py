#!/usr/bin/env python
"""Explicit isolated runtime; production .env database configuration is never used."""

import os
import sys

if __name__ == "__main__":
    os.environ["DJANGO_SETTINGS_MODULE"] = "loan_manager.local_settings"
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
