"""
Open Chat Studio OAuth Views.

Session-based OAuth implementation for accessing Open Chat Studio APIs.
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


def labs_ocs_initiate(request: HttpRequest) -> HttpResponseRedirect:
    """
    Initiate OAuth flow with Open Chat Studio to get API access.

    This is separate from the Connect OAuth and is used to access OCS APIs.
    """
    # Get OCS OAuth settings
    client_id = getattr(settings, "OCS_OAUTH_CLIENT_ID", None)
    ocs_url = getattr(settings, "OCS_URL", "https://www.openchatstudio.com")

    if not client_id:
        messages.error(request, "Open Chat Studio OAuth not configured. Contact administrator.")
        return redirect(request.headers.get("referer", "/labs/overview/"))

    # Store next URL in session
    request.session["ocs_oauth_next"] = request.GET.get("next", "/labs/overview/")

    # Generate PKCE code verifier and challenge (OCS requires PKCE)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    )

    # Store code verifier in session for token exchange
    request.session["ocs_oauth_code_verifier"] = code_verifier

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)
    request.session["ocs_oauth_state"] = state

    # Build authorization URL with PKCE
    callback_url = request.build_absolute_uri(reverse("labs:ocs_callback"))
    auth_params = {
        "client_id": client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    auth_url = f"{ocs_url}/o/authorize/?{urlencode(auth_params)}"
    logger.info(f"Initiating Open Chat Studio OAuth for user {request.user.username}")

    return redirect(auth_url)


def labs_ocs_callback(request: HttpRequest) -> HttpResponseRedirect:
    """
    Handle OAuth callback from Open Chat Studio.

    Exchanges authorization code for access token and stores in session.
    """
    code = request.GET.get("code")
    error = request.GET.get("error")
    error_description = request.GET.get("error_description", "")

    if error:
        logger.error(f"Open Chat Studio OAuth error: {error}")
        messages.error(request, f"Open Chat Studio authorization failed: {error_description or error}")
        return redirect("/labs/overview/")

    if not code:
        messages.error(request, "No authorization code received from Open Chat Studio.")
        return redirect("/labs/overview/")

    # Verify state to prevent CSRF
    state = request.GET.get("state")
    saved_state = request.session.get("ocs_oauth_state")

    if not state or state != saved_state:
        logger.warning("Open Chat Studio OAuth callback with invalid state parameter", extra={"received_state": state})
        messages.error(request, "Invalid authentication state. Please try logging in again.")
        return redirect("/labs/overview/")

    # Get PKCE code verifier from session
    code_verifier = request.session.get("ocs_oauth_code_verifier")
    if not code_verifier:
        logger.error("Open Chat Studio OAuth callback missing code verifier in session")
        messages.error(request, "Session expired. Please try logging in again.")
        return redirect("/labs/overview/")

    # Get next URL from session
    next_url = request.session.get("ocs_oauth_next", "/labs/overview/")

    # Exchange code for token with PKCE
    client_id = settings.OCS_OAUTH_CLIENT_ID
    client_secret = settings.OCS_OAUTH_CLIENT_SECRET
    ocs_url = getattr(settings, "OCS_URL", "https://www.openchatstudio.com")
    callback_url = request.build_absolute_uri(reverse("labs:ocs_callback"))

    try:
        with httpx.Client() as client:
            token_response = client.post(
                f"{ocs_url}/o/token/",
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
            messages.error(request, "Failed to obtain Open Chat Studio access token.")
            return redirect("/labs/overview/")

        token_data = token_response.json()

        # Store OCS OAuth token in session (separate from Connect/CommCare OAuth)
        request.session["ocs_oauth"] = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_at": timezone.now().timestamp() + token_data.get("expires_in", 3600),
            "token_type": token_data.get("token_type", "Bearer"),
            "scope": token_data.get("scope", ""),
        }

        # Clean up OAuth flow data
        request.session.pop("ocs_oauth_next", None)
        request.session.pop("ocs_oauth_state", None)
        request.session.pop("ocs_oauth_code_verifier", None)

        logger.info(f"Open Chat Studio OAuth successful for user {request.user.username}")
        messages.success(request, "Successfully connected to Open Chat Studio!")

        # Sanitize next_url: only allow safe internal paths
        next_url = (next_url or "").replace("\\", "/")
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
            next_url = "/labs/overview/"

        return redirect(next_url)

    except Exception as e:
        logger.exception(f"Error during Open Chat Studio OAuth callback: {e}")
        messages.error(request, f"Error connecting to Open Chat Studio: {str(e)}")
        return redirect("/labs/overview/")


def labs_ocs_logout(request: HttpRequest) -> HttpResponseRedirect:
    """
    Log out of Open Chat Studio by clearing OCS OAuth session data.

    Redirects back to the referring page or labs overview.
    """
    # Clear OCS OAuth data from session
    request.session.pop("ocs_oauth", None)

    logger.info(f"User {request.user.username} disconnected from Open Chat Studio")
    messages.info(request, "Disconnected from Open Chat Studio.")

    # Redirect back to referrer or labs overview
    redirect_url = request.headers.get("referer", "/labs/overview/")
    return redirect(redirect_url)
