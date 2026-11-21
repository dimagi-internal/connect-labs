"""Who labs reads Connect Workers as.

A sync runs headless, so it needs a Connect identity of its own. It is Pulse's
poller (`pulse.client.get_poller_user`), whose org membership already defines what
Pulse covers, unless `WORKERS_SYNC_USERNAME` names a different labs user. Either way
the profile covers exactly the opportunities that identity can read, and says so.

No identity, or one whose Connect refresh token has expired, fails LOUDLY with what
to do about it -- never an empty sync that reads as "this worker has done nothing".
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model

from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
from connect_labs.labs.integrations.connect.export_client import ExportAPIClient

DEFAULT_TIMEOUT = 180.0


class WorkerSyncAuthError(RuntimeError):
    """No usable Connect token for the Connect Worker sync identity."""


def get_sync_user():
    """The labs user the sync reads Connect as: WORKERS_SYNC_USERNAME, else Pulse's poller."""
    username = (getattr(settings, "WORKERS_SYNC_USERNAME", "") or "").strip()
    if username:
        try:
            return get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist:
            raise WorkerSyncAuthError(
                f"WORKERS_SYNC_USERNAME is {username!r}, but no such labs user exists. That user must "
                "have logged into labs in a browser at least once, or unset the setting to use Pulse's poller."
            )

    from connect_labs.pulse.client import PulseAuthError, get_poller_user

    try:
        return get_poller_user()
    except PulseAuthError as exc:
        raise WorkerSyncAuthError(
            f"The Connect Worker sync reads Connect as Pulse's poller, which is not usable: {exc}\n"
            "Or name a sync identity of its own with the WORKERS_SYNC_USERNAME setting."
        ) from exc


def get_access_token() -> str:
    user = get_sync_user()
    try:
        return get_valid_access_token(user)
    except ConnectTokenError as exc:
        raise WorkerSyncAuthError(
            f"The Connect Worker sync identity {user.username!r} has no usable Connect token: {exc}\n"
            "Its refresh token expires if it does not log into labs for a while: log into labs in a browser "
            "as that user to renew it."
        ) from exc


def get_sync_client(timeout: float = DEFAULT_TIMEOUT) -> ExportAPIClient:
    return ExportAPIClient(base_url=settings.CONNECT_PRODUCTION_URL, access_token=get_access_token(), timeout=timeout)
