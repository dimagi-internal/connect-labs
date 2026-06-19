"""CommCare HQ OAuth as the PRIMARY login for the Campaign Utility Tool.

Unlike labs' secondary CommCare connection, the callback here also fetches
identity, checks the whitelist, creates/updates a Django user, and logs them in.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib.auth import login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from commcare_connect.campaign.auth.identity import IdentityError, fetch_identity
from commcare_connect.campaign.auth.whitelist import resolve_campaign_user
from commcare_connect.users.models import User

logger = logging.getLogger(__name__)


def login_page(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated and request.session.get("campaign_oauth"):
        return redirect("campaign:app")
    return render(request, "campaign/login.html")


def oauth_initiate(request: HttpRequest) -> HttpResponseRedirect:
    client_id = getattr(settings, "COMMCARE_OAUTH_CLIENT_ID", None)
    commcare_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")
    if not client_id:
        return render(
            request, "campaign/not_authorized.html", {"reason": "CommCare OAuth not configured."}, status=500
        )

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    )
    state = secrets.token_urlsafe(32)
    request.session["campaign_oauth_code_verifier"] = code_verifier
    request.session["campaign_oauth_state"] = state

    callback_url = request.build_absolute_uri(reverse("campaign:oauth_callback"))
    params = {
        "client_id": client_id,
        "redirect_uri": callback_url,
        "scope": "access_apis",
        "response_type": "code",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return redirect(f"{commcare_url}/oauth/authorize/?{urlencode(params)}")


def oauth_callback(request: HttpRequest) -> HttpResponse:
    if request.GET.get("error"):
        return render(
            request,
            "campaign/not_authorized.html",
            {"reason": request.GET.get("error_description") or request.GET["error"]},
            status=403,
        )

    code = request.GET.get("code")
    state = request.GET.get("state")
    saved_state = request.session.get("campaign_oauth_state")
    code_verifier = request.session.get("campaign_oauth_code_verifier")
    if not code or not state or state != saved_state or not code_verifier:
        return render(
            request,
            "campaign/not_authorized.html",
            {"reason": "Invalid or expired login attempt. Please try again."},
            status=400,
        )

    commcare_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")
    callback_url = request.build_absolute_uri(reverse("campaign:oauth_callback"))
    try:
        with httpx.Client() as client:
            token_resp = client.post(
                f"{commcare_url}/oauth/token/",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": callback_url,
                    "client_id": settings.COMMCARE_OAUTH_CLIENT_ID,
                    "client_secret": settings.COMMCARE_OAUTH_CLIENT_SECRET,
                    "code_verifier": code_verifier,
                },
                timeout=30.0,
            )
    except httpx.RequestError as e:
        logger.warning("campaign token exchange network error: %s", e)
        return render(request, "campaign/not_authorized.html", {"reason": "Could not reach CommCare."}, status=502)

    if token_resp.status_code != 200:
        logger.error("campaign token exchange failed: %s", token_resp.status_code)
        return render(request, "campaign/not_authorized.html", {"reason": "CommCare rejected the login."}, status=403)

    token = token_resp.json()
    access_token = token["access_token"]

    try:
        identity = fetch_identity(access_token)
    except IdentityError:
        return render(
            request, "campaign/not_authorized.html", {"reason": "Could not read your CommCare identity."}, status=403
        )

    username = identity["username"]
    if not username:
        return render(
            request, "campaign/not_authorized.html", {"reason": "CommCare did not return a username."}, status=403
        )

    # Resolve the Django user by CommCare username first, then by email — the same
    # person may already exist under a different OAuth username (e.g. a ConnectID
    # from a prior Connect login) with this email. Reusing that row avoids a
    # duplicate-email IntegrityError on the unique_user_email constraint.
    email = identity.get("email") or None
    name = identity.get("name") or ""
    django_user = User.objects.filter(username=username).first()
    if django_user is None and email:
        django_user = User.objects.filter(email=email).first()
    if django_user is None:
        django_user = User.objects.create(username=username, email=email, name=name)
    elif name and django_user.name != name:
        django_user.name = name
        django_user.save(update_fields=["name"])
    campaign_user = resolve_campaign_user(identity, django_user)
    if campaign_user is None:
        return render(
            request,
            "campaign/not_authorized.html",
            {
                "reason": (
                    "Your CommCare account is not authorized for this tool."
                    " Ask a Campaign Administrator to add you."
                )
            },
            status=403,
        )

    login(request, django_user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["campaign_oauth"] = {
        "access_token": access_token,
        "refresh_token": token.get("refresh_token"),
        "expires_at": timezone.now().timestamp() + token.get("expires_in", 3600),
        "token_type": token.get("token_type", "Bearer"),
        "identity": identity,
    }
    for k in ("campaign_oauth_state", "campaign_oauth_code_verifier"):
        request.session.pop(k, None)
    return redirect("campaign:app")


def logout_view(request: HttpRequest) -> HttpResponseRedirect:
    from django.contrib.auth import logout

    request.session.pop("campaign_oauth", None)
    logout(request)
    return redirect("campaign:login")
