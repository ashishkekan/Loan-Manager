#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -x ../venv/bin/python ]]; then
  exec ../venv/bin/python manage_local.py "$@"
fi
exec python3 manage_local.py "$@"
