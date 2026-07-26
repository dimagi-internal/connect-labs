"""Audit event recording service.

``record()`` is the single write path for audit events. It is best-effort by
contract: it never raises, so a broken audit pipeline can never take down a
user-facing action (the same policy as MCPAuditLog's writer). Every event is
written to two sinks:

1. The ``labs_audit_event`` Postgres table (append-only, hot queryable store).
2. A structured-JSON line on the ``connect_labs.audit_trail.stream`` logger →
   stdout → CloudWatch, giving an independent off-DB copy for alerting and a
   fallback if the DB write fails.

Do not put PHI in ``metadata`` — identifiers only.
"""
from __future__ import annotations

import functools
import json
import logging

from django.db import models, transaction

from connect_labs.audit_trail.context import get_audit_context
from connect_labs.audit_trail.models import AuditEvent, Outcome, Source

logger = logging.getLogger(__name__)

# Dedicated logger for the machine-readable event stream. Configured in
# settings LOGGING with a message-only formatter so each line is pure JSON
# (CloudWatch metric filters require it); propagate=False keeps it out of the
# human-readable root handler.
stream_logger = logging.getLogger("connect_labs.audit_trail.stream")


@functools.lru_cache(maxsize=1)
def _char_limits() -> dict[str, int]:
    """Column width of every AuditEvent CharField, read off the model itself."""
    return {
        field.name: field.max_length
        for field in AuditEvent._meta.get_fields()
        if isinstance(field, models.CharField) and field.max_length
    }


def _clamp(kwargs: dict) -> dict:
    """Trim string values to their column width.

    Callers must never hand-copy a max_length: an over-long value has to cost
    us that value's tail, never the whole row. A dropped row is a hole in the
    compliance record, and because the insert failure is swallowed (see
    ``_write_events``) it is a silent one.
    """
    limits = _char_limits()
    return {
        key: value[: limits[key]] if isinstance(value, str) and key in limits else value
        for key, value in kwargs.items()
    }


def record(
    action: str,
    *,
    resource_type: str = "",
    resource_id=None,
    record_count: int | None = None,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    organization_id: int | None = None,
    labs_only: bool = False,
    outcome: str = Outcome.SUCCESS,
    status_code: int | None = None,
    metadata: dict | None = None,
    user=None,
) -> None:
    """Record one audit event. Never raises.

    In a buffered (web request) context the event is queued and flushed by the
    middleware after the response; otherwise it is written immediately with
    whatever attribution the current audit context provides.

    Args:
        action: An ``Action`` choice value ("read", "create", ...).
        resource_type: What kind of thing was touched (LabsRecord ``type``,
            export endpoint name, "auth", ...).
        resource_id: Opaque identifier of the specific resource, if any.
        user: Explicit user override (e.g. auth signals); defaults to the
            audit context's user.
    """
    try:
        event_kwargs = {
            "action": action,
            "resource_type": resource_type or "",
            "resource_id": str(resource_id) if resource_id is not None else "",
            "record_count": record_count,
            "opportunity_id": opportunity_id,
            "program_id": program_id,
            "organization_id": organization_id,
            "labs_only": labs_only,
            "outcome": outcome,
            "status_code": status_code,
            "metadata": metadata or {},
        }
        if user is not None and getattr(user, "pk", None):
            event_kwargs["user_id"] = user.pk
            event_kwargs["username"] = user.username
            event_kwargs["user_email"] = user.email or ""

        ctx = get_audit_context()
        if ctx is not None and ctx.buffer is not None:
            ctx.buffer.append(event_kwargs)
            return
        _write_events([event_kwargs], ctx)
    except Exception:  # pragma: no cover - defensive: audit must never break callers
        logger.exception("Failed to record audit event (non-fatal)")


def flush_buffer(ctx, status_code: int | None = None) -> None:
    """Write out a buffered context's events. Called by the middleware. Never raises."""
    try:
        if not ctx.buffer:
            return
        events, ctx.buffer = ctx.buffer, []
        if status_code is not None:
            for e in events:
                if e.get("status_code") is None:
                    e["status_code"] = status_code
        _write_events(events, ctx)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to flush audit events (non-fatal)")


