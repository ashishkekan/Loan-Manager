@echo off

cd /d C:\Users\Smart Computer\Desktop\Management\Loan-Manager

call venv\Scripts\activate.bat

python manage.py process_auto_debits
