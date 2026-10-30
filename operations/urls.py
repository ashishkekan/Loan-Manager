from django.urls import path

from . import advanced, appearance, gateway, security, views

app_name = "operations"
urlpatterns = [
    path("appearance/", appearance.appearance_view, name="appearance"),
    path("profile-photo/<int:user_id>/", views.profile_photo, name="profile_photo"),
    path("onboarding/", advanced.onboarding, name="onboarding"),
    path(
        "onboarding/<int:pk>/proof/", advanced.onboarding_proof, name="onboarding_proof"
    ),
    path("", views.home, name="home"),
    path("security/", security.security_view, name="security"),
    path("challenge/", security.challenge_view, name="challenge"),
    path("audit/", views.audit, name="audit"),
    path("deliveries/", views.deliveries, name="deliveries"),
    path("reversals/", views.reversals, name="reversals"),
    path("import/<str:kind>/", views.imports, name="import"),
    path("reconciliation/", views.reconciliation, name="reconciliation"),
    path("proof/<int:pk>/", views.proof, name="proof"),
    path("pdf/<str:kind>/<int:pk>/", views.document_pdf, name="pdf"),
    path(
        "applications/<int:pk>/documents/",
        views.application_documents,
        name="application_documents",
    ),
    path(
        "applications/<int:pk>/edit/", views.edit_application, name="edit_application"
    ),
    path(
        "application-files/<int:pk>/", views.application_file, name="application_file"
    ),
    path("team/", advanced.team, name="team"),
    path("planner/", advanced.planner, name="planner"),
    path("transfer/", advanced.transfer, name="transfer"),
    path("portfolio/", advanced.portfolio, name="portfolio"),
    path("mandates/", gateway.mandates, name="mandates"),
    path("checkout/<int:pk>/", gateway.checkout, name="checkout"),
    path("gateway/webhook/", gateway.webhook, name="webhook"),
    path("<str:section>/new/", views.create, name="create"),
    path("<str:section>/<int:pk>/action/", views.action, name="action"),
    path("<str:section>/", views.listing, name="list"),
]
