"""Admin-ish views for the MCP server.

create_token_browser:
    Browser-driven token creation for the labs-token-setup Claude Code skill.
    Consent page on GET, token creation + localhost redirect on POST.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode, urlparse

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .models import MCPAccessToken

_LOCALHOST_HOSTS = {"localhost", "127.0.0.1"}


def _validate_callback(callback: str) -> str | None:
    """Return None if callback is acceptable; else an error message."""
    if not callback:
        return "callback query param is required"
    parsed = urlparse(callback)
    if parsed.scheme != "http":
        return "callback scheme must be http"
    if parsed.hostname not in _LOCALHOST_HOSTS:
        return "callback host must be localhost or 127.0.0.1"
    try:
        port = parsed.port
    except ValueError:
        return "callback port must be in 1024-65535"
    if port is None or not (1024 <= port <= 65535):
        return "callback port must be in 1024-65535"
    return None


@login_required
@require_http_methods(["GET", "POST"])
def create_token_browser(request):
    """Browser-driven MCP PAT creation.

    GET: shows a consent page.
    POST: creates the token and redirects to the localhost callback.
    """
    callback = request.GET.get("callback") or request.POST.get("callback") or ""
    state = request.GET.get("state") or request.POST.get("state") or ""

    err = _validate_callback(callback)
    if err:
        return HttpResponseBadRequest(f"Invalid callback: {err}")
    if len(state) < 16:
        return HttpResponseBadRequest("state nonce required (min 16 chars)")

    if request.method == "GET":
        return render(
            request,
            "mcp/create_token_browser.html",
            {
                "callback": callback,
                "state": state,
                "default_name": f"claude-code-{datetime.now():%Y%m%d-%H%M%S}",
            },
        )

    # POST — create the token and redirect
    name = (request.POST.get("name") or "").strip()
    if not name:
        name = f"claude-code-{datetime.now():%Y%m%d-%H%M%S}"

    _, raw = MCPAccessToken.create_token(request.user, name=name)

    sep = "&" if "?" in callback else "?"
    redirect_url = f"{callback}{sep}" + urlencode({"token": raw, "state": state, "name": name})
    return redirect(redirect_url)
