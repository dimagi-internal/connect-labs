import markdown as md
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name="dict_lookup")
def dict_lookup(d, key):
    """Look up a key in a dictionary."""
    if isinstance(d, dict):
        return d.get(key, [])
    return []


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


@register.filter(name="get_criteria_field")
def get_criteria_field(form, criterion_id):
    """Get the criteria score field from a form by criterion ID."""
    field_name = f"criteria_score_{criterion_id}"
    if hasattr(form, "fields") and field_name in form.fields:
        return form[field_name]
    return ""
