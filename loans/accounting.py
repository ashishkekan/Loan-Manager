"""Released-funds ledger with periodic or Actual/365 reducing-balance projections.

The loan's interest_basis selects the calculation, preserving existing defaults.
Historical paid records are authoritative; projections never overwrite them.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal("0.00")


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def outstanding(loan, as_of=None):
    as_of = as_of or timezone.localdate()
    released = (
        loan.disbursements.filter(
            status="released", disbursement_date__lte=as_of
        ).aggregate(t=Sum("amount"))["t"]
        or ZERO
    )
    principal = (
        loan.payments.filter(status="paid", payment_date__lte=as_of).aggregate(
            t=Sum("principal_component")
        )["t"]
        or ZERO
    )
    prepaid = (
        loan.prepayments.filter(status="paid", prepayment_date__lte=as_of).aggregate(
            t=Sum("amount")
        )["t"]
        or ZERO
    )
    return money(max(ZERO, released - principal - prepaid))


def last_transaction_date(loan):
    dates = [
        loan.payments.filter(status="paid")
        .order_by("-payment_date")
        .values_list("payment_date", flat=True)
        .first(),
        loan.prepayments.filter(status="paid")
        .order_by("-prepayment_date")
        .values_list("prepayment_date", flat=True)
        .first(),
    ]
    return max((d for d in dates if d), default=loan.start_date)


def refresh_balance(loan, close=False):
    loan.remaining_balance = outstanding(loan)
    loan.total_interest_paid = (
        loan.payments.filter(status="paid").aggregate(t=Sum("interest_component"))["t"]
        or ZERO
    )
    # Undisbursed sanction is not debt. A later release can reopen a settled loan.
    if close and loan.remaining_balance == ZERO and loan.total_disbursed_amount > ZERO:
        loan.status = "closed"
        loan.closed_date = timezone.localdate()
    elif loan.remaining_balance > ZERO and loan.status == "closed":
        loan.status = "active"
        loan.closed_date = None
    loan.save(
        update_fields=[
            "remaining_balance",
            "total_interest_paid",
            "status",
            "closed_date",
        ]
    )


def schedule(loan):
    if loan.interest_basis == "actual_365":
        return daily_schedule(loan)
    from loans.utils import add_periods, get_period_details

    today = timezone.localdate()
    _, ppy = get_period_details(loan.emi_frequency)
    rate = loan.interest_rate / Decimal(ppy * 100)
    releases = list(
        loan.disbursements.filter(status="released").order_by("disbursement_date")
    )
    prepayments = list(
        loan.prepayments.filter(status="paid").order_by("prepayment_date")
    )
    paid = {
        p.payment_number: p
        for p in loan.payments.filter(status="paid").order_by("payment_number")
    }
    balance = ZERO
    ri = pi = 0
    result = []
    # Bounded projections even for non-amortizing imported loans.
    for number in range(
        1, max(loan.tenure_years * ppy + 120, max(paid, default=0) + 1) + 1
    ):
        due = add_periods(loan.schedule_start_date, number - 1, loan.emi_frequency)
        while ri < len(releases) and releases[ri].disbursement_date <= due:
            balance += releases[ri].amount
            ri += 1
        while pi < len(prepayments) and prepayments[pi].prepayment_date <= due:
            balance = max(ZERO, balance - prepayments[pi].amount)
            pi += 1
        p = paid.get(number)
        if p:
            balance = max(ZERO, balance - p.principal_component)
            principal, interest, amount = (
                p.principal_component,
                p.interest_component,
                p.amount,
            )
            status = "paid"
        else:
            if balance <= ZERO:
                if ri == len(releases) and number >= max(paid, default=0):
                    break
                continue
            if loan.status == "closed":
                continue
            interest = money(balance * rate)
            principal = min(balance, max(ZERO, loan.emi - interest))
            amount = money(principal + interest)
            balance = money(balance - principal)
            status = "overdue" if due < today else "pending"
        result.append(
            {
                "period": number,
                "loan": loan,
                "payment": p,
                "due_date": due,
                "payment_date": p.payment_date if p else None,
                "status": status,
                "regular_emi": amount,
                "principal": principal,
                "interest": interest,
                "total_debit": amount,
                "balance": p.balance_after if p else balance,
                "payment_mode": p.payment_mode if p else "manual",
                "payment_type": p.payment_type if p else "emi",
                "is_paid": bool(p),
                "is_pending": status == "pending",
                "is_overdue": status == "overdue",
                "is_projected": not bool(p),
            }
        )
        if not p and principal <= ZERO:
            break
    return result


def daily_schedule(loan):
    """Actual/365 simple interest, billed on contractual installment dates.

    Principal reductions take effect on actual payment dates. Paid ledger rows
    remain authoritative. Unpaid interest is not capitalized; no late fees apply.
    Future unpaid installments assume payment on their due dates.
    """
    from loans.utils import add_periods, get_period_details

    today = timezone.localdate()
    _, ppy = get_period_details(loan.emi_frequency)
    releases = list(loan.disbursements.filter(status="released"))
    paid = {p.payment_number: p for p in loan.payments.filter(status="paid")}
    events = [(r.disbursement_date, r.amount) for r in releases]
    events += [(p.payment_date, -p.principal_component) for p in paid.values()
               if p.payment_date]
    events += [(p.prepayment_date, -p.amount)
               for p in loan.prepayments.filter(status="paid")]
    events.sort(key=lambda event: event[0])
    cursor = min([loan.start_date] + [d for d, _ in events])
    balance = ZERO
    index = 0
    result = []
    daily_rate = Decimal(loan.interest_rate) / Decimal("36500")
    for number in range(1, max(loan.tenure_years * ppy + 120, max(paid, default=0) + 1) + 1):
        due = add_periods(loan.schedule_start_date, number - 1, loan.emi_frequency)
        accrued = ZERO
        while index < len(events) and events[index][0] <= due:
            event_date, change = events[index]
            accrued += balance * daily_rate * Decimal((event_date - cursor).days)
            balance = max(ZERO, balance + change)
            cursor = event_date
            index += 1
        accrued += balance * daily_rate * Decimal((due - cursor).days)
        cursor = due
        p = paid.get(number)
        if p:
            principal, interest, amount = p.principal_component, p.interest_component, p.amount
            status = "paid"
        else:
            # Round the installment's total interest to rupees, retaining the
            # two-decimal money representation. Principal is EMI minus interest
            # so the components reconcile exactly with the recorded payment.
            interest = money(accrued.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if balance <= ZERO and interest <= ZERO:
                if index == len(events) and number >= max(paid, default=0):
                    break
                continue
            principal = min(balance, max(ZERO, loan.emi - interest))
            amount = money(principal + interest)
            balance = money(balance - principal)
            status = "overdue" if due < today else "pending"
        result.append({
            "period": number, "loan": loan, "payment": p, "due_date": due,
            "payment_date": p.payment_date if p else None, "status": status,
            "regular_emi": amount, "principal": principal, "interest": interest,
            "total_debit": amount, "balance": p.balance_after if p else balance,
            "payment_mode": p.payment_mode if p else "manual",
            "payment_type": p.payment_type if p else "emi",
            "is_paid": bool(p), "is_pending": status == "pending",
            "is_overdue": status == "overdue", "is_projected": not bool(p),
        })
        if not p and principal <= ZERO and balance > ZERO:
            break
    return result
