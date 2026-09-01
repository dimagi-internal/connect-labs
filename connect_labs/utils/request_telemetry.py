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

A residual is only as honest as its inputs
------------------------------------------
``self_ms`` is what is LEFT after the two measured buckets, so an unmeasured wait
does not go missing — it gets relabelled as our own CPU. That is exactly what
happened in #1298: outbound instrumentation was opt-in, one client had opted in,
and a 16.5s login that spent 15.0s waiting on production Connect was logged as
``outbound_calls: 0`` with 99% ``self_ms``. Instrumentation is now default-on for
every httpx client (``install_httpx_instrumentation``).

That fixed WHICH calls are measured. #1386 was the same error one level down — HOW
MUCH of each call: the response hook fires at headers, so the body download was
outside the measured span and landed in the residual. On endpoints returning JSON
that is noise; on the audit image proxy, which streams whole JPEGs through Python,
it was the entire cost, and it ranked a view that does no image work as the top CPU
consumer on the web tier. ``send`` is now wrapped so the body read is billed to
``outbound_ms`` too (see ``_patch_client_send``).

Two gaps remain, deliberately: ``stream=True`` calls return at headers and the
caller reads the body on its own time, and anything reaching the network by a route
other than httpx is invisible — prefer httpx.

``self_ms`` is a residual, so it cannot say WHICH
-------------------------------------------------
Everything above makes the residual smaller and more honest. None of it makes the
residual *attributable*: "2.6 s left over" is equally consistent with our Python
burning 2.6 s of CPU and with the request's thread sitting descheduled for 2.6 s
while three gunicorn workers share one vCPU. Those two have opposite fixes, and no
combination of wall-clock buckets distinguishes them.

``cpu_ms`` does, in one number. It is ``time.thread_time()`` across the middleware
— CPU actually consumed by the thread serving this request, which under ASGI is the
same thread-sensitive executor thread the sync middleware chain and the view run in.
Read it against ``self_ms``:

* ``cpu_ms`` ≈ ``self_ms`` — the time really is our Python. Profile the view.
* ``cpu_ms`` ≪ ``self_ms`` — the thread was **not running**. Either it was waiting
  on something outside the two measured buckets, or it was ready and descheduled,
  i.e. CPU contention. Profiling the view will find nothing.

An unbiased sample, and why ``sampled`` is a field and not a ``reason``
----------------------------------------------------------------------
Everything else here is threshold-gated: a line exists only because the request
crossed ``SLOW_REQUEST_MS``. That is right for *finding* an incident and invalid for
*comparing* anything, because the gate truncates the distribution differently at
every load level — when the tier is busy, ordinary requests get pushed just over the
floor and pile up against it, dragging the busy band's mean DOWN. A comparison across
load levels drawn from this stream reports an artefact with the confidence of real
data (#1386 produced two opposite wrong conclusions about the same endpoint in one
day, both this way).

``TELEMETRY_SAMPLE_RATE`` fixes that by logging a fixed fraction *regardless of
duration*, so the sampled population is a fair draw from all requests. The draw
happens before the request runs and cannot depend on its outcome.

The trap this design avoids: a sampled request that is ALSO slow gets
``reason: "slow,sample"``, so a Logs Insights ``filter reason = "sample"`` silently
drops exactly the slow ones — re-introducing the bias the sample exists to remove,
in the query rather than the data. So the authoritative filter is the boolean field:

    fields @timestamp, path, duration_ms, cpu_ms, self_ms, db_ms, outbound_ms
    | filter sampled = 1
    | stats count(), pct(duration_ms, 50), pct(cpu_ms, 50) by bin(5m)

Never mix the two populations in one statistic; ``sampled = 1`` is unbiased,
``sampled = 0`` is the ``duration >= 3s`` tail.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import random
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

