"""Vouching for a labs visitor to canopy, so the agent panel can open.

Labs does not hand canopy a shared secret. It **signs a statement** about the
person whose session is making the request, and canopy — holding only the public
half of the key — returns a short-lived token for that one visitor. The
difference matters in one direction: a secret canopy stores can be stolen from
canopy, and a single static string can name anybody, where a signature proves
this claim, about this visitor, at this moment.

Two things here are load-bearing and easy to get wrong:

* ``sub`` is **our own** id for the signed-in user and nothing else. We are
  asserting "this is a real person on labs", and canopy believes it because it
  verified our signature. Taking the subject from anything the caller supplies
  would let any caller be anybody.
* ``email_verified: true`` on the signed-in user's address is what lets canopy
  recognise them. Canopy lets a visitor in as their own canopy account only if
  exactly one canopy user holds that address verified AND is a member of the
  workspace this site is registered in; anyone else arrives as a **contact**,
  who reaches only the agents this site offers and their own conversations with
  it. Canopy never creates an account from this. See ``assertion_for``.

Canopy's own requirements, worth knowing before debugging a 401: EdDSA/ES256/
RS256 only (never HMAC — the key is public, so a symmetric algorithm would let
anyone sign), ``aud`` must match, ``exp`` at most 120 seconds out, and each
``jti`` works exactly once.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core import signing

log = logging.getLogger(__name__)

#: Canopy caps a declared lifetime at 120s (+30s leeway). Sixty is comfortably
#: inside that and leaves room for a slow hop; there is nothing to gain from
#: asking for longer, since the token canopy returns has its own life.
ASSERTION_TTL_SECONDS = 60

#: A mint is one hop to canopy on the same host. If it has not answered by now it
#: is not going to, and the widget should show a failure rather than hold the
#: request open.
MINT_TIMEOUT_SECONDS = 10


#: The one algorithm labs signs with, for the visitor assertion and the ID-JAG
#: alike. Asymmetric, because canopy verifies with a key anyone may read.
SIGNING_ALG = "EdDSA"

#: How long a rendered page's identity stays good for (``page_token``). It only
#: says which page the panel was opened on, and it is re-checked against the
#: session's user on every mint; past this, the panel still works, the agent just
#: cannot act as the visitor until the page is reloaded.
PAGE_TOKEN_MAX_AGE_SECONDS = 2 * 60 * 60
_PAGE_TOKEN_SALT = "connect_labs.labs.canopy.page"

#: ID-JAGs are good for at most 300 seconds under the contract; canopy redeems
#: one immediately, so this only has to cover the hop.
ID_JAG_TTL_SECONDS = 120

#: Which pages may let canopy act as the visitor, and with which scopes, keyed
#: by URL name. THE route registry: a page's scopes are decided here, server-
#: side, and never from anything the browser sends. A page not listed gets no
#: grant at all — the panel still opens, and the agent works as it did before.
#: Every scope here must exist in ``connect_labs.mcp.delegation.SCOPE_TOOLS``.
PAGE_SCOPES: dict[str, tuple[str, ...]] = {
    "marketplace:network": ("marketplace:read",),
    "marketplace:round": ("marketplace:read",),
}


class CanopyNotConfigured(RuntimeError):
    """No panel here — base URL or signing key missing."""


class CanopyMintFailed(RuntimeError):
    """Canopy refused or could not be reached. Carries its own words where it
    gave any, because "token endpoint returned 502" on the page is not
    something anyone can act on."""


def is_configured() -> bool:
    """Whether a panel can work at all.

    All three are required and the panel is hidden without them. A partly
    configured panel renders a launcher that opens onto an error the page cannot
    explain, which is worse than no launcher.
    """
    return bool(
        getattr(settings, "CANOPY_BASE_URL", "")
        and getattr(settings, "CANOPY_APP_NAME", "")
        and getattr(settings, "CANOPY_SIGNING_KEY", "")
    )


#: Canopy's own cap. Checked here so an oversized selection is caught where the
#: page is built — with a log line naming the page — rather than as a 422 inside
#: a widget the visitor cannot see the console of.
MAX_VISIBLE_IDS = 400
#: What canopy actually caps is BYTES (8 KiB, canopy-web
#: `apps/canopy_sessions/page_state.py::MAX_STATE_BYTES`), and a count cap does
#: not keep under it: 400 org slugs serialise to ~11 KiB, so the network page's
#: state was refused whole (422) and the agent saw nothing of the screen
#: (2026-09-25). Trimmed by serialised size, with headroom for what canopy adds.
STATE_BYTE_BUDGET = 7 * 1024


def panel_context(
    *,
    resource: str = "",
    backing_tool: str = "",
    visible_ids=(),
    filters=None,
    path: str = "",
    request=None,
) -> dict:
    """What ``labs/includes/canopy_panel.html`` needs, or a dict that renders nothing.

    Pass ``request`` and the panel carries a signed ``page_token`` naming this
    page's route, which the widget hands back when it mints. That is how labs
    knows, at mint time and from its own signature, which page's scopes to put
    in the ID-JAG (``PAGE_SCOPES``). An unregistered route gets no token.

    ``visible_ids`` is the selection the visitor can see — slugs or ids, never
    rows. It is truncated rather than allowed to breach canopy's 8 KiB cap,
    because a refused page state means the agent is blind to the whole screen,
    where a truncated one still describes most of it. Pass only what THIS
    visitor may see: "the agent sees exactly what the user sees" is the access
    story, and keeping it true belongs to the page.
    """
    ready = is_configured()
    ids = [str(i) for i in visible_ids][:MAX_VISIBLE_IDS]
    state = None
    if resource:
        state = {"resource": resource, "visible_ids": ids}
        if backing_tool:
            state["backing_tool"] = backing_tool
        if filters:
            state["filters"] = filters
        if path:
            state["path"] = path
        state["visible_ids"] = _fit_ids(state)
    return {
        "ready": ready,
        "base_url": _audience() if ready else "",
        "app_name": getattr(settings, "CANOPY_APP_NAME", "") if ready else "",
        "agent": getattr(settings, "CANOPY_AGENT_SLUG", "") if ready else "",
        "page_state": state,
        "page_token": page_token(request) if ready and request is not None else "",
    }


def page_token(request) -> str:
    """A server-signed statement of which registered page this is, for this user.

    Empty for a page not in ``PAGE_SCOPES``. Signed with Django's signer (labs'
    own secret) because it never leaves labs: the browser carries it from the
    rendered page back to ``canopy_views.token``, and only labs reads it.
    """
    match = getattr(request, "resolver_match", None)
    view_name = getattr(match, "view_name", "") or ""
    user = getattr(request, "user", None)
    if view_name not in PAGE_SCOPES or user is None or not user.is_authenticated:
        return ""
    return signing.dumps({"page": view_name, "user": user.pk}, salt=_PAGE_TOKEN_SALT, compress=False)


def scopes_for_page_token(raw, user) -> tuple[str, ...]:
    """The scopes the page named by ``raw`` grants ``user``, or () — never an error.

    Anything wrong (absent, forged, expired, minted for another user, a page no
    longer registered) means no grant, and the panel carries on without one.
    The scopes are read from the registry NOW, not from the token, so removing a
    page from ``PAGE_SCOPES`` takes effect on the next mint.
    """
    if not raw or not isinstance(raw, str) or len(raw) > 1024 or user is None or not user.is_authenticated:
        return ()
    try:
        claims = signing.loads(raw, salt=_PAGE_TOKEN_SALT, max_age=PAGE_TOKEN_MAX_AGE_SECONDS)
    except signing.BadSignature:  # includes SignatureExpired
        return ()
    if not isinstance(claims, dict) or claims.get("user") != user.pk:
        return ()
    return PAGE_SCOPES.get(claims.get("page"), ())


def _fit_ids(state: dict) -> list[str]:
    """The longest prefix of ``visible_ids`` that keeps ``state`` inside the budget.

    A prefix, not a sample: the page lists what is on top first, and "the first
    N of what I see" is the honest description of a truncated screen.
    """
    ids = list(state.get("visible_ids") or [])
    fixed = len(json.dumps({**state, "visible_ids": []}).encode())
    size, kept = fixed, []
    for i in ids:
        # `"<id>"` plus its comma separator (`, ` from json.dumps' default).
        size += len(json.dumps(i).encode()) + 2
        if size > STATE_BYTE_BUDGET:
            break
        kept.append(i)
    return kept


def assertion_for(user) -> str:
    """A signed statement that ``user`` is a real person on labs, right now."""
    if not is_configured():
        raise CanopyNotConfigured("CANOPY_BASE_URL / CANOPY_SIGNING_KEY are not set")

    import jwt  # local: pyjwt is only needed on this path

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": settings.CANOPY_APP_NAME,
            # OUR id for them. Opaque to canopy, and namespaced by the app, so it
            # cannot collide with a canopy user or another site's people.
            "sub": _subject(user),
            "aud": _audience(),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ASSERTION_TTL_SECONDS)).timestamp()),
            "jti": str(uuid.uuid4()),
            # `name` is descriptive: it is what a person sees in the agent's own
            # listing of who it spoke to.
            #
            # `get_display_name`, not `get_full_name`: labs' User replaces
            # first_name/last_name with a single `name` ("First and last name do
            # not cover name patterns around the globe"), so the inherited
            # accessor reads two fields that are None here.
            "name": user.get_display_name(),
            "email": user.email or "",
            # The address came from Connect's OAuth identity, which is how this
            # person signed in to labs — so vouching for it is vouching for our
            # own signed-in user, which is what this signature is for. Canopy
            # uses it to let a member of the site's workspace arrive as their
            # own canopy account (their chats, their access) instead of as an
            # anonymous contact; for anyone else it changes nothing. Never
            # claimed for an empty address.
            "email_verified": bool(user.email),
        },
        settings.CANOPY_SIGNING_KEY,
        algorithm=SIGNING_ALG,
        # So canopy can pick this key out of the JWKS we publish. Without it a
        # rotation has nothing to select on: canopy would try every published key
        # and succeed only by luck of ordering.
        headers={"kid": public_jwk()["kid"]},
    )


def id_jag_for(user, scopes) -> str:
    """An ID-JAG letting canopy act as ``user`` at labs' MCP, within ``scopes``.

    draft-ietf-oauth-identity-assertion-authz-grant, as pinned by canopy-web's
    host-grant contract. Labs is both the issuer and the audience — it grants
    for its own authorization server — and canopy can only REDEEM it, at labs'
    token endpoint, authenticated as itself and holding a DPoP key
    (``connect_labs.mcp.delegation``). Signed with the same key as the visitor
    assertion, so canopy needs no second key to trust.

    ``sub`` is the same value the visitor assertion carries: the two statements
    are about one person.
    """
    from connect_labs.mcp import delegation, oauth

    if not is_configured():
        raise CanopyNotConfigured("CANOPY_BASE_URL / CANOPY_SIGNING_KEY are not set")
    if not delegation.grant_enabled():
        raise CanopyNotConfigured("CANOPY_CLIENT_ID / LABS_PUBLIC_URL are not set")
    scopes = [s for s in scopes if s in delegation.SCOPE_TOOLS]
    if not scopes:
        raise ValueError("an ID-JAG needs at least one scope this server offers")

    import jwt

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": oauth.public_base_url(),
            "aud": oauth.public_base_url(),
            "sub": _subject(user),
            "client_id": delegation.canopy_client_id(),
            "resource": oauth.resource_url(),
            "scope": " ".join(scopes),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ID_JAG_TTL_SECONDS)).timestamp()),
            "jti": str(uuid.uuid4()),
        },
        settings.CANOPY_SIGNING_KEY,
        algorithm=SIGNING_ALG,
        headers={"kid": public_jwk()["kid"], "typ": "oauth-id-jag+jwt"},
    )


def _subject(user) -> str:
    """OUR id for a user — the ``sub`` of both the assertion and the ID-JAG."""
    return str(user.pk)


def host_verification_key():
    """The public half of labs' signing key, for checking an ID-JAG labs issued."""
    if not getattr(settings, "CANOPY_SIGNING_KEY", ""):
        raise CanopyNotConfigured("CANOPY_SIGNING_KEY is not set")
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(settings.CANOPY_SIGNING_KEY.encode(), password=None).public_key()


