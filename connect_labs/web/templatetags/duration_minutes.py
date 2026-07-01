from django import template
from django.template.defaultfilters import pluralize

register = template.Library()


@register.filter
def duration_minutes(td):
    total_seconds = int(td.total_seconds())
    minutes = total_seconds // 60
    seconds = total_seconds % 60

    return f"{minutes} minute{pluralize(minutes, ',s')} {seconds} second{pluralize(seconds, ',s')}"
