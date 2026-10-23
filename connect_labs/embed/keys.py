"""A client's public keys, and verifying the assertion it signs with them.

The JOSE checks are the canopy SDK's (``canopy_sdk.jose``, ``canopy_sdk.keys``), so labs
verifies a JWT one way everywhere: the algorithm is chosen by labs, never read from the
token, and every failure is a refusal. RS256 is accepted here (2048-bit and up) because
many identity systems an embedder already runs sign nothing else; the SDK keeps it out
of the canopy grant, which is labs' own protocol.

A ``jwks_uri`` is fetched through ``canopy_sdk.fetch`` (https only, public addresses
only, no redirects, bounded), cached for an hour, and refetched at most once per five
minutes when an assertion names a ``kid`` the cache does not hold (a rotated key).
"""

from __future__ import annotations

import logging

from canopy_sdk import fetch
from canopy_sdk.contract import PRIVATE_JWK_MEMBERS, ContractError, constant_time_equal
from canopy_sdk.jose import decode, unverified_claims, unverified_header
from canopy_sdk.keys import public_key_for, select_key
from django.core.cache import cache

logger = logging.getLogger(__name__)

ALGORITHMS = ("EdDSA", "ES256", "RS256")
#: The assertion is used once, server to server, the moment it is made.
ASSERTION_MAX_LIFETIME = 300
LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ("iss", "sub", "aud", "exp", "iat", "jti")
MAX_JTI_LENGTH = 256
MIN_RSA_BITS = 2048

JWKS_CACHE_SECONDS = 3600
REFETCH_FLOOR_SECONDS = 300


def _jwks_from_uri(uri: str, *, fresh: bool) -> list:
    key = f"embed-jwks:{uri}"
    if not fresh:
        cached = cache.get(key)
        if isinstance(cached, list):
            return cached
    try:
        document = fetch.get_json(uri)
    except fetch.FetchError as exc:
        raise ContractError("unknown_key", f"the client's JWKS could not be read: {exc}") from exc
    keys = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(keys, list):
        raise ContractError("unknown_key", "the client's JWKS has no keys")
    cache.set(key, keys, JWKS_CACHE_SECONDS)
    return keys


def client_jwk(client, kid) -> dict:
    """The one signing JWK of ``client`` named by ``kid``. Raises ``ContractError``."""
    if client.jwks:
        found = select_key((client.jwks or {}).get("keys"), kid)
    else:
        found = select_key(_jwks_from_uri(client.jwks_uri, fresh=False), kid)
        if found is None and cache.add(f"embed-jwks-refetch:{client.pk}", 1, REFETCH_FLOOR_SECONDS):
            found = select_key(_jwks_from_uri(client.jwks_uri, fresh=True), kid)
    if found is None:
        raise ContractError("unknown_key", "no published key of this client matches the assertion's kid")
    return found


def verification_key(jwk: dict, alg: str):
    """The public key ``jwk`` names, if it is exactly the shape ``alg`` needs."""
    if alg != "RS256":
        return public_key_for(jwk, alg, algorithms=("EdDSA", "ES256"))
    from jwt import PyJWK

    if PRIVATE_JWK_MEMBERS & set(jwk):
        raise ContractError("bad_key", "a public key must not carry private members")
    if jwk.get("kty") != "RSA" or jwk.get("alg") not in (None, "RS256"):
        raise ContractError("bad_key", "RS256 needs an RSA key")
    try:
        key = PyJWK.from_dict(dict(jwk), algorithm="RS256").key
    except Exception as exc:  # noqa: BLE001 -- any parse failure is a refusal
        raise ContractError("bad_key", "the key could not be read") from exc
    if key.key_size < MIN_RSA_BITS:
        raise ContractError("bad_key", f"an RSA key must be at least {MIN_RSA_BITS} bits")
    return key


def assertion_issuer(assertion: str) -> str:
    """The ``iss`` the assertion claims -- read unverified, only to find whose keys check it."""
    unverified_header(assertion, algorithms=ALGORITHMS)
    iss = unverified_claims(assertion).get("iss")
    if not isinstance(iss, str) or not iss:
        raise ContractError("missing_claim", "the assertion names no iss")
    return iss


def verify_assertion(assertion: str, client, audiences: list[str]) -> dict:
    """The assertion's claims, once its signature, audience, lifetime and issuer check out.

    Raises ``ContractError``. The ``jti`` is NOT consumed here: the caller does that only
    after every other check passed, so a refused request does not burn its assertion.
    """
    header = unverified_header(assertion, algorithms=ALGORITHMS)
    key = verification_key(client_jwk(client, header.get("kid")), header["alg"])
    claims = decode(
        assertion,
        key,
        algorithms=[header["alg"]],
        audience=audiences,
        required=REQUIRED_CLAIMS,
        leeway=LEEWAY_SECONDS,
    )
    if not constant_time_equal(claims.get("iss"), client.client_id):
        raise ContractError("bad_issuer", "the assertion was not issued by this client")
    try:
        lifetime = int(claims["exp"]) - int(claims["iat"])
    except (TypeError, ValueError):
        raise ContractError("invalid", "exp and iat must be numbers") from None
    if lifetime <= 0 or lifetime > ASSERTION_MAX_LIFETIME:
        raise ContractError("too_long", f"an assertion lives at most {ASSERTION_MAX_LIFETIME} seconds")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti or len(jti) > MAX_JTI_LENGTH:
        raise ContractError("bad_jti", "the assertion's jti is missing or too long")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise ContractError("missing_claim", "the assertion names no sub")
    return claims
