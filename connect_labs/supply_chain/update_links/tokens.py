"""Minting and recognising update-link tokens.

A token is 32 random bytes (`secrets.token_urlsafe`), which is the whole of its
strength: unguessable, and carrying nothing -- no link id, no organisation, no
expiry -- that a holder could read or alter. Expiry and revocation live on the
row, where revoking one link is one UPDATE and needs no key rotation.

Only `hash_token(raw)` is stored: an HMAC-SHA256 keyed with the deployment's
SECRET_KEY. A plain SHA-256 would already keep the raw token out of the
database; keying it means a copy of the table on its own cannot even be used to
check a guess. Lookup is by that digest, and the final comparison is
constant-time, so the response to a guess does not depend on how much of it was
right.
"""

import hashlib
import hmac
import secrets

from django.conf import settings

# Long enough for 256 bits of randomness in URL-safe base64, short enough that
# an absurd path segment is refused before it is hashed.
TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128
HINT_LENGTH = 6


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw: str) -> str:
    key = f"supply-update-link:{settings.SECRET_KEY}".encode()
    return hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()


def hint(raw: str) -> str:
    return raw[:HINT_LENGTH]


def find_link(raw):
    """The link this token belongs to, usable or not; None for a bad token."""
    from connect_labs.supply_chain.update_links.models import UpdateLink

    if not raw or not isinstance(raw, str) or len(raw) > MAX_TOKEN_LENGTH:
        return None
    digest = hash_token(raw)
    link = UpdateLink.objects.filter(token_hash=digest).select_related("org").first()
    if link is None or not hmac.compare_digest(link.token_hash, digest):
        return None
    return link


def find_usable_link(raw):
    """The link, only if it has not expired and has not been revoked.

    One answer -- None -- for a token that never existed, one that expired and
    one that was revoked. A caller that could tell those apart could learn
    which guesses had once been real.
    """
    link = find_link(raw)
    return link if link is not None and link.is_usable else None
