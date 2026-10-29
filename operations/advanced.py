from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from loans.models import Investment
from . import models as m
from .forms import PlannerForm, TransferForm
from .services import money, amortize, permit, checker
from .access import loans_for, can_operate

ROLE_PERMISSIONS = {
    "Loan Officer": [
        "add_application",
        "add_loanrevision",
        "add_collectionfollowup",
        "change_collectionfollowup",
        "add_documentreview",
        "add_kycreview",
        "add_relatedparty",
        "add_collateral",
        "change_collateral",
    ],
    "Approver": [
        "approve_application",
        "approve_disbursement",
        "approve_revision",
        "approve_reversal",
        "approve_waiver",
        "release_collateral",
        "approve_payout",
    ],
    "Accountant": [
        "verify_receipt",
        "add_reversal",
        "add_charge",
        "change_charge",
        "add_payout",
        "change_payout",
    ],
    "Collection Agent": ["add_collectionfollowup", "change_collectionfollowup"],
}


def provision_roles():
    for name, codes in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(
            Permission.objects.filter(
                content_type__app_label="operations", codename__in=codes
            )
        )


class TeamForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=get_user_model().objects.filter(is_active=True)
    )
    organization = forms.ModelChoiceField(
        queryset=m.Organization.objects.filter(active=True)
    )
    branch = forms.ModelChoiceField(queryset=m.Branch.objects.all(), required=False)
    role = forms.ChoiceField(choices=[(r, r) for r in ROLE_PERMISSIONS])

    def clean(self):
        data = super().clean()
        if data.get("branch") and data["branch"].organization != data.get(
            "organization"
        ):
            raise forms.ValidationError("Branch belongs to another company.")
        return data