def _thumbprint(jwk: dict) -> str:
    """RFC 7638 thumbprint — the key's ``kid``, derived FROM the key.

    Canopy selects a verification key by the ``kid`` on the assertion, and
    computes this same value from what we publish. A fixed string would give two
    different keys the same name, leaving a rotation nothing to select on; a
    thumbprint changes when the key does and needs no configuration.
    """
    import base64
    import hashlib

    # Only the members RFC 7638 defines for this key type, lexicographic, no
    # whitespace. The canonical form is the whole point.
    canonical = json.dumps(
        {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"]},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(hashlib.sha256(canonical).digest()).decode().rstrip("=")


def public_jwk() -> dict:
    """The public half of our signing key, as a JWK.

    **Derived from the private key, never configured separately.** Two settings
    that have to agree are two settings that can disagree — and the failure mode
    is assertions that verify against nothing, at a moment far from the edit.
    """
    from cryptography.hazmat.primitives import serialization
    from jwt.algorithms import OKPAlgorithm

    private = serialization.load_pem_private_key(settings.CANOPY_SIGNING_KEY.encode(), password=None)
    jwk = OKPAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"use": "sig", "alg": "EdDSA"})
    jwk["kid"] = _thumbprint(jwk)
    return jwk


def _audience() -> str:
    """The one canopy this assertion is for.

    Trailing slash trimmed because canopy compares against its own base URL
    without one, and an address bar supplies it.
    """
    return settings.CANOPY_BASE_URL.rstrip("/")


