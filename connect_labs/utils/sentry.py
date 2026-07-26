"""Sentry event enrichment and noise filtering for the labs deployment.

Two problems this solves.

**Attribution — why every issue showed ``Users: 0``.** Sentry's Django
integration only attaches ``request.user`` when ``send_default_pii=True``
(see ``sentry_sdk.integrations.django._set_user_info``) — a switch that also
ships cookies, request bodies and client IPs. Labs sits next to PHI, so that
switch stays off. Instead we attach *only* the acting identity, explicitly,
from the audit-trail contextvar the codebase already opens at every edge:

* web requests — ``AuditTrailMiddleware``
* MCP tool calls — ``audit_context()`` in ``connect_labs.mcp.server``
* Celery tasks — the ``task_prerun`` receiver in ``audit_trail.signals``

Reusing that single source means Sentry and the HIPAA audit trail can never
disagree about who did something. Explicitly-set ``user`` fields survive
``send_default_pii=False``: the SDK's ``EventScrubber`` denylists
``ip_address``/``remote_addr`` but not ``id``/``username``/``email``.

**Noise.** Two high-volume issue classes were drowning real errors: internet
scanners opening websockets against the ALB, and FastMCP's own ``ERROR`` log
for every ``ToolError`` we raise — including plain input-validation errors
("Provide exactly one of opportunity_id / program_id"), which are 4xx-shaped
user errors, not defects. Genuine tool failures are still reported: the
``UPSTREAM_ERROR`` branch in ``mcp.server`` logs them itself, with the
original traceback and (now) full user attribution.

Everything here is best-effort by contract — a bug in enrichment must never
swallow the error being reported, so each step is independently guarded and
falls back to sending the event unchanged.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# FastMCP's tool dispatcher logs an ERROR for every ToolError raised out of a
# handler (``_call_tool``: ``except FastMCPError: logger.exception(...)``, and
# ToolError is a FastMCPError). Ours are raised deliberately: MCPToolError (bad
# input / not found / permission / rate limit) is a 4xx-shaped user error, and
# the UPSTREAM_ERROR branch already reports the underlying exception itself with
# a real traceback. Either way the FastMCP-logged copy is duplicate or noise,
# and it arrives unattributed — it is logged after our audit context is gone.
# Scoped to ToolError so a genuine FastMCP-internal failure still reports.
_DUPLICATE_TOOL_ERROR_LOGGER = "fastmcp.server.server"

# Ambient internet noise: scanners and misrouted proxies open websocket
# connections against the ALB, which Django's ASGI handler rejects. Nothing is
# broken and there is no action to take.
_WEBSOCKET_REJECTION = "Django can only handle ASGI/HTTP connections"


def _exception_type_and_value(event, hint) -> tuple[str, str]:
    """(type_name, message) for the event's exception, or ("", "")."""
    exc_info = (hint or {}).get("exc_info")
    if exc_info and len(exc_info) >= 2 and exc_info[1] is not None:
        return type(exc_info[1]).__name__, str(exc_info[1])
    try:
        values = event.get("exception", {}).get("values") or []
        if values:
            return values[-1].get("type") or "", values[-1].get("value") or ""
    except AttributeError:
        pass
    return "", ""


def _is_noise(event, hint) -> bool:
    exc_type, exc_value = _exception_type_and_value(event, hint)
    if exc_type == "ToolError" and event.get("logger") == _DUPLICATE_TOOL_ERROR_LOGGER:
        return True
    if exc_type == "ValueError" and _WEBSOCKET_REJECTION in exc_value:
        return True
    return False


def _current_audit_context():
    """The audit context for this request/task, with its user resolved.

    Web requests carry a lazy ``request.user``; resolving it can touch the
    session store and the DB, which may itself fail while an error is being
    handled (a broken transaction, a dead connection). Failure here just means
    an unattributed event, never a lost one.
    """
    try:
        from connect_labs.audit_trail.context import get_audit_context
        from connect_labs.audit_trail.service import resolve_context_user

        ctx = get_audit_context()
        if ctx is None:
            return None
        try:
            resolve_context_user(ctx)
        except Exception:  # noqa: BLE001 - unattributed beats undelivered
            pass
        return ctx
    except Exception:  # noqa: BLE001 - audit_trail unavailable (e.g. early boot)
        return None


def _attribute(event, ctx) -> None:
    """Stamp the acting user and labs scope onto the event."""
    if ctx.user_id is not None:
        # `setdefault`-style: never clobber a user another integration resolved.
        user = event.setdefault("user", {})
        user.setdefault("id", str(ctx.user_id))
        if ctx.username:
            user.setdefault("username", ctx.username)
        if ctx.user_email:
            user.setdefault("email", ctx.user_email)

    tags = event.setdefault("tags", {})
    if ctx.source:
        tags.setdefault("labs.source", ctx.source)
    if ctx.request_id:
        tags.setdefault("labs.request_id", ctx.request_id)
    # For MCP this is "mcp:<tool_name>" and for Celery the task's dotted path —
    # the single most useful grouping dimension outside a web request.
    if ctx.path:
        tags.setdefault("labs.path", ctx.path)

    labs_context = getattr(getattr(ctx, "request", None), "labs_context", None)
    if isinstance(labs_context, dict):
        for key in ("opportunity_id", "program_id", "organization_id"):
            value = labs_context.get(key)
            if value is not None:
                tags.setdefault(f"labs.{key}", str(value))


def before_send(event, hint):
    """Sentry ``before_send`` hook: drop known noise, attribute the rest."""
    try:
        if _is_noise(event, hint):
            return None
    except Exception:  # noqa: BLE001 - when in doubt, keep the event
        pass

    try:
        ctx = _current_audit_context()
        if ctx is not None:
            _attribute(event, ctx)
    except Exception:  # noqa: BLE001 - enrichment must never drop an event
        pass

    return event
