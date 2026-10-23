"""The example system's endpoints: its page, the page's token endpoint, and its JWKS.

In a real embedding system these are wherever that system keeps them. The one rule that
matters: the person named in the statement is ALWAYS the person signed in to this system
(``request.user``), never anything a request supplies.
"""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from connect_labs.embed_example import config
from connect_labs.embed_example.embedder import EmbedError

HANDOFFS = ("fragment", "message")


@login_required
@require_GET
@never_cache
def page(request):
    """The example page. ``?resource=<kind>/<id>`` and ``?handoff=fragment|message``."""
    context = {
        "configured": config.configured(),
        "resources": config.resources(),
        "labs_origin": config.labs_url(),
        "client_id": config.client_id(),
    }
    chosen = config.resource(request.GET.get("resource")) if context["configured"] else None
    handoff = request.GET.get("handoff") if request.GET.get("handoff") in HANDOFFS else "fragment"
    context.update(resource=chosen, handoff=handoff)
    if chosen is not None:
        embedder = config.embedder()
        if handoff == "fragment":
            # Way 1: mint the token while rendering, and put it in the frame's address after
            # "#". The fragment is never sent to a server, so it stays out of every log.
            try:
                embed = embedder.embed(request.user.username, chosen.kind, chosen.id)
                context["frame_src"] = f"{embed.embed_url}#token={embed.token}"
            except EmbedError as refused:
                context["refused"] = refused
        else:
            # Way 2: frame labs with no token; the page's script answers the frame's
            # "needs-token" message with a token from token() below.
            context["frame_src"] = embedder.frame_address(chosen.kind, chosen.id)
        labs = urlsplit(config.labs_url())
        context["example_config"] = {
            "labsOrigin": f"{labs.scheme}://{labs.netloc}",
            "frameAddress": embedder.frame_address(chosen.kind, chosen.id),
            "tokenUrl": reverse("embed_example:token") + "?" + urlencode({"resource": chosen.key}),
            "csrfToken": get_token(request),
        }
    return render(request, "embed_example/page.html", context)


@login_required
@require_POST
@never_cache
def token(request):
    """A fresh one-time token for the page's script (the postMessage handoff)."""
    if not config.configured():
        raise Http404("the example is not configured")
    chosen = config.resource(request.GET.get("resource"))
    if chosen is None:
        raise Http404("not one of the example's resources")
    try:
        embed = config.embedder().embed(request.user.username, chosen.kind, chosen.id)
    except EmbedError as refused:
        return JsonResponse({"error": refused.error, "error_description": refused.description}, status=502)
    return JsonResponse({"token": embed.token, "embed_url": embed.embed_url})


@require_GET
def jwks(request):
    """The example's public keys, the URL registered with labs. Public: a public key is public."""
    if not config.configured():
        raise Http404("the example is not configured")
    response = JsonResponse(config.embedder().jwks())
    response["Cache-Control"] = "max-age=300"
    return response
