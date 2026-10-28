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

from . import full_access, token_scopes
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
    # The caller asks for a scope (``?scope=no-uservisit-data``); the consent page names it
    # so the person approving sees what they are handing over.
    scope = request.GET.get("scope") or request.POST.get("scope") or token_scopes.FULL
    scope_labels = dict(token_scopes.SCOPE_CHOICES)
    if scope not in scope_labels:
        return HttpResponseBadRequest(f"Unknown scope {scope!r}")

    if request.method == "GET":
        return render_consent(request, callback=callback, state=state, scope=scope)

    # POST — create the token and redirect
    name = (request.POST.get("name") or "").strip()
    if not name:
        name = f"claude-code-{datetime.now():%Y%m%d-%H%M%S}"

    allowed = None
    if scope == token_scopes.FULL:
        # Clicking "create" is not enough for full access: an agent driving this
        # browser could click it. The person's passkey, through canopy, is.
        allowed = full_access.self_minted(request.user)
        if allowed is None:
            return full_access.begin(
                request,
                full_access.PendingFullAccess.TOKEN_BROWSER,
                {"callback": callback, "state": state, "name": name},
            )
    return mint_and_redirect(request, callback=callback, state=state, name=name, scope=scope, full_access=allowed)


def render_consent(
    request,
    *,
    callback: str,
    state: str,
    scope: str = token_scopes.FULL,
    name: str = "",
    notice: dict | None = None,
    status: int = 200,
):
    """The consent page; with ``notice`` when a full-access token waits on (or was refused)
    the person's passkey (``full_access._show``)."""
    return render(
        request,
        "mcp/create_token_browser.html",
        {
            "callback": callback,
            "state": state,
            "scope": scope,
            "scope_label": dict(token_scopes.SCOPE_CHOICES)[scope],
            "is_no_uservisit_data": scope == token_scopes.NO_USERVISIT_DATA,
            "needs_passkey": scope == token_scopes.FULL and not full_access.may_self_mint(request.user),
            "default_name": name or f"claude-code-{datetime.now():%Y%m%d-%H%M%S}",
            "full_access_notice": notice,
        },
        status=status,
    )


def mint_and_redirect(
    request, *, callback: str, state: str, name: str, scope: str = token_scopes.FULL, full_access=None
):
    """Mint the token and hand it to the localhost callback."""
    if _validate_callback(callback):
        return HttpResponseBadRequest("Invalid callback")
    _, raw = MCPAccessToken.create_token(request.user, name=name, scope=scope, full_access=full_access)

    sep = "&" if "?" in callback else "?"
    redirect_url = f"{callback}{sep}" + urlencode({"token": raw, "state": state, "name": name})
    return redirect(redirect_url)
