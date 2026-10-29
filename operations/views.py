import csv
from zipfile import BadZipFile
from openpyxl.utils.exceptions import InvalidFileException
import io
from datetime import date
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Q, Sum, Count
from django.http import HttpResponse, FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from loans.models import Loan, LoanDocument, LoanDisbursement
from loans.utils import generate_full_schedule
from . import models as m, forms as f, services as s
from .access import loans_for, scope, can_operate, application_scope
from .imports import read_rows, validate_rows, commit_rows, LOAN_HEADERS, BANK_HEADERS

SECTIONS = {
    "disbursements": (LoanDisbursement, f.DisbursementForm, None),
    "receipts": (m.Receipt, f.ReceiptForm, None),
    "applications": (m.Application, f.ApplicationForm, None),
    "revisions": (m.LoanRevision, f.RevisionForm, "add_loanrevision"),
    "collections": (m.CollectionFollowup, f.FollowupForm, "add_collectionfollowup"),
    "policies": (m.ChargePolicy, f.PolicyForm, "add_chargepolicy"),
    "charges": (m.Charge, f.ChargeForm, "add_charge"),
    "documents": (LoanDocument, f.DocumentForm, None),
    "parties": (m.RelatedParty, f.RelatedPartyForm, "add_relatedparty"),
    "collateral": (m.Collateral, f.CollateralForm, "add_collateral"),
}


def scoped_queryset(section, user):
    model = SECTIONS[section][0]
    if section == "applications":
        return model.objects.filter(application_scope(user))
    if section == "policies":
        if not user.is_superuser:
            raise PermissionDenied(
                "Only administrators manage global product policies."
            )
        return model.objects.all()
    return model.objects.filter(loan__in=loans_for(user))


@login_required
def home(request):
    loans = loans_for(request.user).select_related("user", "organization", "branch")
    available_orgs = m.Organization.objects.filter(
        pk__in=loans.values("organization_id")
    )
    available_branches = m.Branch.objects.filter(pk__in=loans.values("branch_id"))
    if request.GET.get("organization", "").isdigit():
        loans = loans.filter(organization_id=int(request.GET["organization"]))
    if request.GET.get("branch", "").isdigit():
        loans = loans.filter(branch_id=int(request.GET["branch"]))
    branches = loans.values("organization__name", "branch__name").annotate(
        count=Count("id"),
        sanctioned=Sum("amount"),
        outstanding=Sum("remaining_balance"),
    )
    pending = m.Receipt.objects.filter(loan__in=loans, status="pending").count()
    overdue = []
    for loan in loans.filter(status="active"):
        rows = [r for r in generate_full_schedule(loan) if r["is_overdue"]]
        if rows:
            days = (timezone.localdate() - rows[0]["due_date"]).days
            overdue.append(
                {
                    "loan": loan,
                    "days": days,
                    "bucket": (
                        "1–30"
                        if days <= 30
                        else "31–60" if days <= 60 else "61–90" if days <= 90 else "90+"
                    ),
                    "due": sum(r["total_debit"] for r in rows),
                }
            )
    return render(
        request,
        "operations/home.html",
        {
            "loans": loans,
            "organizations": available_orgs,
            "available_branches": available_branches,
            "branch_summary": branches,
            "pending": pending,
            "overdue": overdue,
            "sections": SECTIONS,
            "staff": request.user.is_staff,
        },
    )


@login_required
def listing(request, section):
    if section not in SECTIONS:
        raise Http404
    queryset = scoped_queryset(section, request.user).order_by("-pk")
    from django.core.paginator import Paginator

    page = Paginator(queryset, 30).get_page(request.GET.get("page"))
    rows = []
    for obj in page:
        fields = []
        for field in obj._meta.concrete_fields:
            if field.name in ("before", "after", "schedule", "proof", "file"):
                continue
            value = getattr(obj, field.name)
            if field.is_relation:
                value = str(value) if value else "—"
            fields.append(
                (field.verbose_name, str(value) if value is not None else "—")
            )
        rows.append({"obj": obj, "fields": fields})
    return render(
        request,
        "operations/list.html",
        {"section": section, "rows": rows, "page": page},
    )


