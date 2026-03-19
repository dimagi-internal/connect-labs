import markdown as md
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="markdown")
def render_markdown(value):
    """Render a string as Markdown HTML."""
    if not value:
        return ""
    html = md.markdown(
        str(value),
        extensions=["nl2br", "sane_lists", "smarty"],
    )
    return mark_safe(html)
