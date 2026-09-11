from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def inr(value):
    if value is None or value == "":
        return "₹0"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return "₹0"
    if amount >= Decimal("10000000"):
        return f"₹{float(amount) / 10000000:.2f} Cr"
    if amount >= Decimal("100000"):
        return f"₹{float(amount) / 100000:.2f} L"
    return f"₹{int(amount):,}"


@register.simple_tag
def preserve_filters(request, **kwargs):
    get = request.GET.copy()
    if "page" not in kwargs:
        get.pop("page", None)
    for key, val in kwargs.items():
        if val is not None and val != "":
            get[key] = str(val)
        else:
            get.pop(key, None)
    return get.urlencode()
