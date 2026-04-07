"""
CommCare Connect OAuth Views.

Session-based OAuth implementation for labs environment.
Stores tokens in session instead of database.
"""

import datetime
import hashlib
import logging
import secrets
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from commcare_connect.labs.integrations.connect.oauth import fetch_user_organization_data, introspect_token
from commcare_connect.users.models import User

logger = logging.getLogger(__name__)


def labs_login_page(request: HttpRequest) -> HttpResponse:
    """
    Display the labs login page with OAuth explanation.

    This is the entry point showing users what will happen before redirecting to OAuth.
    If already authenticated, shows logged-in status with logout option.
    """
    labs_oauth = request.session.get("labs_oauth")
    user_profile = None

    if labs_oauth:
        user_profile = labs_oauth.get("user_profile")

    # Get the next URL to pass through
    next_url = request.GET.get("next", "/labs/overview/")

    context = {
        "next": next_url,
        "user_profile": user_profile,
    }

    return render(request, "labs/login.html", context)


def labs_oauth_login(request: HttpRequest) -> HttpResponse:
    """
    Initiate OAuth flow to Connect production.

    No login required - this is the entry point for unauthenticated users.
    Stores OAuth state in session and redirects to Connect prod.
    """
    # Check if OAuth is configured
    if not settings.CONNECT_OAUTH_CLIENT_ID or not settings.CONNECT_OAUTH_CLIENT_SECRET:
        logger.error("OAuth not configured - missing CONNECT_OAUTH_CLIENT_ID or CONNECT_OAUTH_CLIENT_SECRET")
        messages.error(request, "OAuth authentication is not configured. Please contact your administrator.")
        return render(request, "labs/login.html", status=500)

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    request.session["oauth_next"] = request.GET.get("next", "/labs/overview/")

    # Generate PKCE code verifier and challenge
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    )

    request.session["oauth_code_verifier"] = code_verifier

    # Build callback URL
    callback_url = request.build_absolute_uri(reverse("labs:oauth_callback"))

    # Get OAuth scopes from settings
    scopes = getattr(settings, "LABS_OAUTH_SCOPES", ["export"])
    scope_string = " ".join(scopes)

    # Build OAuth authorize URL with PKCE
    params = {
        "client_id": settings.CONNECT_OAUTH_CLIENT_ID,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": scope_string,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    authorize_url = f"{settings.CONNECT_PRODUCTION_URL}/o/authorize/?{urlencode(params)}"

    logger.info(
        "Initiating OAuth flow", extra={"user_session": request.session.session_key, "redirect_uri": callback_url}
    )

    return HttpResponseRedirect(authorize_url)


def labs_oauth_callback(request: HttpRequest) -> HttpResponse:
    """
    Handle OAuth callback from Connect production.

    Exchange authorization code for access token and store in session.
    NO database writes - everything stored in encrypted session.
    """
    # Verify state to prevent CSRF
    state = request.GET.get("state")
    saved_state = request.session.get("oauth_state")

    if not state or state != saved_state:
        logger.warning("OAuth callback with invalid state parameter", extra={"received_state": state})
        messages.error(request, "Invalid authentication state. Please try logging in again.")
        return redirect("labs:oauth_initiate")

    # Get authorization code
    code = request.GET.get("code")
    if not code:
        error = request.GET.get("error", "Unknown error")
        error_description = request.GET.get("error_description", "")
        logger.error(f"OAuth error: {error}", extra={"description": error_description})
        messages.error(request, f"Authentication failed: {error_description or error}")
        return redirect("labs:oauth_initiate")

    # Get PKCE code verifier from session
    code_verifier = request.session.get("oauth_code_verifier")
    if not code_verifier:
        logger.error("OAuth callback missing code verifier in session")
        messages.error(request, "Session expired. Please try logging in again.")
        return redirect("labs:oauth_initiate")

    # Exchange code for token with PKCE
    callback_url = request.build_absolute_uri(reverse("labs:oauth_callback"))
    token_url = f"{settings.CONNECT_PRODUCTION_URL}/o/token/"

    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback_url,
        "client_id": settings.CONNECT_OAUTH_CLIENT_ID,
        "client_secret": settings.CONNECT_OAUTH_CLIENT_SECRET,
        "code_verifier": code_verifier,
        "response_type": "token",
    }

    try:
        response = httpx.post(token_url, data=token_data, timeout=10)
        response.raise_for_status()
        token_json = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"OAuth token exchange failed with status {e.response.status_code}", exc_info=True)
        messages.error(request, "Failed to authenticate with Connect. Please try again.")
        return redirect("labs:oauth_initiate")
    except Exception as e:
        logger.error(f"OAuth token exchange failed: {str(e)}", exc_info=True)
        messages.error(request, "Authentication service unavailable. Please try again later.")
        return redirect("labs:oauth_initiate")

    # Get user info from Connect production
    access_token = token_json["access_token"]
    # Try to introspect the token to get user information
    profile_data = introspect_token(
        access_token=access_token,
        client_id=settings.CONNECT_OAUTH_CLIENT_ID,
        client_secret=settings.CONNECT_OAUTH_CLIENT_SECRET,
        production_url=settings.CONNECT_PRODUCTION_URL,
    )

    # If we still don't have profile data, we can't authenticate
    if not profile_data:
        logger.error("Could not retrieve user information from token introspection")
        messages.error(request, "Could not retrieve your profile from Connect. Please try again.")
        return redirect("labs:oauth_initiate")

    # Calculate token expiration
    expires_in = token_json.get("expires_in", 1209600)  # Default 2 weeks
    expires_at = timezone.now() + datetime.timedelta(seconds=expires_in)

    # Fetch OIDC userinfo for reliable email
    try:
        userinfo_url = f"{settings.CONNECT_PRODUCTION_URL}/o/userinfo/"
        userinfo_resp = httpx.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if userinfo_resp.status_code == 200:
            userinfo = userinfo_resp.json()
            if userinfo.get("email"):
                profile_data["email"] = userinfo["email"]
                logger.info(f"Got email from OIDC userinfo for {profile_data.get('username')}")
    except Exception:
        logger.warning("Failed to fetch OIDC userinfo", exc_info=True)

    # Fetch organization data from production API
    org_data = fetch_user_organization_data(access_token)

    # Warn user if organization data fetch failed
    if not org_data:
        logger.warning(f"Failed to fetch organization data for user {profile_data.get('username')}")
        messages.warning(
            request,
            "Could not load your organizations and opportunities from Connect. "
            "You may need to log out and try again.",
        )

    # org_data now includes a 'user' object with email and commcare_username.
    # For Dimagi staff the CommCareHQ username IS their @dimagi.com address, so
    # use it as a fallback when OAuth introspection didn't return an email.
    if org_data and not profile_data.get("email"):
        user_info = org_data.get("user", {})
        email = user_info.get("email") or user_info.get("commcare_username", "")
        if email:
            profile_data["email"] = email
            logger.debug(f"Resolved email from org data for user: {profile_data.get('username')}")

    # Store OAuth data in session (NO database writes)
    request.session["labs_oauth"] = {
        "access_token": access_token,
        "refresh_token": token_json.get("refresh_token", ""),
        "expires_at": expires_at.timestamp(),
        "user_profile": {
            "id": profile_data.get("id"),
            "username": profile_data.get("username"),
            "email": profile_data.get("email"),
            "first_name": profile_data.get("first_name", ""),
            "last_name": profile_data.get("last_name", ""),
        },
        "organization_data": org_data or {},  # Store empty dict if API fails
    }

    # Create or update Django User from OAuth profile
    first_name = profile_data.get("first_name", "")
    last_name = profile_data.get("last_name", "")
    name = f"{first_name} {last_name}".strip() or profile_data.get("username", "")

    user, created = User.objects.update_or_create(
        username=profile_data.get("username"),
        defaults={
            "email": profile_data.get("email") or None,
            "name": name,
        },
    )

    # Log the user in via Django's standard auth
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    # Clean up temporary session keys
    request.session.pop("oauth_state", None)
    request.session.pop("oauth_code_verifier", None)
    next_url = request.session.pop("oauth_next", "/labs/overview/")

    username = profile_data.get("username", "unknown")
    logger.info(f"Successfully authenticated user {username} via OAuth")

    # Use first name if available, otherwise username
    display_name = profile_data.get("first_name") or username
    messages.success(request, f"Welcome, {display_name}!")

    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/labs/overview/"

    return redirect(next_url)


def labs_logout(request: HttpRequest) -> HttpResponse:
    """
    Log out by clearing OAuth session data.

    Redirects to labs login page.
    """
    # Get username before clearing session
    username = None
    labs_oauth = request.session.get("labs_oauth")
    if labs_oauth:
        username = labs_oauth.get("user_profile", {}).get("username")

    # Django logout clears the session entirely
    logout(request)

    if username:
        logger.info(f"User {username} logged out")

    messages.info(request, "You have been logged out.")

    # Redirect to login page
    return redirect("labs:login")


def labs_dashboard(request: HttpRequest) -> HttpResponse:
    """
    Display user's organization, program, and opportunity access.

    Shows data from session with links back to production Connect.
    """
    if not request.user.is_authenticated:
        return redirect("labs:login")

    context = {
        "user": request.user,
        "connect_url": settings.CONNECT_PRODUCTION_URL,
    }

    return render(request, "labs/dashboard.html", context)
