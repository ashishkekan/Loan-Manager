#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

BASE_DIR="$(dirname "$PROJECT_DIR")"

LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

if [ -f "$BASE_DIR/venv/bin/activate" ]; then
    source "$BASE_DIR/venv/bin/activate"
fi

python manage.py process_auto_debits | tee -a "$LOG_DIR/auto_debit.log"
