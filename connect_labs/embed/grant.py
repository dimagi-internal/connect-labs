"""The embed token endpoint's logic: RFC 7523 §2.1, a JWT as an authorization grant.

The client's backend POSTs ``grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer``
and ``assertion=<JWT>``. The assertion's signature, by a key the client registered, is
its authentication. In order:

1. the grant type and the assertion are present;
2. the issuer is a registered, active client, and the assertion verifies against its
   keys (``keys.verify_assertion``: audience, lifetime, issuer, jti);
3. the client may assert users (phase 1: identity authorities only);
4. ``resource`` names a kind and id this client registered;
5. the subject is an active labs user labs holds a Connect token for;
6. the kind lets that user open the resource, read as them;
7. the assertion has not been used -- consumed only now, once everything else passed.

Then a bootstrap token is issued: random, single use, two minutes, stored as a checksum.
Never logs an assertion or a token, only the reason a request was refused.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from canopy_sdk.contract import JWT_BEARER_GRANT, ContractError, token_checksum
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from connect_labs.embed import keys
from connect_labs.embed.kinds import get_kind
from connect_labs.embed.models import EmbedClient, EmbedSession

logger = logging.getLogger(__name__)

BOOTSTRAP_SECONDS = 120
_FRAME_PATH = re.compile(r"^/embed/c/(?P<client>[-\w]+)/(?P<kind>[-\w]+)/(?P<id>[-\w]+)/$")


class EmbedRefused(Exception):
    """An RFC 6749 §5.2 error. ``reason`` names the failed check (safe to log);
    ``description`` is safe to send."""

    def __init__(self, code: str, description: str, reason: str, status: int = 400, client=None):
        super().__init__(description)
        self.code = code
        self.description = description
        self.reason = reason
        self.status = status
        self.client = client

    def body(self) -> dict:
        return {"error": self.code, "error_description": self.description}


@dataclass
class Issued:
    token: str
    session: EmbedSession
    embed_url: str

    def body(self) -> dict:
        return {
            "access_token": self.token,
            "token_type": "Bearer",
            "expires_in": BOOTSTRAP_SECONDS,
            "embed_url": self.embed_url,
        }


def public_base(request) -> str:
    """Labs' own origin as an embedder addresses it: ``LABS_PUBLIC_URL``, else this request's."""
    configured = urlsplit(getattr(settings, "LABS_PUBLIC_URL", "") or "")
    if configured.scheme and configured.netloc:
        return f"{configured.scheme}://{configured.netloc}"
    return f"{request.scheme}://{request.get_host()}"


def token_audiences(request) -> list[str]:
    base = public_base(request)
    return [base + reverse("embed:token"), base, base + "/"]


def parse_resource(value, base: str) -> tuple[str, str, str]:
    """``(client_id, kind, resource_id)`` from a frame URL on labs, else ``EmbedRefused``."""
    parts = urlsplit(value if isinstance(value, str) else "")
    if f"{parts.scheme}://{parts.netloc}" != base or parts.query or parts.fragment:
        raise EmbedRefused("invalid_target", "resource must be a labs embed URL.", "bad_resource")
    match = _FRAME_PATH.match(parts.path)
    if match is None:
        raise EmbedRefused("invalid_target", "resource must be a labs embed URL.", "bad_resource")
    return match["client"], match["kind"], match["id"]


def issue(form, request) -> Issued:
    """Redeem ``form`` (the POSTed form). Raises ``EmbedRefused``."""
    if form.get("grant_type") != JWT_BEARER_GRANT:
        raise EmbedRefused("unsupported_grant_type", "Only the jwt-bearer grant is served here.", "grant_type")
    assertion = form.get("assertion")
    if not assertion:
        raise EmbedRefused("invalid_request", "The assertion parameter is required.", "no_assertion")

    # --- 2. Who signed it, and does it verify. -----------------------------------------
    try:
        iss = keys.assertion_issuer(assertion)
    except ContractError as exc:
        raise EmbedRefused("invalid_client", "The assertion is not usable.", exc.code, 401) from exc
    client = EmbedClient.objects.filter(client_id=iss, active=True).first()
    if client is None:
        raise EmbedRefused("invalid_client", "Unknown or inactive client.", "unknown_client", 401)
    try:
        claims = keys.verify_assertion(assertion, client, token_audiences(request))
    except ContractError as exc:
        code = (
            "invalid_client" if exc.code in ("unknown_key", "bad_key", "bad_signature", "bad_alg") else "invalid_grant"
        )
        raise EmbedRefused(
            code,
            f"The assertion was refused: {exc.message}.",
            exc.code,
            401 if code == "invalid_client" else 400,
            client,
        ) from exc

    # --- 3. Whether this client may say who people are. --------------------------------
    if not client.identity_authority:
        raise EmbedRefused(
            "unauthorized_client", "This client may not assert users.", "not_identity_authority", 400, client
        )

    # --- 4. What it asks to show. ------------------------------------------------------
    base = public_base(request)
    path_client, kind_name, resource_id = parse_resource(claims.get("resource"), base)
    kind = get_kind(kind_name)
    entry = client.resource_entry(kind_name, resource_id) if kind is not None else None
    if path_client != client.client_id or entry is None:
        raise EmbedRefused(
            "invalid_target", "This client may not show that resource.", "resource_not_registered", 400, client
        )

    # --- 5. The person. ------------------------------------------------------------------
    user = get_user_model().objects.filter(username=claims["sub"], is_active=True).first()
    if user is None:
        raise EmbedRefused("invalid_grant", "No labs account for that person.", "unknown_user", 400, client)
    from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token

    try:
        access_token = get_valid_access_token(user)
    except ConnectTokenError:
        # Phase 2 (Connect's jwt-bearer grant) removes this: labs would then get a token
        # for the person from Connect instead of needing one stored.
        raise EmbedRefused(
            "invalid_grant", "That person has not signed in to labs yet.", "no_connect_token", 400, client
        ) from None

    # --- 6. May they open it, read as them. ----------------------------------------------
    refusal = kind.check_access(user, access_token, entry)
    if refusal:
        raise EmbedRefused("invalid_grant", refusal, "no_access", 400, client)

    # --- 7. The assertion works once. -------------------------------------------------
    from canopy_sdk.django.stores import DjangoJtiStore
    from canopy_sdk.stores import Replayed

    try:
        DjangoJtiStore().consume([(f"embed:{client.client_id}", claims["jti"], int(claims["exp"]))])
    except Replayed:
        raise EmbedRefused("invalid_grant", "This assertion has already been used.", "replayed", 400, client) from None

    token = secrets.token_urlsafe(32)
    session = EmbedSession.objects.create(
        client=client,
        user=user,
        kind=kind_name,
        resource_id=str(resource_id),
        entry=entry,
        bootstrap_checksum=token_checksum(token),
        bootstrap_expires_at=timezone.now() + timedelta(seconds=BOOTSTRAP_SECONDS),
    )
    embed_url = base + reverse("embed:frame", args=[client.client_id, kind_name, resource_id])
    return Issued(token=token, session=session, embed_url=embed_url)
