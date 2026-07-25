"""Org-scoped API tokens for the ingestion endpoints.

Tokens are shown once at mint time and stored only as a SHA-256 hash. A short
prefix is kept in the clear so a supplier can recognise which token is which.
"""
import hashlib
import secrets

from django.utils import timezone

from ..models import ApiToken

TOKEN_PREFIX = "oes_"


def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint_token(org, label):
    raw = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    token = ApiToken.objects.create(org=org, label=label or "API token", token_hash=_hash(raw), prefix=raw[:12])
    return token, raw


def resolve_token(raw):
    """Return the owning org for a bearer token, or None."""
    if not raw:
        return None
    token = ApiToken.objects.filter(token_hash=_hash(raw.strip()), revoked=False).select_related("org").first()
    if token is None:
        return None
    token.last_used_at = timezone.now()
    token.save(update_fields=["last_used_at"])
    return token.org


def revoke_token(org, token_id):
    return ApiToken.objects.filter(org=org, id=token_id).update(revoked=True) > 0
