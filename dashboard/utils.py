from dashboard.models import ActivityLog


def add_activity(user, action, title, loan, description=""):
    ActivityLog.objects.create(
        user=user,
        action=action,
        title=title,
        loan=loan,
        description=description,
    )
