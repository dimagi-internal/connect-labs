from django.conf import settings

from connect_labs.utils.dimagi_user import is_dimagi_user
from connect_labs.utils.tables import DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS


def page_settings(request):
    """Expose global page size settings to templates."""
    return {"PAGE_SIZE_OPTIONS": PAGE_SIZE_OPTIONS, "DEFAULT_PAGE_SIZE": DEFAULT_PAGE_SIZE}


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
