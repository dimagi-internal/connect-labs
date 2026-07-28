"""HIPAA-grade audit event model.

One row per auditable event: PHI/data reads and writes at the API-client choke
points, auth events, access denials, exports, and audit-log reviews. Field set
follows ASTM E2147 / NIST SP 800-66r2: who, what action, which resource, when,
where from, and outcome. Events carry opaque identifiers only — never record
names, form answers, or other data content in this table.

The table is append-only: a Postgres trigger (pgtrigger.Protect) rejects
UPDATE and DELETE at the database level. The only sanctioned bypass is the
retention prune task, which uses pgtrigger.ignore() after verifying the day's
S3 archive exists.
"""
import uuid

import pgtrigger
from django.conf import settings
from django.db import models
from django.utils import timezone


class Action(models.TextChoices):
    # Data access (the HIPAA-critical half most systems miss)
    LIST = "list", "List/query records"
    READ = "read", "Read record"
    EXPORT = "export", "Bulk export/fetch"
    # Data mutation
    CREATE = "create", "Create record"
    UPDATE = "update", "Update record"
    DELETE = "delete", "Delete record"
    # Navigation (authenticated HTML page renders — makes the trail a complete
    # per-user click-path even for pages that touch no data)
    PAGE_VIEW = "page_view", "Page view"
    # Authentication / authorization
    LOGIN = "login", "Login"
    LOGIN_FAILED = "login_failed", "Login failed"
    LOGOUT = "logout", "Logout"
    ACCESS_DENIED = "access_denied", "Access denied"
    # Meta
    REVIEW = "review", "Audit log review"
    CANARY = "canary", "Pipeline canary"


class Outcome(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"


class Source(models.TextChoices):
    WEB = "web", "Web request"
    MCP = "mcp", "MCP tool call"
    CELERY = "celery", "Background task"
    SCRIPT = "script", "Script/management command"
    SYSTEM = "system", "System"


class AuditEvent(models.Model):
    """Append-only audit event (ASTM E2147 field set)."""

    event_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Stamped by service.record() at the moment the access HAPPENS, not when the
    # row is written. This was auto_now_add, which meant bulk_create stamped every
    # event in a request identically at flush time — a request that made 347
    # sequential API calls recorded all 347 as one instant, so intra-request
    # ordering was lost and the timestamp was wrong by the request's duration.
    # "When did this access occur" is the whole point of an audit trail, so the
    # default here is only a backstop for rows built outside record().
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    # Who — FK for joins, plus snapshots so the row stays useful if the user
    # is deleted (same rationale as CustomPGHistoryMiddleware).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    username = models.CharField(max_length=150, blank=True, default="")
    user_email = models.CharField(max_length=254, blank=True, default="")

    # What
    action = models.CharField(max_length=20, choices=Action.choices)
    resource_type = models.CharField(max_length=100, blank=True, default="", db_index=True)
    resource_id = models.CharField(max_length=100, blank=True, default="")
    record_count = models.IntegerField(null=True, blank=True)

    # Scope (opaque identifiers, mirrors labs_context)
    opportunity_id = models.IntegerField(null=True, blank=True)
    program_id = models.IntegerField(null=True, blank=True)
    organization_id = models.IntegerField(null=True, blank=True)
    # True when the event touched only labs-local synthetic data (opp id >=
    # LABS_ONLY_OPP_ID_FLOOR) — real PHI is never involved for these.
    labs_only = models.BooleanField(default=False)

    # Where / how
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.SYSTEM)
    ip_address = models.CharField(max_length=45, blank=True, default="")
    user_agent = models.CharField(max_length=300, blank=True, default="")
    # Not always a bare UUID: MCP stamps "mcp:<tool_name>:<8 hex>" and Celery
    # stamps "celery:<task_id>", so this is sized well past 36.
    request_id = models.CharField(max_length=64, blank=True, default="")
    path = models.CharField(max_length=300, blank=True, default="")
    # Query string with free-text parameter values redacted (see
    # service.redact_query_string) — identifiers like ?username=/entity_id=
    # are kept for forensic session reconstruction; typed content is not.
    query_string = models.CharField(max_length=500, blank=True, default="")

    # Outcome
    outcome = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.SUCCESS)
    status_code = models.IntegerField(null=True, blank=True)

    # PHI-free structured extras (experiment name, endpoint, error code, ...)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "labs_audit_event"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["user", "-occurred_at"]),
            models.Index(fields=["action", "-occurred_at"]),
            models.Index(fields=["opportunity_id", "-occurred_at"]),
        ]
        triggers = [
            pgtrigger.Protect(
                name="append_only",
                operation=pgtrigger.Update | pgtrigger.Delete,
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.resource_type} by {self.username or 'anonymous'} at {self.occurred_at}"

    def to_log_dict(self) -> dict:
        """Serialize for the structured-JSON log stream and S3 archive."""
        return {
            "event_uuid": str(self.event_uuid),
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "user_id": self.user_id,
            "username": self.username,
            "user_email": self.user_email,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "record_count": self.record_count,
            "opportunity_id": self.opportunity_id,
            "program_id": self.program_id,
            "organization_id": self.organization_id,
            "labs_only": self.labs_only,
            "source": self.source,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "request_id": self.request_id,
            "path": self.path,
            "query_string": self.query_string,
            "outcome": self.outcome,
            "status_code": self.status_code,
            "metadata": self.metadata,
        }