@login_required
def team(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    form = TeamForm(request.POST or None)
    if request.method == "POST":
        try:
            with transaction.atomic():
                if request.POST.get("action") == "organization":
                    name = request.POST.get("name", "").strip()
                    if not name or len(name) > 160:
                        raise ValidationError(
                            "Enter a company name of at most 160 characters."
                        )
                    m.Organization.objects.get_or_create(name=name)
                elif request.POST.get("action") == "branch":
                    org = get_object_or_404(
                        m.Organization, pk=request.POST.get("organization")
                    )
                    name = request.POST.get("name", "").strip()
                    if not name or len(name) > 160:
                        raise ValidationError(
                            "Enter a branch name of at most 160 characters."
                        )
                    m.Branch.objects.get_or_create(organization=org, name=name)
                elif form.is_valid():
                    provision_roles()
                    data = form.cleaned_data
                    user = data["user"]
                    user.is_staff = True
                    user.save(update_fields=["is_staff"])
                    # Replace only managed operational roles; preserve unrelated groups.
                    user.groups.remove(*Group.objects.filter(name__in=ROLE_PERMISSIONS))
                    user.groups.add(Group.objects.get(name=data["role"]))
                    m.Membership.objects.update_or_create(
                        user=user,
                        organization=data["organization"],
                        defaults={"branch": data["branch"]},
                    )
                    m.AuditEvent.objects.create(
                        actor=request.user,
                        model="operations.membership",
                        object_id=str(user.pk),
                        action="assign",
                        after={
                            "role": data["role"],
                            "organization": str(data["organization"]),
                            "branch": str(data["branch"]),
                        },
                    )
                else:
                    return render(
                        request,
                        "operations/team.html",
                        {"form": form, "organizations": m.Organization.objects.all()},
                    )
            messages.success(request, "Team configuration saved.")
            return redirect("operations:team")
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
    return render(
        request,
        "operations/team.html",
        {
            "form": form,
            "organizations": m.Organization.objects.all(),
            "members": m.Membership.objects.select_related(
                "user", "organization", "branch"
            ),
        },
    )


def simulate_debts(loans, extra, strategy):
    from loans.utils import get_period_details

    debts = [
        {
            "id": x.pk,
            "name": x.loan_name,
            "balance": x.remaining_balance,
            "rate": x.interest_rate / Decimal(1200),
            "minimum": money(x.emi / Decimal(get_period_details(x.emi_frequency)[0])),
            "paid_off": None,
        }
        for x in loans
    ]
    monthly_budget = sum(x["minimum"] for x in debts) + extra
    interest_total = Decimal(0)
    for month in range(1, 1201):
        if not any(x["balance"] > 0 for x in debts):
            return {
                "months": month - 1,
                "interest": money(interest_total),
                "loans": debts,
            }
        available = monthly_budget
        for debt in debts:
            if debt["balance"] <= 0:
                continue
            interest = money(debt["balance"] * debt["rate"])
            interest_total += interest
            debt["balance"] += interest
            payment = min(debt["minimum"], debt["balance"], available)
            debt["balance"] -= payment
            available -= payment
            if debt["balance"] == 0:
                debt["paid_off"] = month
        ordered = sorted(
            (x for x in debts if x["balance"] > 0),
            key=(
                (lambda x: -x["rate"])
                if strategy == "avalanche"
                else (lambda x: x["balance"])
            ),
        )
        for debt in ordered:
            payment = min(debt["balance"], available)
            debt["balance"] -= payment
            available -= payment
            if debt["balance"] == 0:
                debt["paid_off"] = month
    raise ValidationError(
        "Monthly budget does not repay the loans within 100 years. Increase the budget."
    )


@login_required
def planner(request):
    form = PlannerForm(request.POST or None)
    result = None
    loans = loans_for(request.user).filter(
        user=request.user, status="active", remaining_balance__gt=0
    )
    if request.method == "POST" and form.is_valid():
        try:
            if not loans.exists():
                raise ValidationError("Add a funded active loan first.")
            result = simulate_debts(
                loans, form.cleaned_data["budget"], form.cleaned_data["strategy"]
            )
            baseline = simulate_debts(loans, Decimal(0), form.cleaned_data["strategy"])
            result["months_saved"] = baseline["months"] - result["months"]
            result["interest_saved"] = money(baseline["interest"] - result["interest"])
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
    return render(request, "operations/planner.html", {"form": form, "result": result})


@login_required
def transfer(request):
    form = TransferForm(request.POST or None)
    result = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        current = amortize(d["balance"], d["current_rate"], d["months"], "monthly")
        proposed = amortize(d["balance"], d["proposed_rate"], d["months"], "monthly")
        saving = current - proposed
        from decimal import ROUND_CEILING

        result = {
            "current": current,
            "proposed": proposed,
            "net_saving": money(saving * d["months"] - d["transfer_fees"]),
            "break_even": (
                int(
                    (d["transfer_fees"] / saving).to_integral_value(
                        rounding=ROUND_CEILING
                    )
                )
                if saving > 0
                else None
            ),
            "monthly_saving": saving,
        }
    return render(request, "operations/transfer.html", {"form": form, "result": result})


def allocate_receipt(receipt):
    if receipt.kind == "charge":
        return
    from decimal import ROUND_DOWN

    if receipt.kind == "prepayment":
        principal, interest = receipt.amount, Decimal(0)
    else:
        before = receipt.before.get("payment") or {}
        principal = receipt.payment.principal_component - Decimal(
            before.get("principal_component", "0")
        )
        interest = receipt.payment.interest_component - Decimal(
            before.get("interest_component", "0")
        )
    # Allocate the funded share only; unfunded principal remains with the originator.
    investments = list(Investment.objects.filter(loan=receipt.loan).order_by("id"))
    denominator = receipt.loan.amount
    if denominator <= 0:
        return
    for inv in investments:
        ratio = inv.amount / denominator
        m.LenderAllocation.objects.get_or_create(
            receipt=receipt,
            investment=inv,
            defaults={
                "principal": (principal * ratio).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                ),
                "interest": (interest * ratio).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                ),
            },
        )


