"""Canopy acting as a labs visitor: the jwt-bearer grant and the tokens it issues.

This is the HOST half of MCP Enterprise-Managed Authorization (the ID-JAG
flow), as pinned by canopy-web's host-grant contract v1 and designed in
canopy-web ``docs/superpowers/specs/2026-09-26-embedded-caller-delegation-design.md``.
The principle: **the party that signed the user in issues the grant; canopy only
redeems it.** Canopy never holds a key labs trusts to say who a user is.

1. When the canopy panel opens on a registered page, labs signs an ID-JAG for
   the visitor with its own key (``connect_labs.labs.canopy.id_jag_for``) and
   hands it to canopy with the visitor assertion.
2. Canopy redeems it HERE, at labs' ordinary token endpoint, with the RFC 7523
   jwt-bearer grant: authenticating as itself with ``private_key_jwt`` (its key
   published through its Client ID Metadata Document) and proving possession of
   a DPoP key (RFC 9449). Labs issues a short access token — no refresh token —
   bound to that DPoP key, to the visitor, to canopy, and to the page's scopes.
3. Canopy calls labs' MCP with ``Authorization: DPoP <token>`` plus a fresh
   proof per request. ``DPoPGate`` checks the proof, the verifier checks the
   binding, and the tool runs AS THE VISITOR, limited to the tools the scopes
   map to (``SCOPE_TOOLS``).

Everything is off until ``CANOPY_CLIENT_ID`` is set. Tokens that carry no DPoP
binding — PATs and ordinary MCP OAuth sign-ins — never reach this module and
behave exactly as before.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import random
import secrets
from datetime import datetime, timedelta, timezone

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.views import TokenView

from . import client_metadata, dpop, oauth
from .dpop import JoseError

logger = logging.getLogger(__name__)

JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ID_JAG_TYP = "oauth-id-jag+jwt"

#: The contract's ceilings. Access tokens live at most 15 minutes, a grant at
#: most 5, a client assertion at most 1.
ACCESS_TOKEN_TTL_SECONDS = 900
MAX_ID_JAG_LIFETIME_SECONDS = 300
MAX_CLIENT_ASSERTION_LIFETIME_SECONDS = 60
#: Clock skew allowed on ``exp`` / ``iat`` of the two grant-time JWTs.
LEEWAY_SECONDS = 30

#: What each scope unlocks. The ONLY place a delegated token's reach is
#: decided: a tool not listed under one of the token's scopes is neither listed
#: nor callable with it. v1 is read-only on purpose — a write reached through a
#: page should come with a write scope AND a per-call confirmation from the
#: visitor (the design's decision 3), neither of which exists yet.
SCOPE_TOOLS: dict[str, frozenset[str]] = {
    "marketplace:read": frozenset({"marketplace_orgs_get", "marketplace_rounds_list"}),
}

#: The DPoP key thumbprint proved on THIS request, set by ``DPoPGate`` and read
#: by the token verifier. ``None`` means the request carried no DPoP proof.
presented_dpop_jkt: contextvars.ContextVar[str | None] = contextvars.ContextVar("presented_dpop_jkt", default=None)


def canopy_client_id() -> str:
    """The one client allowed to redeem grants, or "" when the grant is disabled."""
    return (getattr(settings, "CANOPY_CLIENT_ID", "") or "").strip()


def grant_enabled() -> bool:
    """Both a canopy client and a public origin (the issuer every JWT names) are needed."""
    return bool(canopy_client_id()) and oauth.sign_in_configured()


def issuer() -> str:
    return oauth.public_base_url()


def token_endpoint() -> str:
    return f"{issuer()}/o/token/"


def tools_for_scopes(scopes) -> frozenset[str]:
    allowed: set[str] = set()
    for scope in scopes or ():
        allowed |= SCOPE_TOOLS.get(scope, frozenset())
    return frozenset(allowed)


def allowed_tools(access_token) -> frozenset[str] | None:
    """The tools a caller may reach, or ``None`` for "every tool" (PATs, OAuth sign-ins).

    Only a token issued by the delegated grant is limited. It is recognised by
    what the verifier stamped on it, and anything that says ``delegated`` is
    limited to its scopes' tools — never widened.
    """
    if access_token is None:
        return None
    claims = getattr(access_token, "claims", None) or {}
    if claims.get("auth_method") != "delegated":
        return None
    return tools_for_scopes(getattr(access_token, "scopes", None) or [])


# ---------------------------------------------------------------------------
# The token endpoint: labs' own, plus one grant type
# ---------------------------------------------------------------------------

_toolkit_token_view = TokenView.as_view()


@csrf_exempt
def token_endpoint_view(request):
    """``/o/token/``: the jwt-bearer grant here, every other grant to django-oauth-toolkit unchanged."""
    if request.method == "POST" and request.POST.get("grant_type") == JWT_BEARER_GRANT:
        return redeem_grant(request)
    return _toolkit_token_view(request)


def _error(code: str, description: str, status: int = 400) -> JsonResponse:
    response = JsonResponse({"error": code, "error_description": description}, status=status)
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    if code == "invalid_dpop_proof":
        response["WWW-Authenticate"] = f'DPoP error="invalid_dpop_proof", algs="{" ".join(dpop.ALLOWED_ALGS)}"'
    return response


class _Refused(Exception):
    def __init__(self, code: str, description: str, status: int = 400, reason: str = ""):
        super().__init__(description)
        self.code = code
        self.description = description
        self.status = status
        self.reason = reason or code


def redeem_grant(request):
    """RFC 7523 jwt-bearer: redeem an ID-JAG labs issued, for a DPoP-bound MCP token."""
    if not grant_enabled():
        return _error("unsupported_grant_type", "This server does not accept the jwt-bearer grant.")
    try:
        body = _redeem(request)
    except _Refused as refused:
        # The reason code names the failed check. Never the tokens or proofs.
        logger.warning("delegated grant refused: %s (%s)", refused.reason, refused.code)
        return _error(refused.code, refused.description, refused.status)
    response = JsonResponse(body)
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


def _redeem(request) -> dict:
    params = request.POST
    client_id = canopy_client_id()

    # --- 1. The client: only canopy, authenticated by private_key_jwt. -------
    presented_client = params.get("client_id", "")
    if not dpop.constant_time_equal(presented_client, client_id):
        raise _Refused("invalid_client", "This client may not use this grant.", 401, "unknown_client")
    if params.get("client_assertion_type") != CLIENT_ASSERTION_TYPE or not params.get("client_assertion"):
        raise _Refused("invalid_client", "Authenticate with private_key_jwt.", 401, "no_client_assertion")
    client_claims, client_jkt = _verify_client_assertion(params["client_assertion"], client_id)

    # --- 2. Possession of the DPoP key the token will be bound to. ----------
    proofs = request.headers.get("DPoP")
    if not proofs or "," in proofs:
        raise _Refused("invalid_dpop_proof", "Send exactly one DPoP proof.", reason="no_dpop_proof")
    try:
        jkt, proof_jti, proof_iat = dpop.verify_dpop_proof(proofs, htm="POST", htu=token_endpoint())
    except JoseError as exc:
        raise _Refused("invalid_dpop_proof", f"The DPoP proof was refused: {exc.message}.", reason=exc.code) from exc
    if dpop.constant_time_equal(jkt, client_jkt):
        # The contract keeps the two keys apart: the client key authenticates
        # canopy, the DPoP key is what a stolen access token would need too.
        raise _Refused("invalid_dpop_proof", "The DPoP key must not be the client's key.", reason="dpop_is_client_key")

    # --- 3. The grant itself: signed by labs, for canopy, for this MCP. ----
    if params.get("assertion") is None:
        raise _Refused("invalid_request", "The assertion parameter is required.")
    grant, user = _verify_id_jag(params["assertion"], client_id)

    requested_resource = params.get("resource")
    if requested_resource is not None and not dpop.constant_time_equal(requested_resource, oauth.resource_url()):
        raise _Refused("invalid_target", "Tokens are issued only for this server's MCP endpoint.")

    granted = [s for s in str(grant.get("scope", "")).split() if s in SCOPE_TOOLS]
    requested = params.get("scope")
    if requested is not None:
        wanted = requested.split()
        if not wanted or not set(wanted) <= set(granted):
            raise _Refused("invalid_scope", "The requested scope is not within the grant.")
        granted = [s for s in granted if s in wanted]
    if not granted:
        raise _Refused("invalid_scope", "The grant carries no scope this server offers.")

    # --- 4. Every statement works once. Consumed only after all checks pass. --
    try:
        dpop.consume_jtis(
            [
                ("client", client_claims["jti"], int(client_claims["exp"])),
                ("idjag", grant["jti"], int(grant["exp"])),
                (f"dpop:{jkt}", proof_jti, proof_iat + dpop.DPOP_IAT_WINDOW_SECONDS),
            ]
        )
    except JoseError as exc:
        raise _Refused(
            "invalid_grant", "This grant, client assertion or proof has already been used.", reason="replayed"
        ) from exc

    return _issue(user, client_id, granted, jkt, grant)


def _verify_client_assertion(assertion: str, client_id: str) -> tuple[dict, str]:
    """Verify canopy's ``private_key_jwt`` against the keys its metadata document names."""
    try:
        header = dpop.unverified_header(assertion)
    except JoseError as exc:
        raise _Refused("invalid_client", "The client assertion is not usable.", 401, exc.code) from exc
    try:
        jwk = _client_key(client_id, header.get("kid"))
        key = dpop.public_key_for(jwk, header["alg"])
        claims = dpop.decode(
            assertion,
            key,
            alg=header["alg"],
            audience=[issuer(), token_endpoint()],
            required=("iss", "sub", "aud", "exp", "iat", "jti"),
            leeway=LEEWAY_SECONDS,
        )
    except client_metadata.MetadataError as exc:
        logger.warning("canopy client metadata unusable: %s", exc)
        raise _Refused("invalid_client", "The client's keys could not be read.", 401, "client_metadata") from exc
    except JoseError as exc:
        raise _Refused("invalid_client", "The client assertion was refused.", 401, f"client_{exc.code}") from exc
    if not (dpop.constant_time_equal(claims["iss"], client_id) and dpop.constant_time_equal(claims["sub"], client_id)):
        raise _Refused(
            "invalid_client", "The client assertion must be issued by the client, about itself.", 401, "client_iss_sub"
        )
    if claims["exp"] - claims["iat"] > MAX_CLIENT_ASSERTION_LIFETIME_SECONDS:
        raise _Refused("invalid_client", "The client assertion may live at most 60 seconds.", 401, "client_lifetime")
    try:
        dpop.check_jti(claims)
    except JoseError as exc:
        raise _Refused("invalid_client", "The client assertion needs a jti.", 401, "client_jti") from exc
    return claims, dpop.jwk_thumbprint(jwk)


