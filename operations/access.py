from django.db.models import Q
from django.core.exceptions import PermissionDenied
from loans.models import Loan
from .models import Membership


def scope(user, prefix=""):
    if user.is_superuser:
        return Q()
    filters = Q(**{prefix + "user": user})
    if user.is_staff:
        for m in Membership.objects.filter(user=user, organization__active=True):
            item = Q(**{prefix + "organization_id": m.organization_id})
            if m.branch_id:
                item &= Q(**{prefix + "branch_id": m.branch_id})
            filters |= item
    return filters


def loans_for(user):
    return Loan.objects.filter(scope(user))


def can_operate(user, loan):
    if not user.is_superuser and (
        not user.is_staff or not loans_for(user).filter(pk=loan.pk).exists()
    ):
        raise PermissionDenied("This loan is outside your assigned company or branch.")


def application_scope(user):
    if user.is_superuser:
        return Q()
    query = Q(applicant=user)
    if user.is_staff:
        for m in Membership.objects.filter(user=user, organization__active=True):
            item = Q(organization_id=m.organization_id)
            if m.branch_id:
                item &= Q(branch_id=m.branch_id)
            query |= item
    return query
