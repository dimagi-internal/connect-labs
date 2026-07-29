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

    **There is deliberately no default.** The poller must be named explicitly,
    either by ``manage.py pulse_poller --set <username>`` (stored in the DB) or
    by the ``PULSE_POLLER_USERNAME`` setting.

    An earlier version fell back to whichever user happened to have a stored
    Connect token, on the theory that ingesting *something* beat ingesting
    nothing. That was wrong, and prod proved it within minutes: it picked an
    account with narrower org membership and every headline figure came out
    understated roughly 5x — 207 opportunities instead of 497, 317,365
    lifetime services instead of 1,648,363. Nothing errored. The display was
    confidently wrong, which is far worse than visibly empty.

    Scope follows this user's Connect org membership, so guessing at it means
    guessing at every number on a funder's screen. Refusing to start is the
    correct failure: it is loud, it is diagnosable, and it cannot mislead.
    """
    user_model = get_user_model()

    # DB override first, then settings. The override exists because the env var
    # lives in an ECS task definition — changing it needs AWS access and a
    # redeploy, while `pulse_poller --set` runs through run-labs-command.
    username = ""
    override = PulseScalar.objects.filter(key=SCALAR_POLLER).first()
    if override:
        username = (override.value or {}).get("username", "") or ""
    source = "pulse_poller override"
    if not username:
        username = getattr(settings, "PULSE_POLLER_USERNAME", "") or ""
        source = "PULSE_POLLER_USERNAME"

    if not username:
        raise PulseAuthError(
            "No Pulse poller configured. Scope — and therefore every figure on a "
            "Pulse display — follows one Connect account's org membership, so it "
            "must be named explicitly rather than guessed.\n"
            "Set it with:  manage.py pulse_poller --set <username>\n"
            "List candidates with:  manage.py pulse_poller --list"
        )

    try:
        return user_model.objects.get(username=username)
    except user_model.DoesNotExist:
        raise PulseAuthError(
            f"Pulse poller {username!r} (from {source}) does not exist in labs. "
            "That user must have logged into labs in a browser at least once. "
            "List candidates with: manage.py pulse_poller --list"
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
