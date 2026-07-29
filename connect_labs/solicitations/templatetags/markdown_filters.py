import markdown as md
import nh3
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
    """Render a string as sanitized Markdown HTML.

    ``description`` / ``scope_of_work`` are author-supplied free text shown to
    other labs users (respondents, reviewers). Python-Markdown passes raw inline
    HTML through untouched and happily renders ``[x](javascript:...)`` links, so
    the rendered output MUST be sanitized before ``mark_safe`` — otherwise a
    solicitation author can land stored XSS in every viewer's session.

    ``nh3.clean`` (ammonia) strips ``<script>``/event handlers and restricts URL
    schemes to a safe set (http/https/mailto by default), which covers both the
    raw-tag and ``javascript:``-URL vectors while leaving normal markdown
    formatting intact.
    """
    if not value:
        return ""
    html = md.markdown(
        str(value),
        extensions=["nl2br", "sane_lists", "smarty"],
    )
    return mark_safe(nh3.clean(html))


@register.filter(name="get_criteria_field")
def get_criteria_field(form, criterion_id):
    """Get the criteria score field from a form by criterion ID."""
    field_name = f"criteria_score_{criterion_id}"
    if hasattr(form, "fields") and field_name in form.fields:
        return form[field_name]
    return ""
