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
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from django.conf import settings

#: Canopy caps a declared lifetime at 120s (+30s leeway). Sixty is comfortably
#: inside that and leaves room for a slow hop; there is nothing to gain from
#: asking for longer, since the token canopy returns has its own life.
ASSERTION_TTL_SECONDS = 60

#: A mint is one hop to canopy on the same host. If it has not answered by now it
#: is not going to, and the widget should show a failure rather than hold the
#: request open.
MINT_TIMEOUT_SECONDS = 10


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


def panel_context(
    *,
    resource: str = "",
    backing_tool: str = "",
    visible_ids=(),
    filters=None,
    path: str = "",
) -> dict:
    """What ``labs/includes/canopy_panel.html`` needs, or a dict that renders nothing.

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
    return {
        "ready": ready,
        "base_url": _audience() if ready else "",
        "app_name": getattr(settings, "CANOPY_APP_NAME", "") if ready else "",
        "page_state": state,
    }


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
            "sub": str(user.pk),
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
        algorithm="EdDSA",
        # So canopy can pick this key out of the JWKS we publish. Without it a
        # rotation has nothing to select on: canopy would try every published key
        # and succeed only by luck of ordering.
        headers={"kid": public_jwk()["kid"]},
    )


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


def vouch_for(user) -> dict:
    """Exchange a signed statement for a short-lived token for ``user``.

    Returns only the two fields the widget needs. Canopy's response is not passed
    through: a new field on its side should not silently become part of labs'
    contract.
    """
    request = urllib.request.Request(
        f"{_audience()}/api/auth/contact-token",
        data=json.dumps({"assertion": assertion_for(user)}).encode(),
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
