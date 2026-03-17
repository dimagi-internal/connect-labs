from django.conf import settings

from commcare_connect.utils.tables import DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS


def page_settings(request):
    """Expose global page size settings to templates."""
    return {"PAGE_SIZE_OPTIONS": PAGE_SIZE_OPTIONS, "DEFAULT_PAGE_SIZE": DEFAULT_PAGE_SIZE}


def gtm_context(request):
    """Provide Google Tag Manager context variables to templates."""
    # TODO: Re-enable once Connect server PR is merged (email not yet available from OAuth).
    is_dimagi = request.user.is_authenticated  # temporarily treat all logged-in users as dimagi for GTM
    user_id = request.user.id if request.user.is_authenticated else None
    return {
        "GTM_VARS_JSON": {
            "isDimagi": is_dimagi,
            "gtmID": settings.GTM_ID,
            "userId": user_id,
        }
    }


def chat_widget_context(request):
    # flags app was removed during labs simplification; chat widget is disabled
    return {
        "chat_widget_enabled": False,
        "chatbot_id": settings.CHATBOT_ID,
        "chatbot_embed_key": settings.CHATBOT_EMBED_KEY,
    }
