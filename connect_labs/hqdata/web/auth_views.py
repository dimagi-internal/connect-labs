"""CommCare HQ OAuth as a way into labs for people with no Connect login.

Same HQ OAuth application as Campaign and the labs CommCare connection (``access_apis`` scope,
PKCE). The redirect URI this flow uses, ``/hq/login/callback/``, has to be registered on that
application in CommCare HQ: until it is, HQ refuses the authorization request.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from base64 import urlsafe_b64encode
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render as django_render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from connect_labs.hqdata.identity import (
    IdentityError,
    fetch_identity,
    hq_base_url,
    hq_host,
    sync_identity,
    user_for_identity,
)
from connect_labs.labs.models import UserCCHQToken

logger = logging.getLogger(__name__)

_STATE, _VERIFIER, _NEXT = "hq_login_state", "hq_login_verifier", "hq_login_next"


def render(request, template, context=None, **kwargs):
    return django_render(request, template, {"present_mode": True, **(context or {})}, **kwargs)


def _safe_next(value: str | None) -> str:
    """Only paths under /hq/; anything else (another site, a labs path) goes home."""
    value = (value or "").replace("\\", "/")
    if ".." in value.split("?")[0].split("/"):
        return "/hq/"  # a browser resolves /hq/../x to /x, so it is not under /hq/ whatever it starts with
    if value.startswith("/hq/") and url_has_allowed_host_and_scheme(value, allowed_hosts=None):
        return value
    return "/hq/"


def _denied(request: HttpRequest, reason: str, status: int) -> HttpResponse:
    return render(request, "hqdata/not_authorized.html", {"reason": reason}, status=status)


def login_page(request: HttpRequest) -> HttpResponse:
    nxt = _safe_next(request.GET.get("next"))
    if request.user.is_authenticated and request.session.get("hq_auth"):
        return redirect(nxt)
    return render(request, "hqdata/login.html", {"next": nxt})


def login_initiate(request: HttpRequest) -> HttpResponse:
    client_id = getattr(settings, "COMMCARE_OAUTH_CLIENT_ID", None)
    if not client_id:
        return _denied(request, "CommCare sign-in is not configured on this server.", 500)

    verifier = secrets.token_urlsafe(64)
    challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(32)
    request.session[_STATE], request.session[_VERIFIER] = state, verifier
    request.session[_NEXT] = _safe_next(request.GET.get("next"))

    params = {
        "client_id": client_id,
        "redirect_uri": request.build_absolute_uri(reverse("hq:login_callback")),
        "scope": "access_apis",
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return redirect(f"{hq_base_url()}/oauth/authorize/?{urlencode(params)}")


def login_callback(request: HttpRequest) -> HttpResponse:
    if request.GET.get("error"):
        return _denied(request, request.GET.get("error_description") or request.GET["error"], 403)

    code, state = request.GET.get("code"), request.GET.get("state")
    saved_state, verifier = request.session.get(_STATE), request.session.get(_VERIFIER)
    if not code or not state or not saved_state or not secrets.compare_digest(state, saved_state) or not verifier:
        return _denied(request, "Invalid or expired sign-in attempt. Please try again.", 400)

    try:
        with httpx.Client() as client:
            resp = client.post(
                f"{hq_base_url()}/oauth/token/",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": request.build_absolute_uri(reverse("hq:login_callback")),
                    "client_id": settings.COMMCARE_OAUTH_CLIENT_ID,
                    "client_secret": settings.COMMCARE_OAUTH_CLIENT_SECRET,
                    "code_verifier": verifier,
                },
                timeout=30.0,
            )
    except httpx.RequestError as exc:
        logger.warning("hq login token exchange network error: %s", exc)
        return _denied(request, "Could not reach CommCare.", 502)
    if resp.status_code != 200:
        logger.warning("hq login token exchange failed: %s", resp.status_code)
        return _denied(request, "CommCare rejected the sign-in.", 403)

    token = resp.json()
    try:
        identity = fetch_identity(token["access_token"])
    except (IdentityError, KeyError):
        return _denied(request, "Could not read your CommCare identity.", 403)
    if not identity["username"]:
        return _denied(request, "CommCare did not return a username.", 403)

    host = hq_host()
    user = user_for_identity(identity, host=host)
    if not user.is_active:
        return _denied(request, "This account is disabled.", 403)

    # The headless chain scheduled syncs use (cchq_tokens): the same row the labs CommCare
    # connection writes, so a person has one chain however they came in.
    UserCCHQToken.objects.update_or_create(
        user=user,
        defaults={
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token", ""),
            "expires_at": timezone.now() + timedelta(seconds=token.get("expires_in", 3600)),
        },
    )
    nxt = request.session.get(_NEXT) or "/hq/"
    for key in (_STATE, _VERIFIER, _NEXT):
        request.session.pop(key, None)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["hq_auth"] = {"host": host, "at": timezone.now().timestamp()}
    sync_identity(user, token["access_token"], host=host, identity=identity)
    return redirect(_safe_next(nxt))


def logout_view(request: HttpRequest) -> HttpResponse:
    request.session.pop("hq_auth", None)
    logout(request)
    return redirect("hq:login")
