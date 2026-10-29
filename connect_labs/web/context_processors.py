import re

from django.conf import settings

from connect_labs.utils.dimagi_user import is_dimagi_user
from connect_labs.utils.tables import DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS


def page_settings(request):
    """Expose global page size settings to templates."""
    return {"PAGE_SIZE_OPTIONS": PAGE_SIZE_OPTIONS, "DEFAULT_PAGE_SIZE": DEFAULT_PAGE_SIZE}


# `<int:workflow_id>` / `<username>` -> `{workflow_id}` / `{username}`.
_ROUTE_PARAM = re.compile(r"<(?:\w+:)?(\w+)>")
# A regex (re_path) route is not a template we can show as a path.
_REGEX_MARKERS = re.compile(r"[\^$()\\\[\]?*+|]")


def analytics_route(request):
    """The matched URL pattern as a path template for analytics, or None.

    Umami stores this instead of the request path, so a pageview carries no
    worker usernames, record ids or query strings (labs-analytics.js). It is
    the same ``ResolverMatch.route`` the per-route request metric uses: it
    comes from the urlconf, never from the request. Unresolved and regex
    routes give None and the browser falls back to its own templating.
    """
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", None) if match is not None else None
    if not isinstance(route, str) or _REGEX_MARKERS.search(_ROUTE_PARAM.sub("", route)):
        return None
    return "/" + _ROUTE_PARAM.sub(r"{\1}", route)[:200]


def analytics_context(request):
    """Provide self-hosted Umami analytics config to templates.

    First-party analytics only (HIPAA bar: no third-party trackers, no BAA
    needed). Empty websiteId/hostUrl disables the tracker entirely —
    labs-analytics.js then leaves window.labsTrack as a no-op queue.
    """
    authenticated = request.user.is_authenticated
    return {
        "ANALYTICS_VARS_JSON": {
            "hostUrl": settings.UMAMI_HOST_URL,
            "websiteId": settings.UMAMI_WEBSITE_ID,
            "route": analytics_route(request),
            "username": request.user.username if authenticated else None,
            "isDimagi": is_dimagi_user(request.user) if authenticated else False,
        }
    }


def chat_widget_context(request):
    # flags app was removed during labs simplification; chat widget is disabled
    return {
        "chat_widget_enabled": False,
        "chatbot_id": settings.CHATBOT_ID,
        "chatbot_embed_key": settings.CHATBOT_EMBED_KEY,
    }