# The unbiased sample (see the module docstring). OFF at 0.0, which is the
# pre-existing behaviour exactly: no request is logged for being sampled, and the
# healthy path still costs two counters and no output. Turning it on is a one-value
# edit to deploy/task-definitions/web.json, the same shape as WEB_LIMIT_CONCURRENCY.
#
# Scope it with the prefix rather than raising the rate globally: the population
# worth sampling is usually one endpoint under investigation, and an empty prefix
# means every path, which on this tier is ~28k requests/day.
SAMPLE_RATE = float(getattr(settings, "TELEMETRY_SAMPLE_RATE", 0.0))
SAMPLE_PATH_PREFIX = getattr(settings, "TELEMETRY_SAMPLE_PATH_PREFIX", "")


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


def add_outbound_ms(elapsed_ms: float) -> None:
    """Add time to the outbound bucket WITHOUT counting another call.

    The response hook fires at headers and already counted the call; the body
    download happens after it and is billed here (see ``_patch_client_send``).
    Separating "count" from "time" is what keeps ``outbound_calls`` meaning
    number-of-requests while ``outbound_ms`` means time-actually-spent-upstream.
    """
    stats = _stats.get()
    if stats is None:
        return
    stats.outbound_ms += elapsed_ms


# Where the start time is stashed between the request and response hooks. An
# attribute on the Request object rather than a ContextVar: with HTTP keep-alive
# and connection pooling several calls can be in flight, and a single shared
# "started" value would attribute the wrong span to each of them.
_STARTED_ATTR = "_labs_telemetry_started"

# When the response hook fired, i.e. when headers were in. Stashed on the RESPONSE
# so ``send`` can bill the body download that happens after the hook (#1386).
_HEADERS_ATTR = "_labs_telemetry_headers_done"


def _on_request(request) -> None:
    try:
        setattr(request, _STARTED_ATTR, time.perf_counter())
    except Exception:  # telemetry must never break the call it is measuring
        pass


def _on_response(response) -> None:
    try:
        now = time.perf_counter()
        started = getattr(response.request, _STARTED_ATTR, None)
        # isinstance, not truthiness: a client whose request hook never ran (or a
        # test double) yields a non-numeric value that would otherwise poison the
        # arithmetic and cost the call its COUNT as well as its timing.
        elapsed_ms = (now - started) * 1000 if isinstance(started, float) else 0.0
        record_outbound_call(response.request.url.host, elapsed_ms)
        setattr(response, _HEADERS_ATTR, now)
    except Exception:  # telemetry must never break the call it is measuring
        pass


def httpx_event_hooks() -> dict[str, list]:
    """Event hooks to pass to ``httpx.Client(event_hooks=...)``.

    Opting in explicitly is no longer required — see ``install_httpx_instrumentation``
    below, which puts these same hooks on every client. This is kept because it is
    harmless (the hooks are de-duplicated by identity) and it states the intent at
    the call site.

    These hooks time to response HEADERS, because that is where the response hook
    fires. The body download is billed separately by the ``send`` wrapper that
    ``install_httpx_instrumentation`` also installs, so ``outbound_ms`` covers the
    whole call — headers AND body — for non-streaming requests.

    This used to say headers-only timing was "the number you want anyway", on the
    grounds that the calls that matter are a slow upstream sitting on a query. That
    held until an endpoint whose entire job was moving bytes: the audit image proxy
    billed every JPEG to ``self_ms`` and looked like our CPU (#1386).
    """
    return {"request": [_on_request], "response": [_on_response]}


# Making the three-way split honest
# ---------------------------------
# ``self_ms`` is a RESIDUAL: duration minus what we managed to measure. It only
# means "our own CPU" if everything else IS measured. Until #1298 the hooks above
# were opt-in and exactly ONE client opted in (``LabsRecordAPIClient``), so the four
# outbound calls ``/labs/callback/`` makes were invisible: it logged 4-16.5s with
# ``outbound_calls: 0`` and ``self_ms`` at ~99% of duration, and the investigation
# went looking for a hot loop that does not exist. 90% of that time was one call to
# production Connect.
#
# So instrumentation is default-on rather than opt-in: the hooks are patched onto
# httpx's own client constructors, which is the one place every outbound call passes
# through -- including ``httpx.get`` / ``httpx.post``, which build a throwaway
# ``Client`` per call and can never be handed hooks by their caller. A client that
# passes its own ``event_hooks`` keeps them.


