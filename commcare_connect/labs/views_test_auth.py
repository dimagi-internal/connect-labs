"""
DEBUG-only view to inject a real OAuth session for Playwright E2E tests.

Reads the CLI token from TokenManager, introspects it against production,
fetches org data, and writes labs_oauth into the Django session — exactly
like BaseLabsURLTest does for the Django test client.
"""

import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth import login
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from commcare_connect.labs.integrations.connect.cli import TokenManager
from commcare_connect.labs.integrations.connect.oauth import fetch_user_organization_data, introspect_token
from commcare_connect.users.models import User

logger = logging.getLogger(__name__)


@require_GET
def test_auth_view(request):
    """Inject a real OAuth session for E2E testing. DEBUG only."""
    if not settings.DEBUG:
        return JsonResponse({"error": "Only available in DEBUG mode"}, status=403)

    profile = request.GET.get("profile")
    token_manager = TokenManager(profile=profile)
    token_data = token_manager.load_token()

    if not token_data:
        return JsonResponse({"error": "No CLI token found. Run: python manage.py get_cli_token"}, status=401)

    if token_manager.is_expired():
        return JsonResponse({"error": "CLI token expired. Run: python manage.py get_cli_token"}, status=401)

    access_token = token_data["access_token"]

    # Introspect token to get user profile
    profile_data = introspect_token(
        access_token=access_token,
        client_id=settings.CONNECT_OAUTH_CLIENT_ID,
        client_secret=settings.CONNECT_OAUTH_CLIENT_SECRET,
        production_url=settings.CONNECT_PRODUCTION_URL,
    )
    if not profile_data:
        return JsonResponse({"error": "Token introspection failed"}, status=401)

    # Fetch org data
    org_data = fetch_user_organization_data(access_token)
    if not org_data:
        return JsonResponse({"error": "Failed to fetch organization data"}, status=500)

    # org_data now includes a 'user' object with email and commcare_username.
    # For Dimagi staff the CommCareHQ username IS their @dimagi.com address, so
    # use it as a fallback when OAuth introspection didn't return an email.
    if not profile_data.get("email"):
        user_info = org_data.get("user", {})
        email = user_info.get("email") or user_info.get("commcare_username", "")
        if email:
            profile_data["email"] = email

    # Convert expires_at from ISO string to timestamp
    if "expires_at" in token_data and isinstance(token_data["expires_at"], str):
        expires_at = datetime.fromisoformat(token_data["expires_at"]).timestamp()
    else:
        expires_in = token_data.get("expires_in", 1209600)
        expires_at = (timezone.now() + timezone.timedelta(seconds=expires_in)).timestamp()

    # Create or update Django User and log in (mirrors OAuth callback)
    first_name = profile_data.get("first_name") or ""
    last_name = profile_data.get("last_name") or ""
    name = f"{first_name} {last_name}".strip() or profile_data.get("username", "")
    defaults = {"name": name}
    email = profile_data.get("email", "")
    if email:
        defaults["email"] = email
    user, _ = User.objects.update_or_create(
        username=profile_data.get("username"),
        defaults=defaults,
    )
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    # Write session — same structure as the OAuth callback
    request.session["labs_oauth"] = {
        "access_token": access_token,
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_at": expires_at,
        "user_profile": {
            "id": profile_data.get("id"),
            "username": profile_data.get("username"),
            "email": profile_data.get("email"),
            "first_name": profile_data.get("first_name", ""),
            "last_name": profile_data.get("last_name", ""),
        },
        "organization_data": org_data,
    }

    # Include org/program data so e2e conftest can auto-detect IDs
    organizations = org_data.get("organizations", [])
    programs = org_data.get("programs", [])

    return JsonResponse(
        {
            "success": True,
            "username": profile_data.get("username"),
            "organizations": [
                {"id": o.get("id"), "slug": o.get("slug"), "name": o.get("name")} for o in organizations
            ],
            "programs": [{"id": p.get("id"), "name": p.get("name")} for p in programs],
        }
    )