@login_required
def create(request, section):
    if section not in SECTIONS:
        raise Http404
    model, form_class, permission = SECTIONS[section]
    if permission:
        s.permit(request.user, permission)
    if section == "policies" and not request.user.is_superuser:
        raise PermissionDenied
    kwargs = {"data": request.POST or None, "files": request.FILES or None}
    if section == "receipts":
        kwargs["user"] = request.user
    form = form_class(**kwargs)
    allowed = loans_for(request.user)
    if "loan" in form.fields:
        form.fields["loan"].queryset = allowed
        form.fields["loan"].initial = request.GET.get("loan")
    if "kind" in form.fields and request.GET.get("kind"):
        form.fields["kind"].initial = request.GET["kind"]
    for field in ("document", "previous_version"):
        if field in form.fields:
            form.fields[field].queryset = LoanDocument.objects.filter(loan__in=allowed)
    if "agent" in form.fields:
        ids = m.Membership.objects.filter(
            organization_id__in=m.Membership.objects.filter(user=request.user).values(
                "organization_id"
            )
        ).values("user_id")
        form.fields["agent"].queryset = (
            get_user_model().objects.filter(is_active=True, is_staff=True)
            if request.user.is_superuser
            else get_user_model().objects.filter(
                pk__in=ids, is_active=True, is_staff=True
            )
        )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                if permission and hasattr(obj, "loan"):
                    can_operate(request.user, obj.loan)
                if section == "disbursements":
                    obj.requested_by = request.user
                    obj.status = "pending"
                elif section == "receipts":
                    obj.submitted_by = request.user
                elif section == "applications":
                    obj.applicant = request.user
                elif section == "revisions":
                    obj.requested_by = request.user
                elif section in ("policies", "charges", "parties"):
                    obj.created_by = request.user
                if section == "charges":
                    if (
                        obj.policy.loan_type != obj.loan.loan_type
                        or obj.policy.effective_from > timezone.localdate()
                    ):
                        raise ValidationError(
                            "Choose an effective policy for this loan type."
                        )
                    obj.amount = s.money(
                        obj.policy.fixed_amount
                        + obj.loan.remaining_balance * obj.policy.percentage / 100
                    )
                    if obj.amount <= 0:
                        raise ValidationError(
                            "Calculated charge must be greater than zero."
                        )
                if section == "collections":
                    can_operate(obj.agent, obj.loan)
                obj.save()
            messages.success(request, "Record saved.")
            return redirect("operations:list", section=section)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(
                None,
                (
                    "; ".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else "A matching record already exists. Refresh and retry."
                ),
            )
    return render(
        request,
        "operations/form.html",
        {"form": form, "title": "Add " + section, "section": section},
    )


