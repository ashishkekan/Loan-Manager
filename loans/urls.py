"""URL routes for the loans app."""

from django.urls import path

from loans.report_views import admin_reports, export_admin_report
from loans.views import (
    LoanCompareView,
    LoanCreateView,
    LoanDeleteView,
    LoanDetailView,
    LoanDisbursementCreateView,
    LoanDisbursementDeleteView,
    LoanDisbursementDetailView,
    LoanDisbursementListView,
    LoanDisbursementUpdateView,
    LoanListView,
    LoanUpdateView,
    activity_logs_dashboard,
    add_bank_account,
    add_note,
    admin_banks,
    close_loan,
    create_support_ticket,
    delete_bank_account,
    delete_document,
    delete_note,
    documents_dashboard,
    download_document,
    export_loan_csv,
    mark_all_notifications_read,
    mark_notification_read,
    notifications_dashboard,
    set_default_bank_account,
    settings_dashboard,
    support_dashboard,
    support_ticket_detail,
    update_appearance_preferences,
    update_notification_preferences,
    update_password,
    update_privacy_settings,
    update_profile,
    upload_document,
    view_document,
)

urlpatterns = [
    path("loans/", LoanListView.as_view(), name="loan_list"),
    path("loans/create/", LoanCreateView.as_view(), name="create_loan"),
    path("loans/compare/", LoanCompareView.as_view(), name="loan_compare"),
    path("loans/<int:pk>/", LoanDetailView.as_view(), name="loan_detail"),
    path("loans/<int:pk>/delete/", LoanDeleteView.as_view(), name="delete_loan"),
    path("loan/<int:pk>/edit/", LoanUpdateView.as_view(), name="edit_loan"),
    path("loan/<int:pk>/close/", close_loan, name="close_loan"),
    path("loans/<int:loan_id>/export/csv/", export_loan_csv, name="export_csv"),
    path("loans/<int:loan_id>/note/add/", add_note, name="add_note"),
    path(
        "loans/<int:loan_id>/note/<int:note_id>/delete/",
        delete_note,
        name="delete_note",
    ),
    path(
        "loans/<int:loan_id>/documents/upload/", upload_document, name="upload_document"
    ),
    path(
        "loans/<int:loan_id>/documents/<int:doc_id>/delete/",
        delete_document,
        name="delete_document",
    ),
    path("documents/", documents_dashboard, name="documents_dashboard"),
    path("documents/upload/", upload_document, name="document_upload"),
    path("documents/<int:document_id>/view/", view_document, name="view_document"),
    path(
        "documents/<int:document_id>/download/",
        download_document,
        name="download_document",
    ),
    path(
        "documents/<int:document_id>/delete/", delete_document, name="document_delete"
    ),
    path(
        "loans/<int:loan_id>/disbursements/",
        LoanDisbursementListView.as_view(),
        name="loan_disbursement_list",
    ),
    path(
        "disbursement/<int:pk>/",
        LoanDisbursementDetailView.as_view(),
        name="loan_disbursement_detail",
    ),
    path(
        "loans/<int:loan_id>/disbursement/create/",
        LoanDisbursementCreateView.as_view(),
        name="create_disbursement",
    ),
    path(
        "disbursement/<int:pk>/edit/",
        LoanDisbursementUpdateView.as_view(),
        name="edit_disbursement",
    ),
    path(
        "disbursement/<int:pk>/delete/",
        LoanDisbursementDeleteView.as_view(),
        name="delete_disbursement",
    ),
    path("notifications/", notifications_dashboard, name="notifications_dashboard"),
    path(
        "<int:notification_id>/read/",
        mark_notification_read,
        name="mark_notification_read",
    ),
    path(
        "mark-all-read/",
        mark_all_notifications_read,
        name="mark_all_notifications_read",
    ),
    path("support/", support_dashboard, name="support_dashboard"),
    path("support/create/", create_support_ticket, name="create_support_ticket"),
    path(
        "support/ticket/<int:ticket_id>/",
        support_ticket_detail,
        name="support_ticket_detail",
    ),
    path("settings/", settings_dashboard, name="settings_dashboard"),
    path("settings/profile/update/", update_profile, name="update_profile"),
    path("settings/password/update/", update_password, name="update_password"),
    path(
        "settings/notifications/update/",
        update_notification_preferences,
        name="update_notification_preferences",
    ),
    path(
        "settings/appearance/update/",
        update_appearance_preferences,
        name="update_appearance_preferences",
    ),
    path(
        "settings/privacy/update/",
        update_privacy_settings,
        name="update_privacy_settings",
    ),
    path("settings/banks/add/", add_bank_account, name="add_bank_account"),
    path(
        "settings/banks/<int:pk>/delete/",
        delete_bank_account,
        name="delete_bank_account",
    ),
    path(
        "settings/banks/<int:pk>/default/",
        set_default_bank_account,
        name="set_default_bank_account",
    ),
    path("banks/", admin_banks, name="admin_banks"),
    path(
        "activity-logs/",
        activity_logs_dashboard,
        name="activity_logs_dashboard",
    ),
    path("reports/", admin_reports, name="admin_reports"),
    path(
        "reports/export/<str:report_type>/<str:format>/",
        export_admin_report,
        name="export_admin_report",
    ),
]
