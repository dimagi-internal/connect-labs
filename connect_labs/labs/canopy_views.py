"""The one endpoint the canopy agent panel needs from labs.

The widget in the browser cannot sign anything — the key would have to be in the
page — so it asks labs, over labs' own session, and labs signs. That is why "one
script tag" is really "one script tag plus one endpoint".
"""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

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

    The one thing read from the request is ``?page=``, the page token the panel
    was rendered with (``canopy.page_token``). It is labs' OWN signature over
    the page's route and this user, so it can say which registered page the
    panel is on and nothing else: the scopes come from labs' registry, and a
    token that is missing, forged, expired or another user's simply means no
    grant — the mint itself goes ahead as before.
    """
    if not canopy.is_configured():
        # 503 rather than 404: the route exists, the deployment has not been
        # given a key. The snippet does not render in this state, so reaching
        # here at all means configuration changed under a loaded page.
        return JsonResponse({"error": "the canopy panel is not configured here"}, status=503)

    try:
        scopes = canopy.scopes_for_page_token(request.GET.get("page"), request.user)
        vouched = canopy.vouch_for(request.user, scopes=scopes)
    except canopy.CanopyMintFailed as exc:
        # Logged with canopy's own words, because they name the cause
        # (`replayed`, `bad_signature`, `unknown_issuer`) and nothing on the page
        # can. Not returned to the browser: the visitor cannot act on any of
        # them, and they describe labs' credential rather than their session.
        log.warning("canopy declined to mint a token for user %s: %s", request.user.pk, exc)
        return JsonResponse({"error": "could not reach the agent service"}, status=502)

    return JsonResponse({"token": vouched["token"], "expires_at": vouched["expires_at"]})


@require_GET
def jwks(request):
    """The public half of labs' signing key, for canopy to verify against.

    **Unauthenticated on purpose.** A public key is public — this is the same
    document every OIDC provider serves, and canopy fetches it from outside any
    session.

    Publishing it as a URL rather than pasting the key into canopy is what makes
    rotation free: put a new private key in the secret store, and canopy follows
    by `kid` on its next fetch. A key that can only be rotated by somebody
    re-pasting it is a key that never gets rotated.
    """
    if not canopy.is_configured():
        return JsonResponse({"keys": []}, status=503)
    try:
        return JsonResponse({"keys": [canopy.public_jwk()]})
    except Exception:
        # A malformed key is a deployment fault, and an empty key set is the
        # honest answer: canopy then refuses our assertions rather than being
        # handed something it cannot parse.
        log.exception("CANOPY_SIGNING_KEY could not be read as a private key")
        return JsonResponse({"keys": []}, status=503)
