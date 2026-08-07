def loan_notifications(request):
    if not request.user.is_authenticated:
        return {
            "header_notifications": [],
            "header_unread_notifications": 0,
        }

    from loans.models import Notification

    notifications = (
        Notification.objects.filter(user=request.user)
        .select_related("loan")
        .order_by("-created_at")[:5]
    )

    unread_count = Notification.objects.filter(
        user=request.user,
        is_read=False,
    ).count()

    return {
        "header_notifications": notifications,
        "header_unread_notifications": unread_count,
    }
