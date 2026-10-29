from operations.management.commands.process_operations import (
    Command as OperationsCommand,
)


class Command(OperationsCommand):
    help = "Compatibility scheduler: sends due reminders; does NOT debit a bank account or mark payments paid."
