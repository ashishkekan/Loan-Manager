# NexusLoan

Django loan tracking and operations application. New workflows are under **Loan Operations** (`/operations/`).

- [Feature guide, workflows and rollout notes](docs/OPERATIONS.md)
- [Payment provider integration contract](docs/PROVIDER_CONTRACT.md)

## Isolated local setup

From this directory:

```bash
../venv/bin/python manage_local.py migrate
../venv/bin/python manage_local.py createsuperuser
../venv/bin/python manage_local.py runserver 127.0.0.1:8029
```

The local runner uses a separate `operations-demo.sqlite3` and console email. Existing databases are not used. Real SMS/payment adapters are disabled locally.

## Validation

```bash
../venv/bin/python manage.py test --settings=loan_manager.test_settings --noinput
../venv/bin/python manage.py makemigrations --check --dry-run --settings=loan_manager.test_settings
```

## Scheduler

Schedule `python manage.py process_operations` in the deployment environment. The old `process_auto_debits` command is a compatibility alias; it no longer fabricates paid transactions. Provider-confirmed recurring collection is separately configured and opt-in. See the feature guide before enabling it.
