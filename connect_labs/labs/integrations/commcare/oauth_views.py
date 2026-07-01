"""
CommCare HQ OAuth Views.

Session-based OAuth implementation for accessing CommCare HQ APIs.
"""

import hashlib
import logging
import secrets
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)


def labs_commcare_initiate(request: HttpRequest) -> HttpResponseRedirect:
    """
    Initiate OAuth flow with CommCare HQ to get API access.

    This is separate from the Connect OAuth and is used to access CommCare APIs
    for fetching form data, blobs, etc.
    """
    # Get CommCare OAuth settings
    client_id = getattr(settings, "COMMCARE_OAUTH_CLIENT_ID", None)
    commcare_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")

    if not client_id:
        messages.error(request, "CommCare OAuth not configured. Contact administrator.")
        return redirect(request.headers.get("referer", "/audit/"))

    # Store next URL in session
    request.session["commcare_oauth_next"] = request.GET.get("next", "/audit/")

    # Generate PKCE code verifier and challenge (CommCareHQ now requires PKCE)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    )

    # Store code verifier in session for token exchange
    request.session["commcare_oauth_code_verifier"] = code_verifier

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)
    request.session["commcare_oauth_state"] = state

    # Build authorization URL with PKCE
    callback_url = request.build_absolute_uri(reverse("labs:commcare_callback"))
    auth_params = {
        "client_id": client_id,
        "redirect_uri": callback_url,
        "scope": "access_apis",
        "response_type": "code",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    auth_url = f"{commcare_url}/oauth/authorize/?{urlencode(auth_params)}"
    logger.info(f"Initiating CommCare OAuth for user {request.user.username}")

    return redirect(auth_url)


def labs_commcare_callback(request: HttpRequest) -> HttpResponseRedirect:
    """
    Handle OAuth callback from CommCare HQ.

    Exchanges authorization code for access token and stores in session.
    """
    code = request.GET.get("code")
    error = request.GET.get("error")
    error_description = request.GET.get("error_description", "")

    if error:
        logger.error(f"CommCare OAuth error: {error}")
        messages.error(request, f"CommCare authorization failed: {error_description or error}")
        return redirect("/audit/")

    if not code:
        messages.error(request, "No authorization code received from CommCare.")
        return redirect("/audit/")

    # Verify state to prevent CSRF
    state = request.GET.get("state")
    saved_state = request.session.get("commcare_oauth_state")

    if not state or state != saved_state:
        logger.warning("CommCare OAuth callback with invalid state parameter", extra={"received_state": state})
        messages.error(request, "Invalid authentication state. Please try logging in again.")
        return redirect("/audit/")

    # Get PKCE code verifier from session
    code_verifier = request.session.get("commcare_oauth_code_verifier")
    if not code_verifier:
        logger.error("CommCare OAuth callback missing code verifier in session")
        messages.error(request, "Session expired. Please try logging in again.")
        return redirect("/audit/")

    # Get next URL from session
    next_url = request.session.get("commcare_oauth_next", "/audit/")

    # Exchange code for token with PKCE
    client_id = settings.COMMCARE_OAUTH_CLIENT_ID
    client_secret = settings.COMMCARE_OAUTH_CLIENT_SECRET
    commcare_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")
    callback_url = request.build_absolute_uri(reverse("labs:commcare_callback"))

    try:
        with httpx.Client() as client:
            token_response = client.post(
                f"{commcare_url}/oauth/token/",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": callback_url,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": code_verifier,
                },
                timeout=30.0,
            )

        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.status_code} - {token_response.text}")
            messages.error(request, "Failed to obtain CommCare access token.")
            return redirect("/audit/")

        token_data = token_response.json()

        # Store CommCare OAuth token in session (separate from Connect OAuth)
        request.session["commcare_oauth"] = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_at": timezone.now().timestamp() + token_data.get("expires_in", 3600),
            "token_type": token_data.get("token_type", "Bearer"),
        }

        # Clean up OAuth flow data
        request.session.pop("commcare_oauth_next", None)
        request.session.pop("commcare_oauth_state", None)
        request.session.pop("commcare_oauth_code_verifier", None)

        has_refresh = bool(token_data.get("refresh_token"))
        logger.info(f"CommCare OAuth successful for user {request.user.username} (refresh_token={has_refresh})")
        messages.success(request, "Successfully connected to CommCare!")

        # Sanitize next_url: only allow safe internal paths
        next_url = (next_url or "").replace("\\", "/")
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
            next_url = "/audit/"

        return redirect(next_url)

    except Exception as e:
        logger.exception(f"Error during CommCare OAuth callback: {e}")
        messages.error(request, f"Error connecting to CommCare: {str(e)}")
        return redirect("/audit/")


def labs_commcare_logout(request: HttpRequest) -> HttpResponseRedirect:
    """
    Log out of CommCare HQ by clearing CommCare OAuth session data.

    Redirects back to the referring page or labs overview.
    """
    # Clear CommCare OAuth data from session
    request.session.pop("commcare_oauth", None)

    logger.info(f"User {request.user.username} disconnected from CommCare")
    messages.info(request, "Disconnected from CommCare.")

    # Redirect back to referrer or labs overview
    redirect_url = request.headers.get("referer", "/labs/overview/")
    return redirect(redirect_url)