@login_required
def portfolio(request):
    investments = Investment.objects.filter(lender=request.user).select_related("loan")
    allocations = m.LenderAllocation.objects.filter(
        investment__lender=request.user, receipt__status="verified"
    ).select_related("investment__loan", "receipt")
    staff_allocations = (
        m.LenderAllocation.objects.filter(
            investment__loan__in=loans_for(request.user), receipt__status="verified"
        ).select_related("investment__loan", "receipt")
        if request.user.is_staff
        else allocations
    )
    if request.method == "POST":
        try:
            with transaction.atomic():
                allocation = get_object_or_404(
                    staff_allocations, pk=request.POST.get("allocation")
                )
                can_operate(request.user, allocation.investment.loan)
                action = request.POST.get("action")
                if action == "request":
                    permit(request.user, "add_payout")
                    m.Payout.objects.get_or_create(
                        allocation=allocation, defaults={"requested_by": request.user}
                    )
                elif action == "approve":
                    payout = get_object_or_404(
                        m.Payout.objects.select_for_update(),
                        allocation=allocation,
                        status="pending",
                    )
                    checker(request.user, payout.requested_by_id, "approve_payout")
                    payout.status, payout.approved_by = "approved", request.user
                    payout.save()
                elif action == "paid":
                    permit(request.user, "change_payout")
                    payout = get_object_or_404(
                        m.Payout.objects.select_for_update(),
                        allocation=allocation,
                        status="approved",
                    )
                    reference = request.POST.get("reference", "").strip()
                    if not reference or len(reference) > 120:
                        raise ValidationError(
                            "Enter the external bank transfer reference."
                        )
                    payout.reference, payout.status, payout.paid_at = (
                        reference,
                        "paid",
                        timezone.now(),
                    )
                    payout.save()
                else:
                    raise ValidationError("Unsupported payout action.")
            messages.success(
                request, "Payout record updated. This action does not transfer money."
            )
        except (ValidationError, IntegrityError) as exc:
            messages.error(
                request,
                (
                    "; ".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else "That bank reference is already recorded."
                ),
            )
        return redirect("operations:portfolio")
    received = allocations.aggregate(
        principal=Sum("principal"), interest=Sum("interest")
    )
    return render(
        request,
        "operations/portfolio.html",
        {
            "investments": investments,
            "allocations": staff_allocations[:100],
            "received": received,
        },
    )


@login_required
def onboarding(request):
    from .forms import validate_upload

    class OnboardingForm(forms.ModelForm):
        class Meta:
            model = m.Onboarding
            fields = ["kind", "amount", "reference", "proof"]

        def clean(self):
            data = super().clean()
            if data.get("proof"):
                validate_upload(data["proof"])
            if data.get("kind") == "funds" and (
                not data.get("amount") or data["amount"] <= 0
            ):
                raise forms.ValidationError("Deposit amount must be positive.")
            if data.get("kind") == "identity" and data.get("amount") != 0:
                raise forms.ValidationError(
                    "Identity verification has no deposit amount; enter zero."
                )
            return data

    form = OnboardingForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        try:
            if request.POST.get("review"):
                if not request.user.is_superuser:
                    raise PermissionDenied
                with transaction.atomic():
                    item = get_object_or_404(
                        m.Onboarding.objects.select_for_update(),
                        pk=request.POST["review"],
                        status="pending",
                    )
                    if item.user_id == request.user.pk:
                        raise ValidationError(
                            "A separate administrator must review the submission."
                        )
                    comments = request.POST.get("comments", "").strip()
                    if not comments:
                        raise ValidationError("Review comments are required.")
                    decision = request.POST.get("decision")
                    if decision not in ("approve", "reject"):
                        raise ValidationError("Invalid decision.")
                    item.status = "verified" if decision == "approve" else "rejected"
                    item.reviewer, item.comments = request.user, comments
                    item.save()
                    if decision == "approve":
                        from accounts.models import Profile

                        profile = Profile.objects.select_for_update().get(
                            user=item.user
                        )
                        if item.kind == "identity":
                            profile.kyc_verified = True
                        else:
                            profile.available_funds += item.amount
                        profile.save()
                messages.success(request, "Submission reviewed.")
                return redirect("operations:onboarding")
            elif form.is_valid():
                item = form.save(commit=False)
                item.user = request.user
                item.save()
                messages.success(request, "Submitted for independent verification.")
                return redirect("operations:onboarding")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    items = (
        m.Onboarding.objects.all()
        if request.user.is_superuser
        else m.Onboarding.objects.filter(user=request.user)
    )
    return render(
        request,
        "operations/onboarding.html",
        {"form": form, "items": items.order_by("-pk")[:100]},
    )


@login_required
def onboarding_proof(request, pk):
    from django.http import FileResponse

    query = (
        m.Onboarding.objects.all()
        if request.user.is_superuser
        else m.Onboarding.objects.filter(user=request.user)
    )
    item = get_object_or_404(query, pk=pk)
    return FileResponse(
        item.proof.open("rb"),
        as_attachment=True,
        filename=item.proof.name.rsplit("/", 1)[-1],
    )
