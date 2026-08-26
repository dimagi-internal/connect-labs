"""
Connect OAuth Helper Functions.

Shared OAuth utilities for both web and CLI authentication flows.
"""

import hashlib
import logging

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def refresh_connect_token(request) -> bool:
    """
    Attempt to refresh the Connect OAuth token using the stored refresh_token
    in ``request.session["labs_oauth"]``.

    On success, mutates the session payload in-place (new access_token,
    refresh_token, expires_at) and returns True. On failure, leaves the
    session unchanged and returns False.

    Mirrors the CCHQ ``_refresh_token`` pattern so callers can attempt a
    silent refresh before deciding the user must re-authenticate.
    """
    from django.utils import timezone

    labs_oauth = request.session.get("labs_oauth") or {}
    refresh_token = labs_oauth.get("refresh_token")
    if not refresh_token:
        logger.debug("No refresh_token in labs_oauth; cannot refresh")
        return False

    client_id = getattr(settings, "CONNECT_OAUTH_CLIENT_ID", "")
    client_secret = getattr(settings, "CONNECT_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        logger.warning("Connect OAuth client credentials not configured for token refresh")
        return False

    token_url = f"{settings.CONNECT_PRODUCTION_URL}/o/token/"
    try:
        response = httpx.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
            timeout=30.0,
        )
        if response.status_code != 200:
            logger.warning(f"Connect token refresh failed: {response.status_code} - {response.text}")
            return False

        token_data = response.json()
        new_oauth = dict(labs_oauth)
        new_oauth["access_token"] = token_data["access_token"]
        new_oauth["refresh_token"] = token_data.get("refresh_token", refresh_token)
        new_oauth["expires_at"] = timezone.now().timestamp() + token_data.get("expires_in", 3600)

        request.session["labs_oauth"] = new_oauth
        if hasattr(request.session, "modified"):
            request.session.modified = True
        return True
    except Exception as e:
        logger.warning(f"Connect token refresh error: {e}")
        return False


# How long a successful org tree is reused. Production computes this list by
# annotating every opportunity the caller can see with Count("uservisit") over its
# largest table, so the cost scales with the caller's access: measured at 0.28-0.37s
# for an account with almost no membership and 4.7-15.0s for broad Dimagi staff
# access. Labs was asking 182-455 times a day against 1-7 logins (#1298).
#
# An hour, not the 300s #1310 shipped, because almost nothing downstream needs this
# list to be current to the minute and the one field that looks time-sensitive is not
# read from here anyway. `visit_count` reaches pipelines through
# labs_context["opportunity"], which context.py builds from get_org_data(request) --
# the SESSION copy, written once at login and thereafter only by the refresh button.
# A long-lived session is already running on a count that is hours or days old, so an
# extra hour at the moment of login is noise against that, not a new class of
# staleness. Anything that genuinely needs an exact visit count should ask for it
# directly rather than read it off this payload.
#
# The escape hatch is what makes the hour safe: "Refresh organization data" forces a
# live fetch and repopulates the entry the next login will read.
_ORG_DATA_CACHE_TTL = 3600


def _org_data_cache_key(access_token: str, owner: str | None = None) -> str:
    """Cache key for one org tree, keyed on its OWNER where that can be proven.

    Hashed either way: this key goes to Redis, which is not a credential store, and
    the usernames here are frequently @dimagi.com addresses.

    ``owner`` must be derived FROM the credential -- the username OAuth introspection
    returned for this token, or the user whose ``UserConnectToken`` this is -- never
    from an ambient ``request.user`` that merely happens to be nearby. That is what
    keeps the key's scope identical to the payload's authorization scope; get it
    wrong and one user is served another's org tree.

    #1310 keyed on the token alone. That is trivially safe but could never serve the
    case the issue was about: a sign-in mints a new token, so login always missed, a
    silent ``refresh_connect_token`` rotation orphaned a good entry, and the ~400
    calls a day that dominate the volume warmed nothing for logins despite carrying a
    different token for the same person. Callers that cannot prove alignment (CLI,
    management commands, Celery) still pass no owner and keep that behaviour, where
    the worst case is a miss.
    """
    if owner:
        return f"labs:org_data:owner:{hashlib.sha256(owner.encode()).hexdigest()}"
    return f"labs:org_data:token:{hashlib.sha256(access_token.encode()).hexdigest()}"


