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
from connect_labs.pulse.models import PulseScalar

# DB override for the poller identity, settable via `manage.py pulse_poller`.
SCALAR_POLLER = "poller"

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 180.0


class PulseAuthError(RuntimeError):
    """No usable Connect token for the poller user.

    Raised rather than returning None so a caller cannot accidentally treat
    "cannot reach Connect" as "Connect returned nothing".
    """


def get_poller_user():
    """The Django user whose Connect membership defines Pulse's scope.

    Configured explicitly via ``PULSE_POLLER_USERNAME``. When that is unset we
    fall back to whichever user has the most recently refreshed Connect token,
    and say so loudly in the log.

    The fallback exists because the alternative is worse: an unset env var
    would otherwise mean a deployed Pulse silently ingests nothing, and the
    display would show an empty screen with no indication that the cause is a
    missing setting. Falling back makes it work and complain; failing shut
    makes it look broken for a reason nobody can see from the page.

    Which user matters, so this is a setting to fill in rather than rely on —
    scope (and therefore every headline number) follows that user's org
    membership.
    """
    user_model = get_user_model()

    # Resolution order: DB override, then env, then fallback. The DB override
    # exists because the env var lives in an ECS task definition — changing it
    # needs AWS access and a redeploy, whereas the poller identity is exactly
    # the thing you discover you got wrong *after* looking at the numbers.
    # `manage.py pulse_poller --set <username>` can be run through the existing
    # run-labs-command workflow, so correcting scope doesn't need either.
    username = ""
    override = PulseScalar.objects.filter(key=SCALAR_POLLER).first()
    if override:
        username = (override.value or {}).get("username", "") or ""
    if not username:
        username = getattr(settings, "PULSE_POLLER_USERNAME", "") or ""

    if username:
        try:
            return user_model.objects.get(username=username)
        except user_model.DoesNotExist:
            raise PulseAuthError(
                f"PULSE_POLLER_USERNAME={username!r} does not exist in labs. "
                "The user must have logged into labs in a browser at least once."
            )

    from connect_labs.labs.models import UserConnectToken

    token = UserConnectToken.objects.order_by("-updated_at").first()
    if token is None:
        raise PulseAuthError(
            "PULSE_POLLER_USERNAME is not set and no user has a stored Connect token. "
            "Set PULSE_POLLER_USERNAME to a user who has logged into labs in a browser."
        )
    logger.warning(
        "[pulse] PULSE_POLLER_USERNAME is unset; falling back to %r. Scope (and every "
        "headline figure) follows that user's org membership — set the env var explicitly.",
        token.user.username,
    )
    return token.user


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
