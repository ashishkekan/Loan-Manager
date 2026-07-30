@echo off

cd /d D:\LoanManager

call venv\Scripts\activate.bat

python manage.py process_auto_debits
