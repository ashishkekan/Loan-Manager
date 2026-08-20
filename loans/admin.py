from django.contrib import admin
from django.http import HttpResponse

from loans.models import (
    Investment,
    Loan,
    LoanAccruedInterest,
    LoanDisbursement,
    LoanDocument,
    LoanNote,
    SupportMessage,
    SupportTicket,
)


class LoanDisbursementInline(admin.TabularInline):
    model = LoanDisbursement
    extra = 0
    fields = ("disbursement_number", "disbursement_date", "amount", "purpose", "status")
    ordering = ("disbursement_number",)
    readonly_fields = ("disbursement_number",)


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = (
        "loan_name",
        "user",
        "loan_type",
        "amount",
        "interest_rate",
        "status",
        "is_public",
        "created_at",
    )
    list_filter = ("status", "loan_type", "is_public", "created_at")
    search_fields = ("loan_name", "user__username", "user__email")
    fieldsets = (
        (
            "Loan Information",
            {
                "fields": (
                    "user",
                    "loan_name",
                    "loan_type",
                    "amount",
                    "interest_rate",
                    "tenure_years",
                    "emi",
                    "start_date",
                    "first_emi_date",
                    "status",
                )
            },
        ),
        (
            "Current Status",
            {
                "fields": (
                    "remaining_balance",
                    "total_interest_paid",
                )
            },
        ),
    )
    date_hierarchy = "created_at"

    # Admin Action: Export to CSV
    actions = ["export_to_csv"]
    inlines = [LoanDisbursementInline]

    def export_to_csv(self, request, queryset):
        import csv

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=loans_report.csv"
        writer = csv.writer(response)
        writer.writerow(["Name", "Type", "Amount", "Rate", "Tenure", "Status"])
        for loan in queryset:
            writer.writerow(
                [
                    loan.loan_name,
                    loan.get_loan_type_display(),
                    loan.amount,
                    loan.interest_rate,
                    f"{loan.tenure_years}y",
                    loan.status,
                ]
            )
        return response

    export_to_csv.short_description = "Export Selected to CSV"


@admin.register(LoanDocument)
class LoanDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "loan", "doc_type", "uploaded_at")
    list_filter = ("doc_type",)


@admin.register(Investment)
class InvestmentAdmin(admin.ModelAdmin):
    list_display = ("loan", "lender", "amount", "status", "created_at")
    list_filter = ("status", "created_at")


@admin.register(LoanNote)
class LoanNoteAdmin(admin.ModelAdmin):
    list_display = ("loan", "content_preview", "created_at")

    def content_preview(self, obj):
        return obj.content[:50] + "..." if len(obj.content) > 50 else obj.content


@admin.register(LoanDisbursement)
class LoanDisbursementAdmin(admin.ModelAdmin):
    list_display = (
        "loan",
        "disbursement_number",
        "disbursement_date",
        "amount",
        "purpose",
        "status",
    )
    list_filter = ("status", "purpose", "disbursement_date")
    search_fields = ("loan__loan_name", "loan__user__username")
    ordering = ("loan", "disbursement_number")
    list_display = (
        "loan",
        "disbursement_number",
        "disbursement_date",
        "amount",
        "purpose",
        "status",
        "is_interest_processed",
    )


@admin.register(LoanAccruedInterest)
class LoanAccruedInterestAdmin(admin.ModelAdmin):
    list_display = (
        "loan",
        "disbursement",
        "emi_date",
        "interest_amount",
        "status",
        "recovered_on",
    )
    list_filter = ("status", "emi_date")
    search_fields = ("loan__loan_name", "loan__user__username")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-emi_date",)
    date_hierarchy = "emi_date"
    list_select_related = ("loan", "disbursement")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = [
        "ticket_number",
        "user",
        "category",
        "subject",
        "status",
        "priority",
        "created_at",
    ]
    list_filter = [
        "status",
        "category",
        "priority",
    ]
    search_fields = [
        "ticket_number",
        "subject",
        "message",
        "user__username",
    ]
    readonly_fields = [
        "ticket_number",
        "created_at",
        "updated_at",
    ]


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = [
        "ticket",
        "user",
        "is_staff_reply",
        "created_at",
    ]
    list_filter = ["is_staff_reply"]
    search_fields = [
        "ticket__ticket_number",
        "message",
    ]
