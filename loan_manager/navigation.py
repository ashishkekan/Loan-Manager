"""Shared, role-aware navigation for the app shell and operations directory."""

from django.urls import reverse


def app_navigation(request):
    if not request.user.is_authenticated:
        return {}
    match = request.resolver_match
    route = match.view_name if match else ""
    kwargs = match.kwargs if match else {}

    def item(label, name, args=(), icon="circle", report=None):
        url = reverse(name, args=args)
        active = route == name
        if name in ("operations:list", "operations:create"):
            active = (
                route in ("operations:list", "operations:create", "operations:action")
                and kwargs.get("section") == args[0]
            )
        elif name == "operations:import":
            active = active and kwargs.get("kind") == args[0]
        elif name == "loan_list":
            active = route in ("loan_list", "loan_detail", "loan_create", "loan_update")
        if report:
            url += "?report=" + report
            active = active and request.GET.get("report", "loan_portfolio") == report
        return {"label": label, "url": url, "active": active, "icon": icon}

    def group(label, icon, items):
        return {
            "label": label,
            "icon": icon,
            "items": items,
            "active": any(i["active"] for i in items),
        }

    def section(label, key):
        result = item(label, "operations:list", (key,))
        if key == "applications" and route in (
            "operations:application_documents",
            "operations:edit_application",
        ):
            result["active"] = True
        return result

    workflow = [
        section("Applications", "applications"),
        section("Disbursements", "disbursements"),
        section("Payment receipts", "receipts"),
        section("Loan revisions", "revisions"),
        section("Collections", "collections"),
        section("Charges & waivers", "charges"),
    ]
    records = [
        section("Document reviews", "documents"),
        section("Related parties", "parties"),
        section("Collateral", "collateral"),
        item("Import loans", "operations:import", ("loans",)),
    ]
    tools = [
        item("Reversals", "operations:reversals"),
        item("Reminder delivery", "operations:deliveries"),
        item("Recurring authorisations", "operations:mandates"),
        item("Multi-loan planner", "operations:planner"),
        item("Balance transfer", "operations:transfer"),
        item("Identity & lender funds", "operations:onboarding"),
        item("Lender portfolio", "operations:portfolio"),
    ]
    if request.user.has_perm("operations.verify_receipt"):
        tools += [
            item("Bank reconciliation", "operations:reconciliation"),
            item("Import bank statement", "operations:import", ("bank",)),
        ]
    if request.user.is_superuser:
        records.append(section("Product policies", "policies"))
    categories = [
        group("Loan workflow", "tasks", workflow),
        group("Documents & records", "folder-open", records),
        group("Payments & planning", "calculator", tools),
    ]
    operations_items = (
        [item("Overview", "operations:home")] + workflow + records + tools
    )
    groups = [
        group(
            "Loans & Payments",
            "wallet",
            [
                item(
                    "All loans" if request.user.is_superuser else "My loans",
                    "loan_list",
                ),
                item("Payment history", "payment_dashboard"),
                item("Prepayment history", "prepayment_dashboard"),
                item("My documents", "documents_dashboard"),
            ],
        ),
        group("Loan Operations", "tasks", operations_items),
    ]
    groups[1]["categories"] = categories
    groups[1]["overview"] = operations_items[0]
    if request.user.is_superuser:
        groups.append(
            group(
                "Reports",
                "chart-column",
                [
                    item(label, "admin_reports", report=key)
                    for label, key in [
                        ("Loan portfolio", "loan_portfolio"),
                        ("Payment collection", "payment_collection"),
                        ("Overdue loans", "overdue"),
                        ("User summary", "user_summary"),
                        ("Performance", "performance"),
                    ]
                ],
            )
        )
        groups.append(
            group(
                "Administration",
                "users-gear",
                [
                    item("Site Appearance", "operations:appearance"),
                    item("Users", "admin_users"),
                    item("Banks", "admin_banks"),
                    item("Companies, branches & team", "operations:team"),
                    item("Audit history", "operations:audit"),
                    item("Activity logs", "activity_logs_dashboard"),
                ],
            )
        )
    groups.append(
        group(
            "My Account",
            "user-shield",
            [
                item("Account security", "operations:security"),
                item("Profile & settings", "settings_dashboard"),
                item("Notifications", "notifications_dashboard"),
                item("Support", "support_dashboard"),
            ],
        )
    )
    active_labels = [
        entry["label"]
        for group in groups
        for entry in group["items"]
        if entry["active"]
    ]
    title = (
        "Loan Operations"
        if route == "operations:home"
        else (active_labels[0] if active_labels else "Dashboard")
    )
    return {
        "navigation_title": title,
        "sidebar_groups": groups,
        "operations_categories": categories,
        "sidebar_dashboard_active": route == "dashboard",
    }
