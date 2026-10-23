"""The protocol, server side: everything an embedding system's backend does.

1. Hold a private key; publish the public half as a JWKS (RFC 7517) so labs can check
   signatures. Labs fetches it from ``jwks()``'s URL, so rotating a key is publishing a
   new one.
2. For each page load, sign a short statement (a JWT) naming the person and the labs
   resource to show (RFC 7523 §2.1).
3. POST it to labs' token endpoint, server to server, and get back a one-time token and
   the frame address.

Only PyJWT, cryptography and an HTTP client. Nothing here is specific to Django, and
nothing imports from labs' own embed code: a system in any language does the same.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass

import httpx
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from jwt.algorithms import OKPAlgorithm

JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
#: Labs accepts up to five minutes; a statement is used the moment it is made.
STATEMENT_SECONDS = 60


class EmbedError(Exception):
    """Labs refused. ``error`` is the OAuth error code; ``description`` is safe to show."""

    def __init__(self, error: str, description: str, status: int):
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description
        self.status = status


@dataclass(frozen=True)
class Embed:
    token: str
    embed_url: str
    expires_in: int


class Embedder:
    """One embedding system: its client id, its private key, and the labs it embeds."""

    def __init__(self, *, client_id: str, private_key_pem: str, labs_url: str):
        self.client_id = client_id
        self.labs_url = labs_url.rstrip("/")
        # Ed25519: small, fast, and labs' recommended algorithm (EdDSA).
        self.key = load_pem_private_key(private_key_pem.encode(), password=None)
        self.public_jwk = OKPAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        # The key id is the key's RFC 7638 thumbprint: stable, and changes with the key.
        self.kid = thumbprint(self.public_jwk)

    def jwks(self) -> dict:
        """What to publish at the JWKS URL registered with labs. Public members only."""
        return {"keys": [{**self.public_jwk, "kid": self.kid, "alg": "EdDSA", "use": "sig"}]}

    def frame_address(self, kind: str, resource_id) -> str:
        return f"{self.labs_url}/embed/c/{self.client_id}/{kind}/{resource_id}/"

    def statement(self, username: str, kind: str, resource_id) -> str:
        """The signed statement: this person, this resource, now, once."""
        now = int(time.time())
        claims = {
            "iss": self.client_id,  # who is asking
            "sub": username,  # whom the frame is for
            "aud": f"{self.labs_url}/embed/token/",  # only labs' token endpoint may accept it
            "iat": now,
            "exp": now + STATEMENT_SECONDS,
            "jti": str(uuid.uuid4()),  # labs accepts each statement once
            "resource": self.frame_address(kind, resource_id),  # what to show
        }
        return jwt.encode(claims, self.key, algorithm="EdDSA", headers={"kid": self.kid})

    def embed(self, username: str, kind: str, resource_id, *, client: httpx.Client | None = None) -> Embed:
        """Exchange a fresh statement for a one-time token. Call per page load; never cache."""
        http = client or httpx.Client(timeout=10)
        try:
            response = http.post(
                f"{self.labs_url}/embed/token/",
                data={"grant_type": JWT_BEARER, "assertion": self.statement(username, kind, resource_id)},
            )
        finally:
            if client is None:
                http.close()
        body = _json(response)
        if response.status_code != 200:
            raise EmbedError(
                body.get("error", "error"), body.get("error_description", "Labs refused."), response.status_code
            )
        return Embed(token=body["access_token"], embed_url=body["embed_url"], expires_in=int(body["expires_in"]))


def thumbprint(jwk: dict) -> str:
    """RFC 7638: SHA-256 over the key's required members, in order, base64url."""
    required = {k: jwk[k] for k in ("crv", "kty", "x")}
    digest = hashlib.sha256(json.dumps(required, separators=(",", ":"), sort_keys=True).encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _json(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