def _client_key(client_id: str, kid) -> dict:
    keys = client_metadata.client_signing_keys(client_id)
    found = _select_key(keys, kid)
    if found is None:
        # Perhaps canopy rotated its key since the cache was filled.
        refreshed = client_metadata.client_signing_keys_refreshed(client_id)
        found = _select_key(refreshed or [], kid)
    if found is None:
        raise JoseError("unknown_key", "no published client key matches the assertion's kid")
    return found


def _select_key(keys: list[dict], kid) -> dict | None:
    signing = [k for k in keys if k.get("use") in (None, "sig")]
    if kid is None:
        # Without a kid the choice is only unambiguous when there is one key.
        return signing[0] if len(signing) == 1 else None
    matches = [k for k in signing if dpop.constant_time_equal(k.get("kid"), kid)]
    return matches[0] if len(matches) == 1 else None


def _verify_id_jag(assertion: str, client_id: str):
    """Verify an ID-JAG labs itself signed, and resolve its subject to an active user."""
    from connect_labs.labs import canopy
    from connect_labs.users.models import User

    try:
        header = dpop.unverified_header(assertion)
        if header.get("typ") != ID_JAG_TYP:
            raise JoseError("bad_typ", "the assertion is not an ID-JAG")
        if header["alg"] != canopy.SIGNING_ALG:
            raise JoseError("bad_alg", "the assertion is not signed with labs' algorithm")
        if not dpop.constant_time_equal(header.get("kid"), canopy.public_jwk()["kid"]):
            raise JoseError("unknown_key", "the assertion names a key labs does not hold")
        grant = dpop.decode(
            assertion,
            canopy.host_verification_key(),
            alg=header["alg"],
            audience=issuer(),
            required=("iss", "sub", "aud", "exp", "iat", "jti", "client_id", "resource", "scope"),
            leeway=LEEWAY_SECONDS,
        )
        dpop.check_jti(grant)
    except canopy.CanopyNotConfigured as exc:
        raise _Refused("invalid_grant", "This server issues no grants.", reason="no_host_key") from exc
    except JoseError as exc:
        raise _Refused("invalid_grant", f"The grant was refused: {exc.message}.", reason=f"idjag_{exc.code}") from exc

    if not dpop.constant_time_equal(grant["iss"], issuer()):
        raise _Refused("invalid_grant", "The grant was issued by someone else.", reason="idjag_iss")
    if not dpop.constant_time_equal(grant["client_id"], client_id):
        raise _Refused("invalid_grant", "The grant was issued to a different client.", reason="idjag_client_id")
    if not dpop.constant_time_equal(grant["resource"], oauth.resource_url()):
        raise _Refused("invalid_grant", "The grant is for a different resource.", reason="idjag_resource")
    if grant["exp"] - grant["iat"] > MAX_ID_JAG_LIFETIME_SECONDS:
        raise _Refused("invalid_grant", "The grant may live at most 300 seconds.", reason="idjag_lifetime")

    try:
        user = User.objects.get(pk=int(grant["sub"]), is_active=True)
    except (TypeError, ValueError, User.DoesNotExist) as exc:
        raise _Refused("invalid_grant", "The grant's subject is not an active user.", reason="idjag_sub") from exc
    return grant, user


