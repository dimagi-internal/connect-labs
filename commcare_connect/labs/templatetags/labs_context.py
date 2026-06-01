"""
Template tags and filters for labs context management.

Provides helpers to work with context URL parameters in templates.
"""
from django import template
from django.utils.http import urlencode

register = template.Library()


@register.simple_tag(takes_context=True)
def context_url_params(context):
    """Get current context as URL query string.

    Usage:
        {% context_url_params %}
        # Returns: "opportunity_id=123&program_id=456"

    Returns:
        Query string with context parameters
    """
    request = context.get("request")
    if not request or not hasattr(request, "labs_context"):
        return ""

    labs_context = request.labs_context
    params = {}

    # Extract only the ID fields (not the full objects)
    if "opportunity_id" in labs_context:
        params["opportunity_id"] = labs_context["opportunity_id"]
    if "program_id" in labs_context:
        params["program_id"] = labs_context["program_id"]
    if "organization_id" in labs_context:
        params["organization_id"] = labs_context["organization_id"]

    return urlencode(params) if params else ""


@register.simple_tag(takes_context=True)
def url_with_context(context, url):
    """Add context parameters to a URL.

    Usage:
        {% url_with_context "/tasks/" %}
        # Returns: "/tasks/?opportunity_id=123&program_id=456"

        {% url 'tasks:list' as task_url %}
        {% url_with_context task_url %}

    Args:
        url: URL to add context to

    Returns:
        URL with context parameters appended
    """
    request = context.get("request")
    if not request or not hasattr(request, "labs_context"):
        return url

    from commcare_connect.labs.context import add_context_to_url

    return add_context_to_url(url, request.labs_context)


@register.filter
def with_context(url, request):
    """Filter to add context to a URL.

    Usage:
        <a href="{{ '/tasks/'|with_context:request }}">Tasks</a>

    Args:
        url: URL to add context to
        request: HttpRequest object

    Returns:
        URL with context parameters appended
    """
    if not request or not hasattr(request, "labs_context"):
        return url

    from commcare_connect.labs.context import add_context_to_url

    return add_context_to_url(url, request.labs_context)


@register.simple_tag(takes_context=True)
def has_context(context):
    """Check if any context is currently set.

    Usage:
        {% has_context as context_set %}
        {% if context_set %}
            <p>Context is active</p>
        {% endif %}

    Returns:
        Boolean indicating if context is set
    """
    request = context.get("request")
    if not request or not hasattr(request, "labs_context"):
        return False

    labs_context = request.labs_context
    return bool(
        labs_context.get("opportunity_id") or labs_context.get("program_id") or labs_context.get("organization_id")
    )
