"""Whole-rupee presentation; stored ledger values retain their precision."""
from decimal import Decimal, ROUND_HALF_UP


def rounded_rupees(value):
    """Round a monetary value to whole rupees, with halves away from zero."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_rupees(value):
    return f"{rounded_rupees(value):,}"