@login_required
@require_POST
def action(request, section, pk):
    reason = request.POST.get("reason", "").strip()
    decision = request.POST.get("decision")
    if decision not in (
        "approve",
        "reject",
        "submit",
        "review",
        "request_reversal",
        "request_waiver",
        "waive",
        "review_document",
        "kyc",
        "request_release",
        "release",
        "resolve",
    ):
        return HttpResponse("Invalid action", status=400)
    try:
        with transaction.atomic():
            obj = get_object_or_404(
                scoped_queryset(section, request.user).select_for_update(), pk=pk
            )
            if decision != "submit" and not reason:
                raise ValidationError("Please provide a reason or review comments.")
            if section == "disbursements":
                can_operate(request.user, obj.loan)
                s.checker(request.user, obj.requested_by_id, "approve_disbursement")
                if obj.status != "pending":
                    raise ValidationError("Disbursement is not pending.")
                if decision not in ("approve", "reject"):
                    raise ValidationError("Unsupported decision.")
                obj.status = "released" if decision == "approve" else "cancelled"
                obj.released_by = request.user
                obj.remarks += "\nReview: " + reason
                obj.save()
            elif section == "receipts":
                can_operate(request.user, obj.loan)
                if decision == "request_reversal":
                    s.permit(request.user, "add_reversal")
                    if (
                        obj.status != "verified"
                        or obj.reversal_requests.filter(status="pending").exists()
                    ):
                        raise ValidationError(
                            "Receipt is not eligible or a request is already pending."
                        )
                    m.Reversal.objects.create(
                        receipt=obj, requested_by=request.user, reason=reason
                    )
                elif decision in ("approve", "reject"):
                    s.verify_receipt(
                        obj.pk, request.user, decision == "approve", reason
                    )
                else:
                    raise ValidationError("Unsupported receipt action.")
            elif section == "applications":
                if decision == "submit":
                    if obj.applicant_id != request.user.pk or obj.status not in (
                        "draft",
                        "rejected",
                    ):
                        raise PermissionDenied
                    obj.status = "submitted"
                    obj.save()
                else:
                    s.review_application(
                        obj.pk,
                        request.user,
                        {
                            "approve": "approved",
                            "reject": "rejected",
                            "review": "review",
                        }.get(decision),
                        reason,
                        bool(request.POST.get("identity_checked")),
                        bool(request.POST.get("income_checked")),
                    )
            elif section == "revisions":
                can_operate(request.user, obj.loan)
                s.review_revision(obj.pk, request.user, decision == "approve")
            elif section == "charges":
                can_operate(request.user, obj.loan)
                if decision == "request_waiver":
                    s.permit(request.user, "change_charge")
                    if obj.status != "due":
                        raise ValidationError("Only unpaid charges can be waived.")
                    obj.waiver_reason, obj.waiver_by = reason, request.user
                    obj.save()
                elif decision == "waive":
                    s.waive_charge(obj.pk, request.user)
                else:
                    raise ValidationError("Unsupported charge action.")
            elif section == "documents":
                can_operate(request.user, obj.loan)
                s.permit(request.user, "add_documentreview")
                if obj.loan.user_id == request.user.pk:
                    raise ValidationError("A separate reviewer must verify documents.")
                if decision == "kyc":
                    if (
                        obj.doc_type != "id_proof"
                        or obj.verification_status != "verified"
                        or (obj.expires_on and obj.expires_on < timezone.localdate())
                    ):
                        raise ValidationError(
                            "KYC needs a verified, unexpired identity document."
                        )
                    s.permit(request.user, "add_kycreview")
                    m.KYCReview.objects.create(
                        user=obj.loan.user,
                        reviewer=request.user,
                        document=obj,
                        approved=True,
                        comments=reason,
                    )
                    profile = obj.loan.user.profile
                    profile.kyc_verified = True
                    profile.save(update_fields=["kyc_verified"])
                else:
                    status = "verified" if decision == "approve" else "rejected"
                    m.DocumentReview.objects.create(
                        document=obj,
                        reviewer=request.user,
                        status=status,
                        comments=reason,
                        expires_on=obj.expires_on,
                    )
                    obj.verification_status = status
                    obj.save(update_fields=["verification_status"])
            elif section == "collateral":
                can_operate(request.user, obj.loan)
                if decision == "request_release":
                    s.permit(request.user, "change_collateral")
                    obj.release_requested_by, obj.release_reason = request.user, reason
                    obj.save()
                elif decision == "release":
                    s.checker(
                        request.user, obj.release_requested_by_id, "release_collateral"
                    )
                    if (
                        not obj.release_requested_by_id
                        or obj.loan.status != "closed"
                        or obj.loan.remaining_balance != 0
                        or obj.loan.charges.filter(status="due").exists()
                    ):
                        raise ValidationError(
                            "Release requires a request and a fully settled closed loan."
                        )
                    obj.released_by, obj.released_at = request.user, timezone.now()
                    obj.save()
            elif section == "collections" and decision == "resolve":
                can_operate(request.user, obj.loan)
                s.permit(request.user, "change_collectionfollowup")
                obj.resolved = True
                obj.notes += "\nResolution: " + reason
                obj.save()
            else:
                raise ValidationError("Unsupported action.")
        messages.success(request, "Action completed.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("operations:list", section=section)


@login_required
def reversals(request):
    items = m.Reversal.objects.filter(
        receipt__loan__in=loans_for(request.user)
    ).select_related("receipt")
    if request.method == "POST":
        obj = get_object_or_404(items, pk=request.POST.get("id"))
        can_operate(request.user, obj.receipt.loan)
        try:
            s.approve_reversal(
                obj.pk, request.user, request.POST.get("decision") == "approve"
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Reversal reviewed.")
        return redirect("operations:reversals")
    return render(
        request, "operations/reversals.html", {"items": items.order_by("-pk")[:100]}
    )


@login_required
def imports(request, kind):
    if kind not in ("loans", "bank"):
        raise Http404
    if kind == "bank":
        s.permit(request.user, "verify_receipt")
    if request.GET.get("template"):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{kind}_import.csv"'
        csv.writer(response).writerow(LOAN_HEADERS if kind == "loans" else BANK_HEADERS)
        return response
    form = f.ImportForm(request.POST or None, request.FILES or None)
    rows = errors = None
    session_key = "import_" + kind
    if request.method == "POST":
        try:
            if request.POST.get("commit"):
                preview = request.session.get(session_key)
                if (
                    not preview
                    or preview["user"] != request.user.pk
                    or timezone.now().timestamp() - preview["at"] > 1800
                ):
                    raise ValidationError("Preview expired. Upload again.")
                count = commit_rows(preview["rows"], kind, request.user)
                request.session.pop(session_key, None)
                messages.success(request, f"Imported {count} rows.")
                return redirect("operations:home")
            elif form.is_valid():
                request.session.pop(session_key, None)
                rows = read_rows(form.cleaned_data["file"])
                errors = validate_rows(rows, kind, request.user)
                if not errors:
                    request.session[session_key] = {
                        "rows": rows,
                        "at": timezone.now().timestamp(),
                        "user": request.user.pk,
                    }
        except (
            ValidationError,
            ValueError,
            IntegrityError,
            BadZipFile,
            InvalidFileException,
            csv.Error,
        ) as exc:
            form.add_error(
                None,
                (
                    "; ".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else "Invalid data or duplicate identifiers. Nothing imported."
                ),
            )
    return render(
        request,
        "operations/import.html",
        {"form": form, "rows": rows, "errors": errors, "kind": kind},
    )


@login_required
def reconciliation(request):
    s.permit(request.user, "verify_receipt")
    receipts = m.Receipt.objects.filter(
        loan__in=loans_for(request.user), status="pending"
    )
    entries = (
        m.BankEntry.objects.all()
        if request.user.is_superuser
        else m.BankEntry.objects.filter(uploaded_by=request.user)
    )
    if request.method == "POST":
        try:
            with transaction.atomic():
                entry = get_object_or_404(
                    entries.select_for_update(),
                    pk=request.POST.get("entry"),
                    receipt__isnull=True,
                )
                receipt = get_object_or_404(receipts, pk=request.POST.get("receipt"))
                can_operate(request.user, receipt.loan)
                if (entry.reference, entry.amount, entry.received_on) != (
                    receipt.reference,
                    receipt.amount,
                    receipt.received_on,
                ):
                    raise ValidationError(
                        "Reference, amount and date must match exactly."
                    )
                s.verify_receipt(
                    receipt.pk, request.user, remarks="Matched to bank entry"
                )
                entry.receipt = receipt
                entry.save()
            messages.success(request, "Bank entry reconciled and receipt verified.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("operations:reconciliation")
    matches = []
    for entry in entries.order_by("-pk")[:100]:
        candidate = (
            receipts.filter(
                reference=entry.reference,
                amount=entry.amount,
                received_on=entry.received_on,
            ).first()
            if not entry.receipt_id
            else None
        )
        matches.append((entry, candidate))
    return render(request, "operations/reconciliation.html", {"matches": matches})


@login_required
def proof(request, pk):
    obj = get_object_or_404(m.Receipt, pk=pk, loan__in=loans_for(request.user))
    if not obj.proof:
        raise Http404
    return FileResponse(
        obj.proof.open("rb"),
        as_attachment=True,
        filename=obj.proof.name.rsplit("/", 1)[-1],
    )


@login_required
def document_pdf(request, kind, pk):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from xml.sax.saxutils import escape

    receipt = None
    if kind == "receipt":
        receipt = get_object_or_404(m.Receipt, pk=pk, loan__in=loans_for(request.user))
        if receipt.status not in ("verified", "reversed"):
            raise Http404
        loan = receipt.loan
        lines = [
            f"Receipt {receipt.number}",
            f"Status: {receipt.status.upper()}",
            f"Amount: INR {receipt.amount}",
            f"Received: {receipt.received_on}",
            f"Reference: {receipt.reference}",
            f"Purpose: {receipt.get_kind_display()}",
        ]
    else:
        loan = get_object_or_404(loans_for(request.user), pk=pk)
        if kind == "noc":
            if (
                loan.status != "closed"
                or loan.remaining_balance != 0
                or not loan.disbursements.filter(status="released").exists()
                or loan.receipts.filter(status="pending").exists()
                or loan.charges.filter(status="due").exists()
            ):
                raise PermissionDenied("NOC requires fully verified settlement.")
            lines = [
                "No Objection Certificate",
                f"Loan closed: {loan.closed_date}",
                "The recorded principal and posted charges are fully settled.",
            ]
        elif kind == "statement":
            lines = [
                "Settlement statement",
                f"Principal outstanding: INR {loan.remaining_balance}",
                f"Interest paid: INR {loan.total_interest_paid}",
                f"Unpaid charges: INR {loan.charges.filter(status='due').aggregate(v=Sum('amount'))['v'] or 0}",
            ]
            for r in loan.receipts.order_by("received_on", "pk"):
                lines.append(
                    f"{r.received_on} | {r.reference} | INR {r.amount} | {r.status}"
                )
        else:
            raise Http404
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    story = [
        Paragraph("NexusLoan", styles["Title"]),
        Paragraph(escape(loan.loan_name), styles["Heading2"]),
        Paragraph(escape(str(loan.user)), styles["Normal"]),
        Spacer(1, 12),
    ]
    story.extend(Paragraph(escape(line), styles["Normal"]) for line in lines)
    SimpleDocTemplate(stream).build(story)
    response = HttpResponse(stream.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{kind}-{pk}.pdf"'
    return response


@login_required
def audit(request):
    s.permit(request.user, "view_auditevent")
    # Full audit contains cross-company metadata and is restricted to superusers.
    if not request.user.is_superuser:
        raise PermissionDenied("Consolidated audit requires administrator access.")
    query = m.AuditEvent.objects.all()
    if request.GET.get("model"):
        query = query.filter(model=request.GET["model"])
    return render(request, "operations/audit.html", {"items": query[:200]})


@login_required
def deliveries(request):
    items = m.Reminder.objects.filter(loan__in=loans_for(request.user)).order_by("-pk")[
        :100
    ]
    return render(request, "operations/deliveries.html", {"items": items})


@login_required
def application_documents(request, pk):
    app = get_object_or_404(
        m.Application.objects.filter(application_scope(request.user)), pk=pk
    )
    form = f.ApplicationDocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if app.applicant_id != request.user.pk or app.status not in (
            "draft",
            "rejected",
        ):
            raise PermissionDenied(
                "Only draft/rejected applications accept new documents."
            )
        if form.is_valid():
            doc = form.save(commit=False)
            doc.application = app
            doc.save()
            messages.success(request, "Application document uploaded.")
            return redirect("operations:application_documents", pk=pk)
    return render(
        request,
        "operations/application_documents.html",
        {"form": form, "application": app},
    )


@login_required
def application_file(request, pk):
    doc = get_object_or_404(
        m.ApplicationDocument,
        pk=pk,
        application__in=m.Application.objects.filter(application_scope(request.user)),
    )
    return FileResponse(
        doc.file.open("rb"),
        as_attachment=True,
        filename=doc.file.name.rsplit("/", 1)[-1],
    )


@login_required
def edit_application(request, pk):
    app = get_object_or_404(
        m.Application, pk=pk, applicant=request.user, status__in=["draft", "rejected"]
    )
    form = f.ApplicationForm(request.POST or None, instance=app)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("operations:list", section="applications")
    return render(
        request, "operations/form.html", {"form": form, "title": "Edit application"}
    )

@login_required
def profile_photo(request, user_id):
    from loans.models import UserProfile
    if request.user.pk != user_id and not request.user.is_superuser:
        raise Http404
    profile=get_object_or_404(UserProfile,user_id=user_id)
    if not profile.photo:
        raise Http404
    return FileResponse(profile.photo.open("rb"),as_attachment=False)
