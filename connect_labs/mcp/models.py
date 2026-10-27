"""Models for the labs MCP server.

MCPAccessToken: Personal Access Tokens for Claude Code clients.
MCPAuditLog: Audit trail of every tool call (added in Task D1).
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

from . import token_scopes


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
    scope = models.CharField(
        max_length=20,
        choices=token_scopes.SCOPE_CHOICES,
        default=token_scopes.FULL,
        help_text="What the token may reach. See connect_labs/mcp/token_scopes.py.",
    )

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
        scope: str = token_scopes.FULL,
        *,
        full_access=None,
    ) -> tuple["MCPAccessToken", str]:
        """Create a new token and return (model_instance, raw_token).

        The raw token is ONLY available at creation time. Store it yourself;
        the DB only has the hash.

        ttl_days is clamped to MAX_TTL_DAYS; None / 0 (historically "no
        expiry") also becomes MAX_TTL_DAYS.

        scope is one of ``token_scopes.SCOPE_CHOICES``; anything else raises
        rather than minting a token whose reach nobody decided.

        A FULL token needs ``full_access``: the ``full_access.FullAccess`` that says why
        this one may be minted (a person's passkey approval through canopy, an account
        allowed to self-mint, or the server-side command). Without it this raises, so a
        new mint path cannot skip the approval by forgetting to ask for it.
        """
        if scope not in dict(token_scopes.SCOPE_CHOICES):
            raise ValueError(f"Unknown token scope {scope!r}")
        if scope == token_scopes.FULL:
            from .full_access import require

            require(full_access, user)
        raw = secrets.token_urlsafe(32)
        if not ttl_days or ttl_days > cls.MAX_TTL_DAYS:
            ttl_days = cls.MAX_TTL_DAYS
        expires_at = timezone.now() + timedelta(days=ttl_days)
        token = cls.objects.create(
            user=user,
            name=name,
            token_hash=_hash_token(raw),
            expires_at=expires_at,
            scope=scope,
        )
        return token, raw

    @classmethod
    def verify(cls, raw: str) -> "MCPAccessToken | None":
        """Look up an active, non-expired token by raw value.

        Returns None if the token is unknown, inactive, or expired, or its user is
        deactivated: a person an admin switched off loses every token with them.
        """
        if not raw:
            return None
        token_hash = _hash_token(raw)
        try:
            token = cls.objects.select_related("user").get(
                token_hash=token_hash,
                is_active=True,
                user__is_active=True,
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
    #: Delegated calls only: what canopy's verified ``Canopy-Execution`` statement said
    #: about who executed the call (agent, commit, sensitive-data declaration and who made it,
    #: runner and its flags, turn, session) -- or ``{"refused": code}``. See
    #: ``mcp/execution.py``.
    execution = models.JSONField(default=dict, blank=True)
    #: The call's audit-context id ("mcp:<hex>"): the same id stamps every
    #: ``AuditEvent`` the call caused (LabsRecord reads, exports from Connect), so
    #: a tool call joins to what it touched.
    request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    #: Wall time of the whole call, gates and handler included.
    duration_ms = models.IntegerField(null=True, blank=True)
    #: The scope the call named, read from its arguments for EVERY call -- reads
    #: too, whose arguments are otherwise not kept. Opaque ids only, for usage
    #: analytics by organisation / programme / opportunity.
    opportunity_ids = models.JSONField(default=list, blank=True)
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    organization_slug = models.CharField(max_length=120, blank=True, default="")
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


class MCPJob(models.Model):
    """A long-running MCP tool call, run as a job instead of inside the request (``mcp/jobs.py``).

    The durable record a caller polls with ``job_status``: who started it (the job runs
    as them, and only they can read it), what was asked, how restricted the call was,
    where it is, and how it ended. The result lives here rather than in Celery's result
    backend so it outlives a Redis flush and a deploy, and is pruned after
    ``MCP_JOB_RETENTION_DAYS``.

    The lease fields are what make a redelivered job safe: a worker holds the job while
    ``lease_until`` is in the future and renews it as it goes, so a copy of the message
    delivered while it runs waits rather than running it twice, and a copy delivered
    after its worker died takes it over.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATE_CHOICES = [
        (QUEUED, "Queued"),
        (RUNNING, "Running"),
        (SUCCEEDED, "Succeeded"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    ]
    LIVE_STATES = (QUEUED, RUNNING)
    TERMINAL_STATES = (SUCCEEDED, FAILED, CANCELLED)

    WORKER = "worker"
    STANDALONE = "standalone"
    RUNNER_CHOICES = [(WORKER, "Worker service (Celery queue)"), (STANDALONE, "Standalone Fargate task")]

    id = models.UUIDField(primary_key=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mcp_jobs")
    tool_name = models.CharField(max_length=100)
    arguments = models.JSONField(default=dict, blank=True)
    #: The calling MCP request's "no user visit data" answer, carried into the job.
    restricted = models.BooleanField(default=False)
    #: Set for a delegated call (canopy acting for a visitor), as on MCPAuditLog.
    client_id = models.CharField(max_length=255, blank=True, default="")
    actor = models.CharField(max_length=100, blank=True, default="")
    runner = models.CharField(max_length=20, choices=RUNNER_CHOICES, default=WORKER)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=QUEUED, db_index=True)
    #: {current, total, message} as the tool last reported it.
    progress = models.JSONField(default=dict, blank=True)
    result = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    error_code = models.CharField(max_length=50, blank=True, default="")
    error = models.TextField(blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    cancel_requested = models.BooleanField(default=False)
    #: Who holds the job now (``<hostname>:<pid>:<nonce>``), until when, and on which
    #: worker -- the worker's shutdown hook hands back what that worker holds.
    lease_owner = models.CharField(max_length=255, blank=True, default="")
    lease_until = models.DateTimeField(null=True, blank=True)
    worker = models.CharField(max_length=255, blank=True, default="")
    #: The standalone task running it, when ``runner`` is standalone.
    task_arn = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mcp_job"
        indexes = [models.Index(fields=["user", "-created_at"])]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.tool_name} job {self.id} ({self.state})"


class StagedEdit(models.Model):
    """An edit an agent proposed through canopy, held until a person confirms it in chat.

    canopy (acting for a visitor) does not call Labs' edit tools directly: it proposes
    one (``workflow_edit_propose``), which checks it and stores it here unsaved. The
    confirm card canopy draws for that proposal shows the change to the person looking
    at it and, on their click, saves it (``workflow_edit_apply``) by running the edit
    tool as that person. The agent never holds the confirm token, so it cannot confirm
    its own edit. See ``mcp/tools/workflow_edit.py``.
    """

    PENDING = "pending"
    APPLIED = "applied"
    DECLINED = "declined"
    FAILED = "failed"
    STATE_CHOICES = [(PENDING, "Pending"), (APPLIED, "Applied"), (DECLINED, "Declined"), (FAILED, "Failed")]

    id = models.UUIDField(primary_key=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mcp_staged_edits")
    tool_name = models.CharField(max_length=100)
    arguments = models.JSONField(default=dict, blank=True)
    #: sha256 of (user, tool, arguments): how the confirm card, which knows only the
    #: proposal's input, finds it again.
    digest = models.CharField(max_length=64, db_index=True)
    #: What the card shows: a one-line summary and the diff, computed at proposal time.
    preview = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    client_id = models.CharField(max_length=255, blank=True, default="")
    actor = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=PENDING, db_index=True)
    result = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class KeptResult(models.Model):
    """What a tool produced, kept for the person it ran as and named by ``labs-result://<id>``.

    A tool that produces rows (``data_query``, ``workflow_run_indicators``) or a result a
    View draws (``targeting_pmc_states``) keeps it here and returns a summary plus an MCP
    ``resource_link``; any MCP client reads it back with ``resources/read``, as itself, a
    page at a time (``mcp/kept_results.py``). It lives a day: an expired one reads as
    ``expired`` and the hourly sweep deletes it.

    ``sensitive``, ``opportunity_ids``, ``through_canopy`` and ``scope`` are what the read
    rules ask (who else could have run it, and what a read through canopy may carry), kept
    beside the payload rather than in it, so the payload is exactly what the tool produced.
    """

    #: The opaque, unguessable id in its URI.
    id = models.CharField(primary_key=True, max_length=64, editable=False)
    #: The labs user the call ran as -- the person, also when canopy called for them.
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=100)
    name = models.CharField(max_length=200, blank=True, default="")
    description = models.CharField(max_length=500, blank=True, default="")
    mime_type = models.CharField(max_length=100, default="application/json")
    payload = models.JSONField()
    #: The audit context's id of the call that made it (its MCPAuditLog row carries it too).
    tool_call_id = models.CharField(max_length=255, blank=True, default="")
    #: Whether it holds labs' sensitive data (rows from production visits). Fails closed.
    sensitive = models.BooleanField(default=True)
    #: The opportunities the call read.
    opportunity_ids = models.JSONField(default=list, blank=True)
    #: Made by a delegated call (canopy acting for the person), so it already went to an
    #: agent Labs had cleared: what lets the person's own View read a sensitive one back.
    through_canopy = models.BooleanField(default=False)
    #: Anything else a read rule needs (a run's ``run_id``, ``opportunity_id``, ``program_id``).
    scope = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "mcp_kept_result"

    def __str__(self) -> str:
        return f"labs-result://{self.id}"

    @property
    def access(self) -> dict:
        """What the read rules ask, in one dict."""
        return {
            **(self.scope or {}),
            "opportunity_ids": list(self.opportunity_ids or []),
            "sensitive": self.sensitive,
            "through_canopy": self.through_canopy,
        }


class FullAccessSelfMint(models.Model):
    """An account allowed to mint full-access MCP credentials for itself, with no
    passkey approval (``full_access.may_self_mint``).

    For an agent account that is never in a project space with real data, such as ACE:
    approving each of its tokens would guard nothing. Ticked by Dimagi staff in the
    Django admin; everyone else's full token needs their passkey through canopy.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="full_access_self_mint"
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    #: Why this account may: shown in the admin beside the tick.
    reason = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "mcp_full_access_self_mint"
        verbose_name = "full-access self-mint"

    def __str__(self) -> str:
        return f"{self.user} may self-mint full access"


def _pending_ref() -> str:
    return secrets.token_urlsafe(24)


class PendingFullAccess(models.Model):
    """A full-access credential waiting on its person's passkey approval in canopy
    (``full_access``). ``ref`` is the sign-off's ``site_ref``, so an approval for one
    pending mint cannot complete another; the row is completed once."""

    TOKEN_CREATE, TOKEN_ROTATE, TOKEN_BROWSER, OAUTH = "token_create", "token_rotate", "token_browser", "oauth"
    KINDS = [
        (TOKEN_CREATE, "Create a token"),
        (TOKEN_ROTATE, "Rotate a token"),
        (TOKEN_BROWSER, "Create a token for a local app"),
        (OAUTH, "Sign an MCP app in"),
    ]

    ref = models.CharField(max_length=64, unique=True, default=_pending_ref, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=20, choices=KINDS)
    #: What to do once approved (a token's name and lifetime, the callback, the OAuth
    #: request). Never a secret: the token does not exist yet.
    payload = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    sign_off_id = models.CharField(max_length=40, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    #: The approval that completed it; unique, so one approval completes one thing.
    approval_jti = models.CharField(max_length=64, null=True, blank=True, unique=True)

    class Meta:
        db_table = "mcp_pending_full_access"