# Query-string parameters whose values are free text a human typed (search
# boxes, notes) — those can contain PHI content (e.g. a typed patient name),
# unlike identifier params (?username=, ?entity_id=, ?status=), which are the
# point of capturing the query string for session reconstruction.
FREE_TEXT_PARAMS = {"q", "query", "search", "term", "notes", "note", "text", "message", "comment", "title"}


def redact_query_string(query_string: str) -> str:
    """Redact free-text parameter values, keep identifier parameters verbatim.

    Bounded output is part of this helper's contract (callers stash the result
    on a context long before it reaches a row), so it clamps here too — to the
    same column width ``_clamp`` would apply.
    """
    if not query_string:
        return ""
    try:
        from urllib.parse import parse_qsl, urlencode

        pairs = parse_qsl(query_string, keep_blank_values=True)
        redacted = [(k, "[redacted]" if k.lower() in FREE_TEXT_PARAMS else v) for k, v in pairs]
        return urlencode(redacted)[: _char_limits()["query_string"]]
    except Exception:  # pragma: no cover - malformed input: drop rather than risk content
        return ""


# Data writes and exports double as product-analytics feature events — one
# choke point feeds both systems. Reads/lists are excluded (page views cover
# them); payload is resource_type + labs_only only, never identifiers.
_ANALYTICS_ACTIONS = {"create", "update", "delete", "export"}


def _emit_analytics(rows) -> None:
    try:
        from connect_labs.utils import server_analytics

        for row in rows:
            if row.action in _ANALYTICS_ACTIONS and row.outcome == Outcome.SUCCESS:
                server_analytics.send_event(
                    f"data_{row.action}",
                    {"resource_type": row.resource_type, "labs_only": row.labs_only},
                    url="/server/data",
                )
    except Exception:  # pragma: no cover - analytics must never break auditing
        logger.exception("Audit→analytics emit failed (non-fatal)")


def _resolve_context_user(ctx) -> None:
    """Fill ctx user fields from the request's (lazy) user, once available."""
    request = getattr(ctx, "request", None)
    if request is None or ctx.user_id is not None:
        return
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        ctx.user_id = user.pk
        ctx.username = user.username
        ctx.user_email = user.email or ""


def _write_events(events: list[dict], ctx) -> None:
    """Build AuditEvent rows, bulk-insert, and emit JSON stream lines.

    The JSON stream is emitted even if the DB insert fails, so CloudWatch
    always has a copy.
    """
    if ctx is not None:
        _resolve_context_user(ctx)
    envelope = {
        "user_id": ctx.user_id if ctx else None,
        "username": ctx.username if ctx else "",
        "user_email": ctx.user_email if ctx else "",
        "source": (ctx.source if ctx else Source.SYSTEM) or Source.SYSTEM,
        "ip_address": ctx.ip_address if ctx else "",
        "user_agent": ctx.user_agent if ctx else "",
        "request_id": ctx.request_id if ctx else "",
        "path": ctx.path if ctx else "",
        "query_string": ctx.query_string if ctx else "",
    }
    rows = []
    for event_kwargs in events:
        kwargs = {**envelope, **{k: v for k, v in event_kwargs.items() if v is not None or k == "record_count"}}
        # Explicit user on the event wins over the context envelope.
        rows.append(AuditEvent(**_clamp(kwargs)))

    try:
        # Savepoint-wrap so a failed insert can't poison a caller's open
        # transaction (immediate writes may run inside one).
        with transaction.atomic():
            AuditEvent.objects.bulk_create(rows)
    except Exception:
        logger.exception("Audit DB write failed; events preserved in log stream only")

    _emit_analytics(rows)

    for row in rows:
        try:
            payload = row.to_log_dict()
            if payload.get("occurred_at") is None:
                # bulk_create with auto_now_add fills occurred_at on the DB
                # side only after a successful insert; stamp a wall-clock
                # fallback for the stream line.
                from django.utils import timezone

                payload["occurred_at"] = timezone.now().isoformat()
            stream_logger.info(json.dumps(payload, default=str))
        except Exception:  # pragma: no cover - defensive
            logger.exception("Audit stream emit failed (non-fatal)")