def fetch_user_organization_data(
    access_token: str, force_refresh: bool = False, owner: str | None = None
) -> dict | None:
    """
    Fetch user's organizations, programs, and opportunities from Connect production.

    Cached for ``_ORG_DATA_CACHE_TTL``, keyed by ``owner`` when the caller can prove
    the token belongs to them and by the token otherwise -- see
    ``_org_data_cache_key``. Only a SUCCESSFUL fetch is cached: caching the ``None``
    would make one network blip look like a revoked permission for the whole window,
    which is the failure #1195 was filed for.

    Args:
        access_token: OAuth Bearer token for Connect production
        force_refresh: skip the cache and repopulate it. For the "refresh org data"
            view, whose entire purpose is to defeat staleness.
        owner: username the token belongs to, derived from the token itself. Omit
            rather than guess.

    Returns:
        Dict with 'organizations', 'programs', 'opportunities' keys, or None if fails.
    """
    cache_key = _org_data_cache_key(access_token, owner)
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        response = httpx.get(
            f"{settings.CONNECT_PRODUCTION_URL}/export/opp_org_program_list/",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,  # Increased timeout from 10 to 30 seconds
            # Production emits http:// URLs behind its proxy (gunicorn without
            # --forwarded-allow-ips) and the proxy 301s them back to https://.
            # Every other caller of this API sets this for that reason — the
            # export client, _fetch_endpoint, and the access probe after
            # connect-labs#1214. Bare, a redirect arrives with an unparseable
            # body, becomes None, and surfaces as a denial or UPSTREAM_ERROR.
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
        cache.set(cache_key, data, _ORG_DATA_CACHE_TTL)
        return data

    except Exception as e:
        logger.error(f"Failed to fetch organization data: {str(e)}", exc_info=True)
        return None


def introspect_token(access_token: str, client_id: str, client_secret: str, production_url: str) -> dict | None:
    """
    Introspect OAuth token to get user profile information.

    Calls the OAuth introspection endpoint to validate token and retrieve
    user information including ID, username, and email.

    Args:
        access_token: OAuth Bearer token to introspect
        client_id: OAuth client ID
        client_secret: OAuth client secret (required for introspection)
        production_url: Base URL of production Connect instance

    Returns:
        Dict with user profile {'id', 'username', 'email', 'first_name', 'last_name'}
        or None if introspection fails or token is invalid.

    Example:
        >>> profile = introspect_token(
        ...     access_token="abc123",
        ...     client_id="my_client",
        ...     client_secret="secret",
        ...     production_url="https://connect.dimagi.com"
        ... )
        >>> if profile:
        ...     print(f"User: {profile['username']}")
    """
    try:
        introspect_response = httpx.post(
            f"{production_url}/o/introspect/",
            data={"token": access_token},
            auth=(client_id, client_secret),
            timeout=10,
        )

        if introspect_response.status_code != 200:
            logger.warning(f"Token introspection failed with status {introspect_response.status_code}")
            return None

        introspect_data = introspect_response.json()

        if not introspect_data.get("active"):
            logger.warning("Token is not active")
            return None

        # Extract user profile from introspection response.
        # sub may contain the CommCareHQ username (e.g. mtheis@dimagi.com for Dimagi staff).
        # Use it as an email fallback if it looks like an email address.
        sub = introspect_data.get("sub", "")
        sub_email = sub if "@" in str(sub) else ""

        profile_data = {
            "id": introspect_data.get("user_id") or sub or 0,
            "username": introspect_data.get("username"),
            "email": introspect_data.get("email", "") or sub_email,
            "first_name": introspect_data.get("given_name", ""),
            "last_name": introspect_data.get("family_name", ""),
        }

        logger.info(f"Token introspection successful for user: {profile_data.get('username')}")
        return profile_data

    except httpx.HTTPError as e:
        logger.error(f"HTTP error during token introspection: {str(e)}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Failed to introspect token: {str(e)}", exc_info=True)
        return None
