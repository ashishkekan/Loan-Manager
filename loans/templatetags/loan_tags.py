from django import template

register = template.Library()


@register.filter
def health_color(score):
    try:
        s = int(score)
    except (ValueError, TypeError):
        return "danger"
    if s >= 80:
        return "success"
    if s >= 60:
        return "primary"
    if s >= 40:
        return "warn"
    return "danger"
