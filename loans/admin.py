from django.contrib import admin

from .models import Investment, Loan, LoanDocument


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
    readonly_fields = (
        "emi",
        "remaining_balance",
        "total_interest_paid",
        "funded_amount",
    )
    date_hierarchy = "created_at"

    # Admin Action: Export to CSV
    actions = ["export_to_csv"]

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