def _issue(user, client_id: str, scopes: list[str], jkt: str, grant: dict) -> dict:
    from .models import DelegatedAccessToken

    raw = secrets.token_urlsafe(32)
    DelegatedAccessToken.objects.create(
        token_checksum=_checksum(raw),
        user=user,
        client_id=client_id,
        actor=client_id,
        scope=" ".join(scopes),
        cnf_jkt=jkt,
        grant_jti=str(grant["jti"])[:64],
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
    )
    _prune()
    logger.info("delegated token issued: user=%s client=%s scope=%s", user.pk, client_id, " ".join(scopes))
    return {
        "access_token": raw,
        "token_type": "DPoP",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "scope": " ".join(scopes),
    }


def _prune() -> None:
    """Drop expired tokens and used jtis. Cheap (indexed), and best-effort."""
    from .models import DelegatedAccessToken

    try:
        DelegatedAccessToken.objects.filter(expires_at__lt=datetime.now(timezone.utc)).delete()
        dpop.prune_seen_jtis()
    except Exception:  # noqa: BLE001 -- housekeeping must never fail a grant
        logger.warning("pruning delegated tokens failed", exc_info=True)


def _checksum(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The MCP side: resolving a delegated token, and the DPoP gate in front of it
# ---------------------------------------------------------------------------


def resolve_delegated_token(raw: str, presented_jkt: str | None):
    """``(user, client_id, scopes, actor, jkt, expires_at)`` for a live delegated token, else None.

    Live means: known, unexpired, its user active, and — always — presented
    with a DPoP proof signed by the key it is bound to. A bound token with no
    proof, or a proof by a different key, is refused exactly like an unknown one.
    """
    from .models import DelegatedAccessToken

    if not raw:
        return None
    try:
        token = DelegatedAccessToken.objects.select_related("user").get(token_checksum=_checksum(raw))
    except DelegatedAccessToken.DoesNotExist:
        return None
    if token.expires_at <= datetime.now(timezone.utc):
        return None
    if token.user is None or not token.user.is_active:
        return None
    if presented_jkt is None:
        logger.warning("delegated token presented without a DPoP proof (user=%s)", token.user_id)
        return None
    if not dpop.constant_time_equal(presented_jkt, token.cnf_jkt):
        logger.warning("delegated token presented with a proof by the wrong key (user=%s)", token.user_id)
        return None
    return token.user, token.client_id, token.scopes, token.actor, token.cnf_jkt, token.expires_at


def _check_mcp_proof(proof: str, method: str, access_token: str) -> str:
    jkt, jti, iat = dpop.verify_dpop_proof(proof, htm=method, htu=oauth.resource_url(), access_token=access_token)
    dpop.consume_jtis([(f"dpop:{jkt}", jti, iat + dpop.DPOP_IAT_WINDOW_SECONDS)])
    if random.random() < 0.01:  # noqa: S311 -- scheduling housekeeping, not security
        try:
            dpop.prune_seen_jtis()
        except Exception:  # noqa: BLE001
            logger.warning("pruning used DPoP jtis failed", exc_info=True)
    return jkt


class DPoPGate:
    """ASGI wrapper for ``/mcp``: verify a DPoP proof, then hand FastMCP a plain bearer.

    FastMCP (and the MCP SDK under it) only understands ``Authorization: Bearer``.
    So for ``Authorization: DPoP <token>`` this checks the proof against the
    request (method, the public MCP URL, the token's hash, freshness, single-use
    jti), records the proving key's thumbprint in ``presented_dpop_jkt`` for the
    verifier, and rewrites the header to ``Bearer``. A bad proof is refused here,
    with RFC 9449's ``invalid_dpop_proof``.

    A request with an ordinary ``Bearer`` header passes through untouched, with
    no key presented — which is what makes a DPoP-bound token useless as a plain
    bearer: the verifier refuses a bound token when no key was proved.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        authorization = [value for key, value in headers if key.lower() == b"authorization"]
        if len(authorization) != 1 or authorization[0][:5].lower() != b"dpop ":
            marker = presented_dpop_jkt.set(None)
            try:
                await self.app(scope, receive, send)
            finally:
                presented_dpop_jkt.reset(marker)
            return

        proofs = [value for key, value in headers if key.lower() == b"dpop"]
        try:
            token = authorization[0][5:].strip().decode("ascii")
            if not token or len(proofs) != 1:
                raise JoseError("no_proof", "send exactly one DPoP proof with a DPoP-bound token")
            proof = proofs[0].decode("ascii")
            jkt = await sync_to_async(_check_mcp_proof, thread_sensitive=True)(proof, scope.get("method", ""), token)
        except UnicodeDecodeError:
            await _refuse(send, "the credentials are not ASCII")
            return
        except JoseError as exc:
            logger.warning("MCP DPoP proof refused: %s", exc.code)
            await _refuse(send, f"the DPoP proof was refused: {exc.message}")
            return

        rewritten = [(key, value) for key, value in headers if key.lower() not in (b"authorization", b"dpop")]
        rewritten.append((b"authorization", b"Bearer " + token.encode("ascii")))
        marker = presented_dpop_jkt.set(jkt)
        try:
            await self.app({**scope, "headers": rewritten}, receive, send)
        finally:
            presented_dpop_jkt.reset(marker)


async def _refuse(send, description: str) -> None:
    body = json.dumps({"error": "invalid_dpop_proof", "error_description": description}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (
                    b"www-authenticate",
                    f'DPoP error="invalid_dpop_proof", algs="{" ".join(dpop.ALLOWED_ALGS)}"'.encode(),
                ),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