async def _on_request_async(request) -> None:
    """``AsyncClient`` awaits its hooks, so the async path needs coroutines."""
    _on_request(request)


async def _on_response_async(response) -> None:
    _on_response(response)


def _merged_event_hooks(event_hooks, on_request, on_response) -> dict[str, list]:
    """The caller's hooks plus ours, with ours never added twice."""
    merged = {
        "request": list((event_hooks or {}).get("request", [])),
        "response": list((event_hooks or {}).get("response", [])),
    }
    # Ours runs LAST on the way out and FIRST on the way back, so the span we time is
    # the wait on the wire rather than the wire plus whatever the caller's own hooks
    # do. Identity checks, not equality: a client that already passed
    # httpx_event_hooks() by hand must not be counted twice.
    if on_request not in merged["request"]:
        merged["request"].append(on_request)
    if on_response not in merged["response"]:
        merged["response"].insert(0, on_response)
    return merged


_PATCHED_MARK = "_labs_telemetry_patched"


def _patch_client_init(client_cls, on_request, on_response) -> None:
    original = client_cls.__init__
    if getattr(original, _PATCHED_MARK, False):
        return

    # event_hooks is keyword-only on httpx's clients, so it can always be intercepted
    # here without having to know the rest of the signature.
    @functools.wraps(original)
    def __init__(self, *args, event_hooks=None, **kwargs):
        return original(self, *args, event_hooks=_merged_event_hooks(event_hooks, on_request, on_response), **kwargs)

    setattr(__init__, _PATCHED_MARK, True)
    client_cls.__init__ = __init__


def _body_ms(response) -> float:
    """Milliseconds spent reading the body, i.e. everything after the response hook.

    Returns 0.0 rather than guessing when the hook never ran (a test double, or a
    client constructed before instrumentation was installed) — an unmeasured span
    is better billed to nobody than to the wrong bucket.
    """
    headers_done = getattr(response, _HEADERS_ATTR, None)
    if not isinstance(headers_done, float):
        return 0.0
    return (time.perf_counter() - headers_done) * 1000


def _patch_client_send(client_cls) -> None:
    """Bill the response-body download to ``outbound_ms`` instead of ``self_ms``.

    httpx fires its ``response`` event hook when HEADERS arrive; the body is read
    afterwards, inside ``send`` itself::

        response = self._send_handling_auth(...)
        if not stream:
            response.read()        # <-- the transfer, after the hook has fired

    ``self_ms`` is a residual (``duration - outbound_ms - db_ms``), so anything the
    hook cannot see is relabelled as our own CPU. For JSON that is noise; for
    ``ExperimentAuditImageConnectView``, which proxies whole JPEGs, it was the
    entire cost — 11,482 s of "CPU" over 7 days on a view that does no image work,
    ranking it the top CPU consumer on the web tier ahead of ``bulk-data`` (#1386).
    Same class of error as #1298, one level down: that was about WHICH calls are
    measured, this is about HOW MUCH of each call.

    ``stream=True`` genuinely returns at headers — the caller reads the body later,
    on its own time — so it is left alone rather than billed a span it did not spend
    here.
    """
    original = client_cls.send
    if getattr(original, _PATCHED_MARK, False):
        return

    @functools.wraps(original)
    def send(self, request, *args, stream: bool = False, **kwargs):
        response = original(self, request, *args, stream=stream, **kwargs)
        if not stream:
            try:
                add_outbound_ms(_body_ms(response))
            except Exception:  # telemetry must never break the call it is measuring
                pass
        return response

    setattr(send, _PATCHED_MARK, True)
    client_cls.send = send


def _patch_async_client_send(client_cls) -> None:
    """Async twin of :func:`_patch_client_send` — ``await response.aread()``."""
    original = client_cls.send
    if getattr(original, _PATCHED_MARK, False):
        return

    @functools.wraps(original)
    async def send(self, request, *args, stream: bool = False, **kwargs):
        response = await original(self, request, *args, stream=stream, **kwargs)
        if not stream:
            try:
                add_outbound_ms(_body_ms(response))
            except Exception:  # telemetry must never break the call it is measuring
                pass
        return response

    setattr(send, _PATCHED_MARK, True)
    client_cls.send = send


