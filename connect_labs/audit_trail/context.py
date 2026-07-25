"""Request/task-scoped audit context propagation.

A contextvar carries "who is acting and from where" from the edge (middleware,
MCP auth, Celery task entry) down to the data-access choke points, which call
``service.record()`` without needing a request object.

Two modes:

- **Buffered** (web requests): ``AuditTrailMiddleware`` opens a context with a
  buffer; ``record()`` appends pending events; the middleware flushes them
  after the response — outside the view's ATOMIC_REQUESTS transaction — so
  audit rows survive request rollbacks and carry the final status code.
- **Immediate** (Celery, MCP, scripts): ``audit_context()`` sets attribution
  with no buffer; ``record()`` writes each event straight to the DB + log
  stream.

Contextvars propagate across Django's sync/async boundaries (asgiref) but NOT
into ThreadPoolExecutor workers — events recorded from hand-rolled thread
pools fall back to unattributed immediate writes, which is acceptable
best-effort behavior.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class AuditContext:
    user_id: int | None = None
    username: str = ""
    user_email: str = ""
    source: str = "system"
    ip_address: str = ""
    user_agent: str = ""
    request_id: str = ""
    path: str = ""
    query_string: str = ""
    # When not None, record() appends pending event kwargs here instead of
    # writing immediately; the middleware flushes at response time.
    buffer: list[dict] | None = None
    # Set by the middleware so user attribution can be resolved lazily at
    # flush time (request.user is a lazy object until first access).
    request: object = field(default=None, repr=False)


_current: contextvars.ContextVar[AuditContext | None] = contextvars.ContextVar("labs_audit_context", default=None)


def get_audit_context() -> AuditContext | None:
    return _current.get()


def set_audit_context(ctx: AuditContext) -> contextvars.Token:
    return _current.set(ctx)


def reset_audit_context(token: contextvars.Token) -> None:
    _current.reset(token)


@contextmanager
def audit_context(user=None, source: str = "script", request_id: str = "", **extra):
    """Attribute audit events recorded inside the block (Celery/MCP/scripts).

    Args:
        user: Django User instance (or None for a system principal).
        source: One of the ``Source`` choices — "celery", "mcp", "script".
        request_id: Optional correlation id linking back to the originating
            web request or task id.
        **extra: Additional AuditContext field overrides (e.g. ``username``
            when only a username string is known).
    """
    ctx = AuditContext(source=source, request_id=request_id, **extra)
    if user is not None and getattr(user, "pk", None):
        ctx.user_id = user.pk
        ctx.username = user.username
        ctx.user_email = user.email or ""
    token = set_audit_context(ctx)
    try:
        yield ctx
    finally:
        reset_audit_context(token)
