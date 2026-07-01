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
        """
        raw = secrets.token_urlsafe(32)
        expires_at = None
        if ttl_days:
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