def vouch_for(user, scopes=()) -> dict:
    """Exchange a signed statement for a short-lived token for ``user``.

    With ``scopes`` (the page's, from ``scopes_for_page_token``) and the grant
    enabled, the same request also carries an ``id_jag`` so canopy can act as
    this visitor at labs' MCP, within those scopes. Without either, the request
    is exactly what it was before the grant existed.

    Returns only the two fields the widget needs. Canopy's response is not passed
    through: a new field on its side should not silently become part of labs'
    contract.
    """
    from connect_labs.mcp import delegation

    payload = {
        "assertion": assertion_for(user),
        # `agent_slug` says which canopy tenant this token is for — see
        # CANOPY_AGENT_SLUG. Without it canopy resolves the tenant from the
        # site name alone, which stops working once two workspaces share it.
        "agent_slug": getattr(settings, "CANOPY_AGENT_SLUG", ""),
    }
    if scopes and delegation.grant_enabled():
        try:
            payload["id_jag"] = id_jag_for(user, scopes)
        except (CanopyNotConfigured, ValueError):
            # No grant is a working panel (the agent acts as itself, as it
            # always has); a failed mint is not. Say so, and carry on without.
            log.warning("could not issue an ID-JAG for user %s", user.pk, exc_info=True)
    request = urllib.request.Request(
        f"{_audience()}/api/auth/contact-token",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=MINT_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Canopy names the reason in the body (`replayed`, `bad_signature`,
        # `unknown_issuer`, …) and that is the only useful thing in the failure.
        detail = ""
        try:
            detail = exc.read().decode()[:500]
        except Exception:  # pragma: no cover - a body that cannot be read
            pass
        raise CanopyMintFailed(f"canopy returned {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CanopyMintFailed(f"canopy could not be reached: {exc}") from exc

    token = body.get("token")
    if not token:
        raise CanopyMintFailed("canopy returned no token")
    return {"token": token, "expires_at": body.get("expires_at", "")}
