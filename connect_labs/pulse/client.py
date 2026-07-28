"""Resolve the Connect export client the pollers run as.

Pulse is a background job, so it cannot use ``request.session["labs_oauth"]``
like the rest of labs. It runs as a designated Django user whose Connect
refresh token is already stored by the normal browser login, via
``connect_tokens.get_valid_access_token`` — which handles refresh and whose
docstring anticipates exactly this use.

The operational consequence is worth stating plainly: **refresh tokens have an
absolute lifetime.** If the poller user does not log into labs for a long
enough stretch, ingest stops. That is a real steady state, not an edge case,
and it must surface as visible unhealthy state rather than as a screen quietly
showing yesterday's numbers under a green LIVE badge.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model

from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
from connect_labs.labs.integrations.connect.export_client import ExportAPIClient

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 180.0


class PulseAuthError(RuntimeError):
    """No usable Connect token for the poller user.

    Raised rather than returning None so a caller cannot accidentally treat
    "cannot reach Connect" as "Connect returned nothing".
    """


def get_poller_user():
    """The Django user whose Connect membership defines Pulse's scope."""
    username = getattr(settings, "PULSE_POLLER_USERNAME", "") or ""
    if not username:
        raise PulseAuthError(
            "PULSE_POLLER_USERNAME is not set. Pulse needs a designated user whose "
            "Connect org membership defines what the dashboard can see."
        )
    user_model = get_user_model()
    try:
        return user_model.objects.get(username=username)
    except user_model.DoesNotExist:
        raise PulseAuthError(
            f"PULSE_POLLER_USERNAME={username!r} does not exist in labs. "
            "The user must have logged into labs in a browser at least once."
        )


def get_access_token() -> str:
    try:
        return get_valid_access_token(get_poller_user())
    except ConnectTokenError as exc:
        # Includes the expired-refresh-token case, which is the failure mode
        # most likely to be mistaken for "no new data".
        raise PulseAuthError(str(exc)) from exc


def get_client(timeout: float = DEFAULT_TIMEOUT) -> ExportAPIClient:
    return ExportAPIClient(
        base_url=settings.CONNECT_PRODUCTION_URL,
        access_token=get_access_token(),
        timeout=timeout,
    )


def fetch_json(client: ExportAPIClient, path: str) -> dict:
    """GET a non-paginated export endpoint.

    ``ExportAPIClient`` only exposes ``paginate``/``fetch_all``, which assume a
    ``{"next": ..., "results": [...]}`` envelope. ``opp_org_program_list``
    returns a bare object instead, so it needs a plain GET — reusing the
    client's already-configured auth and version headers rather than building a
    second HTTP client with its own drift risk.
    """
    response = client.http_client.get(f"{client.base_url}{path}")
    response.raise_for_status()
    return response.json()
