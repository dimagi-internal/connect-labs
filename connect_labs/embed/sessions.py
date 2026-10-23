"""Trading a bootstrap token for an embed session, and the cookie that carries it.

The cookie is ``Secure; HttpOnly; SameSite=None; Partitioned``. ``SameSite=None`` is the
only way a cookie reaches labs in a frame on another site at all; ``Partitioned`` (CHIPS)
keys it to the top-level site that framed labs, so a different site framing labs later
has no session to use. One cookie per resource (``labs_embed_<kind>_<id>``), so two
embeds on one page keep their own sessions.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta
from http.cookies import Morsel

from canopy_sdk.contract import token_checksum
from django.db import transaction
from django.utils import timezone

from connect_labs.embed.models import EmbedSession

COOKIE_PREFIX = "labs_embed_"
_COOKIE_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class SessionRefused(Exception):
    def __init__(self, description: str, reason: str):
        super().__init__(description)
        self.description = description
        self.reason = reason


def cookie_name(kind: str, resource_id: str) -> str:
    return COOKIE_PREFIX + _COOKIE_SAFE.sub("-", f"{kind}_{resource_id}")


def start(token: str, client_id: str, kind: str, resource_id: str) -> tuple[EmbedSession, str]:
    """Consume ``token`` (once, in its window, for exactly this client and resource) and
    return the session with its raw session token. Raises ``SessionRefused``."""
    if not isinstance(token, str) or not token or len(token) > 200:
        raise SessionRefused("No embed token was given.", "no_token")
    now = timezone.now()
    with transaction.atomic():
        session = (
            EmbedSession.objects.select_for_update()
            .select_related("client")
            .filter(bootstrap_checksum=token_checksum(token))
            .first()
        )
        if session is None:
            raise SessionRefused("This embed token is not valid.", "unknown_token")
        if session.started_at is not None:
            raise SessionRefused("This embed token has already been used.", "token_used")
        if session.bootstrap_expires_at <= now:
            raise SessionRefused("This embed token has expired.", "token_expired")
        if (session.client.client_id, session.kind, session.resource_id) != (client_id, kind, str(resource_id)):
            raise SessionRefused("This embed token is for something else.", "wrong_resource")
        if not session.client.active:
            raise SessionRefused("This embed is switched off.", "client_inactive")
        raw = secrets.token_urlsafe(32)
        session.started_at = now
        session.session_checksum = token_checksum(raw)
        session.expires_at = now + timedelta(seconds=session.client.session_seconds)
        session.save(update_fields=["started_at", "session_checksum", "expires_at"])
    return session, raw


def lookup(raw: str) -> EmbedSession | None:
    """The live session a cookie value names, or None."""
    if not raw or len(raw) > 200:
        return None
    session = (
        EmbedSession.objects.select_related("client", "user")
        .filter(session_checksum=token_checksum(raw), revoked_at__isnull=True)
        .first()
    )
    if session is None or not session.live or not session.user.is_active:
        return None
    return session


class _PartitionedMorsel(Morsel):
    """Python adds ``partitioned`` to Morsel only in 3.14."""

    def OutputString(self, attrs=None):
        return super().OutputString(attrs) + "; Partitioned"


def set_session_cookie(response, session: EmbedSession, raw: str) -> None:
    name = cookie_name(session.kind, session.resource_id)
    response.set_cookie(
        name,
        raw,
        max_age=session.client.session_seconds,
        path="/",
        secure=True,
        httponly=True,
        samesite="None",
    )
    plain = response.cookies[name]
    partitioned = _PartitionedMorsel()
    partitioned.set(plain.key, plain.value, plain.coded_value)
    partitioned.update({k: v for k, v in plain.items() if v})
    dict.__setitem__(response.cookies, name, partitioned)
