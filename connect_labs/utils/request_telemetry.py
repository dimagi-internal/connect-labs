"""Per-request performance telemetry: duration, DB queries, outbound HTTP fan-out.

Why this exists
---------------
On 2026-07-29 the single web task sat at 100% CPU for 54 minutes. The cause was
one request path issuing ~139 sequential outbound HTTP calls to production
Connect, repeated on every page open (#1037). Diagnosing it took log archaeology
— counting repeated ``httpx`` INFO lines by hand — because **nothing in labs
recorded what a request cost**. The web log group held ~40,000 lines for that
window and not one of them said "this request took N seconds and made M calls".

This module is that missing line. It is deliberately the *diagnostic* layer, not
a request log: ALB access logs already record every request's URL, status and
latency far more cheaply. What the ALB cannot see is WHY a request was slow —
how many database queries and how many outbound HTTP calls it made, and to whom.
That is what gets logged here, and only for requests that actually look wrong, so
the normal case costs two counters and no output at all.

Reading it
----------
Every line is a single JSON object on the ``connect_labs.telemetry.request``
logger, so CloudWatch Logs Insights can parse it directly::

    fields @timestamp, path, duration_ms, outbound_ms, db_ms, self_ms,
           outbound_calls, db_queries, reason
    | filter ispresent(outbound_calls)
    | sort duration_ms desc

The fan-out that caused the incident would surface immediately as
``outbound_calls: 139`` on a single request — no archaeology.

Counts alone answer only the fan-out question
---------------------------------------------
The original version recorded how MANY outbound calls and queries a request made
but not how LONG they took, which is only enough to diagnose an incident shaped
like #1037. The 2026-08-11 review hit the other shape: ``/audit/api/<id>/bulk-data/``
taking 9–87 seconds on **2** outbound calls and ~16 queries. Nothing recorded could
say whether those seconds were spent waiting on Connect or burning local CPU, so
the runbook's own "slow_no_saturation → check outbound_by_host" branch dead-ended.

``outbound_ms``, ``db_ms`` and ``self_ms`` split the duration three ways so that
question is answerable from the log line. They are wall-clock waits and can
overlap slightly with each other under concurrency; treat them as attribution,
not as an exact budget that must sum to ``duration_ms``.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from django.conf import settings
from django.db import connection

logger = logging.getLogger("connect_labs.telemetry.request")

# Thresholds. A request trips the log if it exceeds ANY of them. Defaults are set
# well above normal labs traffic (median ALB p95 is 0.23s) so a clean request is
# silent and anything logged is worth reading.
SLOW_REQUEST_MS = getattr(settings, "TELEMETRY_SLOW_REQUEST_MS", 3000)
OUTBOUND_CALL_LIMIT = getattr(settings, "TELEMETRY_OUTBOUND_CALL_LIMIT", 20)
DB_QUERY_LIMIT = getattr(settings, "TELEMETRY_DB_QUERY_LIMIT", 100)


@dataclass
class RequestStats:
    """Counters for the request currently being served on this context."""

    outbound_calls: int = 0
    outbound_by_host: Counter = field(default_factory=Counter)
    outbound_ms: float = 0.0
    db_queries: int = 0
    db_ms: float = 0.0

    def top_outbound(self, n: int = 3) -> dict[str, int]:
        return dict(self.outbound_by_host.most_common(n))


# A ContextVar rather than thread-local: labs runs on UvicornWorker, where sync
# views execute in an asgiref thread pool. contextvars propagate across that
# boundary (thread-locals do not), so the counters follow the request.
_stats: contextvars.ContextVar[RequestStats | None] = contextvars.ContextVar("labs_request_stats", default=None)


def current_stats() -> RequestStats | None:
    """Stats for the in-flight request, or None outside one (Celery, shell, tests)."""
    return _stats.get()


def record_outbound_call(host: str, elapsed_ms: float = 0.0) -> None:
    """Count one outbound HTTP call against the in-flight request. No-op outside one."""
    stats = _stats.get()
    if stats is None:
        return
    stats.outbound_calls += 1
    stats.outbound_by_host[host] += 1
    stats.outbound_ms += elapsed_ms


# Where the start time is stashed between the request and response hooks. An
# attribute on the Request object rather than a ContextVar: with HTTP keep-alive
# and connection pooling several calls can be in flight, and a single shared
# "started" value would attribute the wrong span to each of them.
_STARTED_ATTR = "_labs_telemetry_started"


def _on_request(request) -> None:
    try:
        setattr(request, _STARTED_ATTR, time.perf_counter())
    except Exception:  # telemetry must never break the call it is measuring
        pass


def _on_response(response) -> None:
    try:
        started = getattr(response.request, _STARTED_ATTR, None)
        # isinstance, not truthiness: a client whose request hook never ran (or a
        # test double) yields a non-numeric value that would otherwise poison the
        # arithmetic and cost the call its COUNT as well as its timing.
        elapsed_ms = (time.perf_counter() - started) * 1000 if isinstance(started, float) else 0.0
        record_outbound_call(response.request.url.host, elapsed_ms)
    except Exception:  # telemetry must never break the call it is measuring
        pass


def httpx_event_hooks() -> dict[str, list]:
    """Event hooks to pass to ``httpx.Client(event_hooks=...)``.

    A first-class httpx feature, not a monkeypatch — every client that opts in
    gets counted, and one that doesn't simply isn't measured.

    Timing is to response HEADERS, not through the body download, because that is
    where the response hook fires. For the calls that matter here — a slow
    upstream sitting on a query — that is the number you want anyway.
    """
    return {"request": [_on_request], "response": [_on_response]}


class RequestTelemetryMiddleware:
    """Log a structured line for any request that looks expensive.

    Sync-only on purpose. Django adapts it into the same thread-sensitive
    executor the view runs in, which is what keeps ``execute_wrapper`` counting
    the view's own queries rather than a different thread's.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        stats = RequestStats()
        token = _stats.set(stats)
        started = time.perf_counter()

        def count_queries(execute, sql, params, many, context):
            stats.db_queries += 1
            q_started = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                stats.db_ms += (time.perf_counter() - q_started) * 1000

        try:
            # execute_wrapper is the supported hook and, unlike connection.queries,
            # works with DEBUG=False — which is the only mode that matters here.
            with connection.execute_wrapper(count_queries):
                response = self.get_response(request)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            try:
                self._maybe_log(request, stats, duration_ms)
            except Exception:
                logger.debug("request telemetry failed", exc_info=True)
            _stats.reset(token)

        return response

    def _maybe_log(self, request, stats: RequestStats, duration_ms: int) -> None:
        reasons = []
        if duration_ms >= SLOW_REQUEST_MS:
            reasons.append("slow")
        if stats.outbound_calls >= OUTBOUND_CALL_LIMIT:
            reasons.append("outbound_fanout")
        if stats.db_queries >= DB_QUERY_LIMIT:
            reasons.append("db_fanout")
        if not reasons:
            return

        user = getattr(request, "user", None)
        payload = {
            "event": "slow_request",
            "reason": ",".join(reasons),
            "method": request.method,
            "path": request.path,
            "duration_ms": duration_ms,
            "outbound_calls": stats.outbound_calls,
            "outbound_ms": int(stats.outbound_ms),
            "outbound_by_host": stats.top_outbound(),
            "db_queries": stats.db_queries,
            "db_ms": int(stats.db_ms),
            # What is left after waiting on Postgres and on upstream HTTP. A large
            # remainder means the time is in our own Python, and neither of the
            # other two numbers will lead you to it.
            "self_ms": max(0, duration_ms - int(stats.outbound_ms) - int(stats.db_ms)),
            "username": getattr(user, "username", None) if user and user.is_authenticated else None,
        }
        # Never log the query string: labs URLs carry opportunity and record ids
        # and this stream is not the audit trail.
        logger.warning(json.dumps(payload, default=str))
