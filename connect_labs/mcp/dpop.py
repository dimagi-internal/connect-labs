"""JOSE building blocks for the delegated (canopy) grant: keys, single use, DPoP.

Every signature check here is PyJWT's (with ``cryptography`` underneath); this
module only decides WHICH key and WHICH algorithms a check may use, and what
the claims must say. Nothing here verifies a signature by hand.

Two rules run through all of it:

* **Asymmetric algorithms only** — EdDSA (Ed25519) and ES256. The verification
  keys here are public (published in a JWKS, or carried in the DPoP header
  itself), and with a symmetric algorithm the verification key IS a signing
  key: anyone holding it could forge.
* **Fail closed.** Every function returns a verdict or raises ``JoseError``;
  there is no path that answers "probably fine".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import jwt
from django.db import IntegrityError, transaction
from jwt import PyJWK

#: The only algorithms any JWT crossing between canopy and labs may use.
ALLOWED_ALGS = ("EdDSA", "ES256")

#: DPoP proofs are fresh by construction (a new one per request), so the window
#: only has to absorb clock skew between canopy and labs.
DPOP_IAT_WINDOW_SECONDS = 60

#: A proof is a few hundred bytes. Anything near this is not a proof.
MAX_JWT_BYTES = 8 * 1024

#: A jti is an opaque unique string, usually a UUID. Bounded so a hostile one
#: cannot be arbitrarily large, and hashed before storage so its length never
#: reaches a column.
MAX_JTI_LENGTH = 256

# Key types and curves allowed for each algorithm, so a proof cannot pair
# ``alg: ES256`` with an Ed25519 key (or the reverse) and slip through a
# library's key coercion.
_KEY_SHAPES = {
    "EdDSA": ("OKP", "Ed25519"),
    "ES256": ("EC", "P-256"),
}
# The private members of a JWK. A "public" JWK that carries one is a key leak
# at best and a confused sender at worst; refused either way.
_PRIVATE_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "k"})


class JoseError(Exception):
    """A refused token or proof. ``code`` is safe to log; the message names the check."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def jwk_thumbprint(jwk: dict) -> str:
    """RFC 7638 thumbprint of a public JWK (OKP or EC)."""
    kty = jwk.get("kty")
    if kty == "OKP":
        members = ("crv", "kty", "x")
    elif kty == "EC":
        members = ("crv", "kty", "x", "y")
    else:
        raise JoseError("bad_key", f"unsupported key type {kty!r}")
    if not all(isinstance(jwk.get(m), str) for m in members):
        raise JoseError("bad_key", "the key is missing a required member")
    canonical = json.dumps({m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True)
    return b64url(hashlib.sha256(canonical.encode()).digest())


def public_key_for(jwk: dict, alg: str):
    """The verification key a JWK names, if it is a PUBLIC key of the shape ``alg`` needs."""
    if alg not in ALLOWED_ALGS:
        raise JoseError("bad_alg", f"algorithm {alg!r} is not accepted")
    if not isinstance(jwk, dict):
        raise JoseError("bad_key", "the key is not a JWK object")
    if _PRIVATE_MEMBERS & set(jwk):
        raise JoseError("bad_key", "a public key must not carry private members")
    kty, crv = _KEY_SHAPES[alg]
    if jwk.get("kty") != kty or jwk.get("crv") != crv:
        raise JoseError("bad_key", f"{alg} needs a {kty}/{crv} key")
    if jwk.get("alg") not in (None, alg):
        raise JoseError("bad_key", "the key is declared for a different algorithm")
    try:
        return PyJWK.from_dict(jwk, algorithm=alg).key
    except Exception as exc:  # noqa: BLE001 -- any parse failure is a refusal
        raise JoseError("bad_key", "the key could not be read") from exc


def unverified_header(token: str) -> dict:
    if not isinstance(token, str) or not token or len(token) > MAX_JWT_BYTES:
        raise JoseError("malformed", "not a compact JWS of acceptable size")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise JoseError("malformed", "not a compact JWS") from exc
    if header.get("alg") not in ALLOWED_ALGS:
        raise JoseError("bad_alg", f"algorithm {header.get('alg')!r} is not accepted")
    return header


def decode(token: str, key, *, alg: str, audience=None, required=(), leeway: int = 0) -> dict:
    """Verify signature and standard claims. Raises ``JoseError`` on any failure."""
    options = {"require": list(required), "verify_aud": audience is not None}
    try:
        return jwt.decode(token, key, algorithms=[alg], audience=audience, options=options, leeway=leeway)
    except jwt.ExpiredSignatureError as exc:
        raise JoseError("expired", "the token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise JoseError("bad_audience", "the token is for a different audience") from exc
    except jwt.ImmatureSignatureError as exc:
        raise JoseError("not_yet_valid", "the token is not valid yet") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise JoseError("missing_claim", f"the token is missing {exc.claim!r}") from exc
    except jwt.InvalidSignatureError as exc:
        raise JoseError("bad_signature", "the signature does not verify") from exc
    except jwt.PyJWTError as exc:
        raise JoseError("invalid", "the token is not valid") from exc


def constant_time_equal(a, b) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def check_jti(claims: dict) -> str:
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti or len(jti) > MAX_JTI_LENGTH:
        raise JoseError("missing_claim", "the token needs a jti")
    return jti


# ---------------------------------------------------------------------------
# Single use
# ---------------------------------------------------------------------------


def jti_key(kind: str, jti: str) -> str:
    return f"{kind}:{hashlib.sha256(jti.encode()).hexdigest()}"


def consume_jtis(entries: list[tuple[str, str, int]]) -> None:
    """Record every ``(kind, jti, exp)`` as used, or none of them.

    Raises ``JoseError("replayed")`` when any one was already used. The rows are
    kept until the statement they belong to has expired — after that it would be
    refused on ``exp`` anyway — so the table stays bounded.
    """
    from .models import SeenJTI

    now = datetime.now(timezone.utc)
    rows = [
        SeenJTI(
            key=jti_key(kind, jti),
            # A floor, so a statement that claims to expire in the past is still
            # remembered long enough to cover the clock-skew leeway.
            expires_at=max(datetime.fromtimestamp(exp, timezone.utc), now) + timedelta(minutes=5),
        )
        for kind, jti, exp in entries
    ]
    try:
        with transaction.atomic():
            for row in rows:
                row.save(force_insert=True)
    except IntegrityError as exc:
        raise JoseError("replayed", "this token has already been used") from exc


def prune_seen_jtis() -> None:
    from .models import SeenJTI

    SeenJTI.objects.filter(expires_at__lt=datetime.now(timezone.utc)).delete()


# ---------------------------------------------------------------------------
# DPoP (RFC 9449)
# ---------------------------------------------------------------------------


def normalize_htu(url: str) -> str:
    """Scheme and host lower-cased, no query or fragment, no trailing slash (RFC 9449 section 4.3)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def verify_dpop_proof(
    proof: str,
    *,
    htm: str,
    htu: str,
    access_token: str | None = None,
    now: float | None = None,
) -> tuple[str, str, int]:
    """Verify a DPoP proof. Returns ``(jkt, jti, iat)``; the caller consumes the jti.

    Checks ``typ``, an allowed ``alg``, an embedded PUBLIC key of the matching
    shape, the signature against that key, ``htm``, ``htu``, ``iat`` within
    +/- 60 seconds, a ``jti``, and — when an access token is presented with it —
    ``ath``, the hash of that token, so a proof made for one token cannot be
    used with another.
    """
    header = unverified_header(proof)
    if header.get("typ") != "dpop+jwt":
        raise JoseError("bad_proof", "a DPoP proof must have typ dpop+jwt")
    alg = header["alg"]
    jwk = header.get("jwk")
    key = public_key_for(jwk, alg)
    # The leeway lets PyJWT's own "iat is not in the future" check absorb the
    # same skew the window below allows; the window is the real freshness rule.
    claims = decode(proof, key, alg=alg, required=("iat", "jti", "htm", "htu"), leeway=DPOP_IAT_WINDOW_SECONDS)

    if not constant_time_equal(claims.get("htm"), htm):
        raise JoseError("bad_proof", "the proof is for a different HTTP method")
    if not constant_time_equal(normalize_htu(claims.get("htu") or ""), normalize_htu(htu)):
        raise JoseError("bad_proof", "the proof is for a different URL")

    iat = claims.get("iat")
    current = time.time() if now is None else now
    if not isinstance(iat, int | float) or abs(current - iat) > DPOP_IAT_WINDOW_SECONDS:
        raise JoseError("bad_proof", "the proof is not fresh")

    if access_token is not None:
        expected_ath = b64url(hashlib.sha256(access_token.encode()).digest())
        if not constant_time_equal(claims.get("ath"), expected_ath):
            raise JoseError("bad_proof", "the proof is not bound to this access token")
    elif "ath" in claims:
        raise JoseError("bad_proof", "a proof at the token endpoint carries no ath")

    jti = check_jti(claims)
    return jwk_thumbprint(jwk), jti, int(iat)
