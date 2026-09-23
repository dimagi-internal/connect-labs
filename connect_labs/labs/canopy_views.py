"""The one endpoint the canopy agent panel needs from labs.

The widget in the browser cannot sign anything — the key would have to be in the
page — so it asks labs, over labs' own session, and labs signs. That is why "one
script tag" is really "one script tag plus one endpoint".
"""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from connect_labs.labs import canopy

log = logging.getLogger(__name__)


@login_required
@require_POST
def token(request):
    """Mint a canopy token for the person whose session this is.

    The subject is ``request.user`` and can be nothing else: the request body is
    ignored entirely rather than read and validated, because a subject taken from
    a caller would let any caller be anybody, and the safest way to not read a
    field is to have no code that reads it.

    CSRF applies as normal. Labs sets ``CSRF_COOKIE_HTTPONLY``, so the widget
    cannot read the cookie — it is handed the rendered token instead, through
    ``csrfToken`` in the snippet. If this ever starts 403ing with no
    ``X-CSRFToken`` on the request, that binding is what broke.
    """
    if not canopy.is_configured():
        # 503 rather than 404: the route exists, the deployment has not been
        # given a key. The snippet does not render in this state, so reaching
        # here at all means configuration changed under a loaded page.
        return JsonResponse({"error": "the canopy panel is not configured here"}, status=503)

    try:
        vouched = canopy.vouch_for(request.user)
    except canopy.CanopyMintFailed as exc:
        # Logged with canopy's own words, because they name the cause
        # (`replayed`, `bad_signature`, `unknown_issuer`) and nothing on the page
        # can. Not returned to the browser: the visitor cannot act on any of
        # them, and they describe labs' credential rather than their session.
        log.warning("canopy declined to mint a token for user %s: %s", request.user.pk, exc)
        return JsonResponse({"error": "could not reach the agent service"}, status=502)

    return JsonResponse({"token": vouched["token"], "expires_at": vouched["expires_at"]})
