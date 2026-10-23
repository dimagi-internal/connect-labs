"""Filters for the Labs Usage admin page (labs/admin/usage.html)."""

from django import template

register = template.Library()


@register.filter
def usage_duration(ms) -> str:
    """Milliseconds as a short human duration: 850 ms, 12.3 s, 4.2 min, 1.5 h."""
    try:
        ms = float(ms or 0)
    except (TypeError, ValueError):
        return ""
    if ms < 1000:
        return f"{ms:.0f} ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"