def install_httpx_instrumentation() -> None:
    """Count every httpx call against the in-flight request, not just opted-in ones.

    Idempotent, and safe outside a request: ``record_outbound_call`` no-ops when no
    request is in flight, so Celery, management commands and tests are unaffected.
    """
    try:
        import httpx

        _patch_client_init(httpx.Client, _on_request, _on_response)
        _patch_client_init(httpx.AsyncClient, _on_request_async, _on_response_async)
        # Separate from the __init__ patch: the body read happens inside ``send``,
        # which no event hook can observe. See _patch_client_send (#1386).
        _patch_client_send(httpx.Client)
        _patch_async_client_send(httpx.AsyncClient)
    except Exception:  # telemetry must never break the calls it is measuring
        logger.debug("httpx instrumentation not installed", exc_info=True)


def _should_sample(request) -> bool:
    """Draw for the unbiased sample, independent of anything the request does.

    Runs before the view, on the request path, so it must not be able to raise:
    a telemetry decision is never allowed to break the request it is measuring.
    """
    try:
        if SAMPLE_RATE <= 0:
            return False
        if SAMPLE_PATH_PREFIX and not request.path.startswith(SAMPLE_PATH_PREFIX):
            return False
        # >= 1.0 short-circuits to a census rather than 1_000_000 coin flips a day.
        return SAMPLE_RATE >= 1.0 or random.random() < SAMPLE_RATE
    except Exception:
        logger.debug("telemetry sample decision failed", exc_info=True)
        return False


class RequestTelemetryMiddleware:
    """Log a structured line for any request that looks expensive.

    Sync-only on purpose. Django adapts it into the same thread-sensitive
    executor the view runs in, which is what keeps ``execute_wrapper`` counting
    the view's own queries rather than a different thread's.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Here rather than at import: this runs once per worker process, before any
        # request is served, and only where the counters have a request to attach to.
        install_httpx_instrumentation()

    def __call__(self, request):
        stats = RequestStats()
        token = _stats.set(stats)
        # Drawn BEFORE the request runs. Whether a line exists must not depend on
        # anything the request does, or the sample stops being a fair draw — which
        # is the whole defect it exists to correct.
        sampled = _should_sample(request)
        started = time.perf_counter()
        # Per-THREAD CPU, not per-process: with WEB_CONCURRENCY workers each running
        # an asgiref thread pool, process CPU would include whatever the other
        # in-flight requests in this worker burned, and attribute it to this one.
        thread_started = time.thread_time()

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
            cpu_ms = int((time.thread_time() - thread_started) * 1000)
            try:
                self._maybe_log(request, stats, duration_ms, cpu_ms, sampled)
            except Exception:
                logger.debug("request telemetry failed", exc_info=True)
            _stats.reset(token)

        return response

    def _maybe_log(
        self,
        request,
        stats: RequestStats,
        duration_ms: int,
        cpu_ms: int = 0,
        sampled: bool = False,
    ) -> None:
        reasons = []
        if duration_ms >= SLOW_REQUEST_MS:
            reasons.append("slow")
        if stats.outbound_calls >= OUTBOUND_CALL_LIMIT:
            reasons.append("outbound_fanout")
        if stats.db_queries >= DB_QUERY_LIMIT:
            reasons.append("db_fanout")
        if sampled:
            reasons.append("sample")
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
            # CPU this request's thread actually burned. self_ms says how much time
            # is unexplained; cpu_ms says whether that time was us computing or us
            # not running at all. See the module docstring.
            "cpu_ms": cpu_ms,
            # The authoritative filter for the unbiased population. Do NOT select on
            # reason == "sample": a sampled request that is also slow reads
            # "slow,sample" and would be dropped, biasing the sample by query.
            "sampled": sampled,
            "username": getattr(user, "username", None) if user and user.is_authenticated else None,
        }
        # Never log the query string: labs URLs carry opportunity and record ids
        # and this stream is not the audit trail.
        logger.warning(json.dumps(payload, default=str))
