"""Models for the labs MCP server.

MCPAccessToken: Personal Access Tokens for Claude Code clients.
MCPAuditLog: Audit trail of every tool call (added in Task D1).
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def _hash_token(raw: str) -> str:
    """SHA-256 hash a raw token for storage.

    We don't need bcrypt here — PATs are already 32 bytes of entropy from
    secrets.token_urlsafe(32), so a single SHA-256 is sufficient (brute-force
    is infeasible). Hashing at all prevents DB-read-only attackers from
    grabbing working tokens.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MCPAccessToken(models.Model):
    """Personal Access Token for MCP clients.

    The raw token is only returned once, at creation. After that, only the
    SHA-256 hash is stored. Clients send the raw token as
    `Authorization: Bearer <raw_token>`.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_tokens",
    )
    name = models.CharField(
        max_length=100,
        help_text="User-provided label, e.g. 'claude-code-laptop'.",
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "mcp_access_token"
        indexes = [
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.user.username})"

    #: Hard ceiling on token lifetime. No mint path may create an immortal
    #: credential — with self-service PATs across many users, a leaked token
    #: must age out even if nobody notices the leak.
    MAX_TTL_DAYS = 365

    @classmethod
    def create_token(
        cls,
        user,
        name: str,
        ttl_days: int | None = 90,
    ) -> tuple["MCPAccessToken", str]:
        """Create a new token and return (model_instance, raw_token).

        The raw token is ONLY available at creation time. Store it yourself;
        the DB only has the hash.

        ttl_days is clamped to MAX_TTL_DAYS; None / 0 (historically "no
        expiry") also becomes MAX_TTL_DAYS.
        """
        raw = secrets.token_urlsafe(32)
        if not ttl_days or ttl_days > cls.MAX_TTL_DAYS:
            ttl_days = cls.MAX_TTL_DAYS
        expires_at = timezone.now() + timedelta(days=ttl_days)
        token = cls.objects.create(
            user=user,
            name=name,
            token_hash=_hash_token(raw),
            expires_at=expires_at,
        )
        return token, raw

    @classmethod
    def verify(cls, raw: str) -> "MCPAccessToken | None":
        """Look up an active, non-expired token by raw value.

        Returns None if the token is unknown, inactive, or expired.
        """
        if not raw:
            return None
        token_hash = _hash_token(raw)
        try:
            token = cls.objects.select_related("user").get(
                token_hash=token_hash,
                is_active=True,
            )
        except cls.DoesNotExist:
            return None
        if token.expires_at and token.expires_at < timezone.now():
            return None
        return token

    def touch(self) -> None:
        """Update last_used_at. Call on every successful request."""
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])


class MCPOAuthClient(models.Model):
    """Marks an OAuth application as an MCP client, registered by ``oauth.register_client``.

    The marker is what confines the application to the ``mcp`` scope
    (``oauth.MCPScopes``) and what the MCP token verifier requires, so a token
    minted for labs' other OAuth APIs can never call MCP tools, and an MCP
    sign-in can never mint a token for those APIs.
    """

    application = models.OneToOneField(
        settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_client",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mcp_oauth_client"

    def __str__(self) -> str:
        return f"MCP client {self.application_id}"


class MCPAuditLog(models.Model):
    """Audit trail of every MCP tool call.

    Writes are logged in full (args, version transitions). Reads log the tool
    name and scope only (args omitted to save storage — argument shapes for
    reads are trivial).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="mcp_audit_logs",
    )
    tool_name = models.CharField(max_length=100, db_index=True)
    is_write = models.BooleanField(default=False)
    arguments = models.JSONField(default=dict, blank=True)
    success = models.BooleanField()
    error_code = models.CharField(max_length=50, blank=True)
    version_before = models.IntegerField(null=True, blank=True)
    version_after = models.IntegerField(null=True, blank=True)
    #: Set only for a call made with a delegated token (canopy acting for a
    #: visitor): the client that redeemed the grant, and the informational
    #: ``Canopy-Actor`` header naming the agent. ``user`` is the visitor. Empty
    #: for PATs and ordinary OAuth sign-ins, whose caller IS the user.
    client_id = models.CharField(max_length=255, blank=True, default="")
    actor = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "mcp_audit_log"
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["tool_name", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.tool_name} by {self.user} at {self.created_at}"


class DelegatedAccessToken(models.Model):
    """An access token canopy redeemed for a labs visitor (the jwt-bearer grant).

    Kept in its OWN table rather than django-oauth-toolkit's ``AccessToken``, on
    purpose: the toolkit authenticates labs' REST API with any live row in its
    table (``verify_request(scopes=[])`` checks no scope), so a delegated token
    stored there would open labs' whole API to canopy. Here nothing but the MCP
    verifier ever reads it (``delegation.resolve_delegated_token``).

    The raw token is never stored, only its SHA-256 (as for PATs). It is
    sender-constrained: ``cnf_jkt`` is the RFC 7638 thumbprint of the DPoP key
    canopy proved possession of when it redeemed the grant, and every MCP request
    made with it must carry a DPoP proof signed by that same key. A token copied
    out of a log is useless without it. There is no refresh token: canopy gets a
    new one only by redeeming a fresh grant, which labs issues only while the
    visitor is on the page.
    """

    token_checksum = models.CharField(max_length=64, unique=True)
    #: The visitor. Tools run as this user.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_delegated_tokens",
    )
    #: The client that redeemed the grant (canopy's CIMD URL).
    client_id = models.CharField(max_length=255)
    #: RFC 8693 ``act.sub`` — who is acting for the visitor.
    actor = models.CharField(max_length=255, blank=True)
    #: Space-separated scopes, a subset of what the page's grant carried.
    scope = models.CharField(max_length=255)
    cnf_jkt = models.CharField(max_length=64)
    #: The grant's ``jti``, so an audit can join a token to the page visit that issued it.
    grant_jti = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mcp_delegated_access_token"

    def __str__(self) -> str:
        return f"Delegated token for user {self.user_id} via {self.client_id}"

    @property
    def scopes(self) -> list[str]:
        return self.scope.split()


class SeenJTI(models.Model):
    """A ``jti`` already used, so a signed statement works exactly once.

    One table for all three kinds (client assertion, grant, DPoP proof), keyed
    by kind plus a hash of the value. In the database rather than a cache
    because single use has to hold across every worker and task, and has to
    fail CLOSED: an insert that cannot happen refuses the request, where a cache
    that silently drops writes would quietly allow replays.
    """

    key = models.CharField(max_length=200, unique=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "mcp_seen_jti"

    def __str__(self) -> str:
        return self.key
