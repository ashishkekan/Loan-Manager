# Auto Debit Scheduler Setup

This project supports automatic EMI debit using platform-specific schedulers.

- Ubuntu/Linux → Cron
- Windows → Task Scheduler

The scheduler automatically runs the following Django management command:

```bash
python manage.py process_auto_debits
```

---

# Project Structure

```
loan_manager/
│
├── logs/
│   └── auto_debit.log
│
├── scripts/
│   ├── scheduler.py
│   ├── ubuntu_auto_debit.sh
│   ├── windows_auto_debit.bat
│   └── auto_debit.cron
│
└── manage.py
```

---

# Configuration

The scheduler can be selected using an environment variable.

## Ubuntu

```bash
export LOAN_SERVER=ubuntu
```

To make it permanent:

```bash
echo "export LOAN_SERVER=ubuntu" >> ~/.bashrc
source ~/.bashrc
```

---

## Windows

Command Prompt

```cmd
set LOAN_SERVER=windows
```

Or create a System Environment Variable

```
LOAN_SERVER=windows
```

---

# Automatic Detection

If `LOAN_SERVER` is not configured, `scripts/scheduler.py` automatically detects the operating system.

| Platform | Selected Scheduler |
|----------|--------------------|
| Windows | windows_auto_debit.bat |
| Linux / Ubuntu | ubuntu_auto_debit.sh |

---

# Ubuntu Setup

Make the shell script executable.

```bash
chmod +x scripts/ubuntu_auto_debit.sh
```

Create the logs directory.

```bash
mkdir -p logs
```

---

# Install Cron Job

Open cron.

```bash
crontab -e
```

Add the following line.

```cron
0 17 * * * /home/ubuntu/Documents/ZIPS/Product/Loan/loan_manager/venv/bin/python /home/ubuntu/Documents/ZIPS/Product/Loan/loan_manager/scripts/scheduler.py
```

This executes the scheduler every day at **5:00 PM IST**.

---

# Verify Cron

```bash
crontab -l
```

---

# Windows Task Scheduler

Create a new task.

### Program

```
python.exe
```

### Arguments

```
D:\LoanManager\scripts\scheduler.py
```

### Trigger

```
Daily
5:00 PM
```

---

# Manual Execution

Ubuntu

```bash
python scripts/scheduler.py
```

Windows

```cmd
python scripts\scheduler.py
```

---

# Manual Auto Debit

Run directly.

```bash
python manage.py process_auto_debits
```

---

# Logs

Ubuntu

```
logs/auto_debit.log
```

Example

```
==================================================
Started : Thu Jul 30 17:00:00 IST 2026

Processed : 5
Skipped : 2
Failed : 0

Completed : Thu Jul 30 17:00:03 IST 2026
```

---

# Changing Scheduler Time

Ubuntu

Edit the cron entry.

Example:

```
0 17 * * *   → 5:00 PM
30 18 * * *  → 6:30 PM
0 20 * * *   → 8:00 PM
```

Windows

Update the trigger time in Task Scheduler.

---

# Testing

Run the scheduler manually.

```bash
python scripts/scheduler.py
```

Expected output:

```
Running scheduler for : ubuntu
```

or

```
Running scheduler for : windows
```

---

# Notes

- Supports Monthly, Quarterly, Half-Yearly and Yearly EMI frequencies.
- Uses the loan's `first_emi_date` as the schedule start date.
- Auto Debit processes all due EMIs.
- Existing loans remain backward compatible.
- If the server is offline at the scheduled time, the next execution processes any missed due EMIs according to the implemented catch-up logic.
