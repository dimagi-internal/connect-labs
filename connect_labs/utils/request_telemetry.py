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

#1435 is the same error again, and it is not about an HTTP call at all: a
deliberate ``time.sleep`` between retries. ``download_image_from_connect`` backs
off up to 0.9 s per request, on the request thread. That wait is not outbound,
not db and not CPU, so every millisecond of it landed in the residual and read as
a hot loop. ``retry_wait_ms`` bills it to its own bucket (see
``record_retry_wait``), and ``retry_waits`` counts the waits — which is the number
that says whether a slow tail is made of *retries* (an upstream reliability
problem) or of something still unnamed. On a path that sleeps once between
attempts, ``attempts == retry_waits + 1``.

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

Memory, not just time
---------------------
Two CPU pins in a row (2026-09-08, #1575) ended in a gunicorn ``SIGKILL ... out of
memory?`` with nothing in this stream to say which request held the memory, and the
2026-09-09 follow-up named the one measurement that splits the remaining causes:
**peak RSS per heavy call, not elapsed time**. Every logged line therefore carries

* ``rss_kb`` / ``rss_delta_kb`` -- the worker's resident set at the end of the
  request, and how much it grew across it (``/proc/self/statm``; Python rarely hands
  memory back, so growth is a fair proxy for the working set the request built);
* ``maxrss_kb`` / ``maxrss_rise_kb`` -- the process high-water mark
  (``getrusage``), and how far THIS request pushed it. Non-zero only for a request
  that set a new peak, which is exactly the request an OOM kill is about.

Both are per PROCESS: a worker serving concurrent requests attributes the growth to
whichever finished, so read them across many lines, not one.

Streams are measured to the end of the body, not the headers
------------------------------------------------------------
A ``StreamingHttpResponse`` returns to this middleware at HEADERS; the work -- every
pipeline read behind ``/labs/workflow/api/<id>/pipeline-data/stream/`` -- happens
afterwards, while the body is iterated. So until #1575's follow-up that route read
64 ms median in ``Labs/Requests`` while it pinned a core for 15 minutes, and a
worker OOM-killed mid-stream left no line at all. The body is now wrapped
(``_wrap_stream``): a ``stream_end`` line (``event = "stream_end"``) carries the
real duration, bytes, chunks, the sampled PEAK RSS during the stream and its
outcome (``complete`` / ``closed`` / ``error``), and ``Labs/Streams`` counts
``StreamStarted`` at headers and ``StreamFinished`` at the end -- so a stream whose
worker was killed shows up as ``StreamStarted - StreamFinished > 0``.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

from django.conf import settings
from django.db import connection

logger = logging.getLogger("connect_labs.telemetry.request")
# One EMF line per request (see "Every request, as a metric" above). A separate
# logger so it can be routed, levelled and kill-switched apart from the diagnostic
# stream, and so the diagnostic stream's tests and queries never see it.
metrics_logger = logging.getLogger("connect_labs.telemetry.metrics")
# The newer EMF lines (heavy-request memory, streams) on a CHILD of it: same handler
# in production (propagation), but a distinct logger name, so the per-request
# Labs/Requests line keeps its one-line-per-request contract in tests and queries.
extra_metrics_logger = logging.getLogger("connect_labs.telemetry.metrics.extra")

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
# Scoping: ``TELEMETRY_SAMPLE_PATH_PREFIX`` is a comma-separated list (empty = every
# path) and ``TELEMETRY_SAMPLE_EXCLUDE_PREFIX`` drops paths that are pure volume. It
# shipped scoped to ``/audit/image/`` alone, which sampled NOTHING across 62 alarm
# hours because the heavy routes were elsewhere (2026-09-02). Since 2026-10-29 it
# samples every path except health checks (~75% of requests) and requests that
# never resolved to a route (scanners; dropped at log time, which depends only on
# the URL, never on the outcome): ~12k lines/day at 2026-10-29 traffic -- see
# deploy/task-definitions/README.md.
SAMPLE_RATE = float(getattr(settings, "TELEMETRY_SAMPLE_RATE", 0.0))


def _prefixes(value) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value or "").split(",")
    return tuple(p.strip() for p in items if p and p.strip())


SAMPLE_PATH_PREFIXES = _prefixes(getattr(settings, "TELEMETRY_SAMPLE_PATH_PREFIX", ""))
SAMPLE_EXCLUDE_PREFIXES = _prefixes(getattr(settings, "TELEMETRY_SAMPLE_EXCLUDE_PREFIX", ""))

# The per-request route metric. On by default: it is the only request-level signal
# the read-only incident role can see. False is a kill switch, not a tuning knob.
ROUTE_METRICS_ENABLED = getattr(settings, "TELEMETRY_ROUTE_METRICS", True)

METRICS_NAMESPACE = "Labs/Requests"
# The ONLY dimensions, in this order. Adding one multiplies the number of billed
# CloudWatch metrics AND puts its value into every log line; the test pins it.
METRIC_DIMENSIONS = ("route", "method", "status_class")
# A request that never resolved to a URL pattern (404s, scanners, anything that
# failed before URL resolution). Collapsed to one value so a scanner walking random
# paths cannot mint a metric per path — and so a raw path never becomes a dimension.
UNMATCHED_ROUTE = "unmatched"
# The method is client-controlled (any token is a legal HTTP method), so it is
# bounded too: anything outside the standard set is one bucket.
_KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
# Route templates come from our own urlconf, so they are bounded by construction;
# the cap only guards against a pathological regex pattern.
_MAX_ROUTE_LEN = 200


@dataclass
class RequestStats:
    """Counters for the request currently being served on this context."""

    outbound_calls: int = 0
    outbound_by_host: Counter = field(default_factory=Counter)
    outbound_ms: float = 0.0
    db_queries: int = 0
    db_ms: float = 0.0
    # Deliberate waits we chose to take (retry backoff), billed away from the
    # residual so a sleep does not read as our CPU. See ``record_retry_wait``.
    retry_waits: int = 0
    retry_wait_ms: float = 0.0

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


def record_retry_wait(elapsed_ms: float) -> None:
    """Bill a deliberate between-retry sleep to its own bucket. No-op outside a request.

    A backoff sleep is not outbound (nothing is on the wire), not db, and not CPU
    (the thread is parked). ``self_ms`` is a residual, so without this the whole
    wait is indistinguishable from a hot loop — which is #1435, and the same class
    as #1298 and #1386 before it: a wait we chose to take, that nothing counted.

    Counting the waits as well as timing them is the load-bearing half. It is what
    separates "the tail is retries, so this is an upstream reliability problem"
    from "the tail is something else" — two branches the residual cannot tell
    apart, because it absorbs both.
    """
    stats = _stats.get()
    if stats is None:
        return
    stats.retry_waits += 1
    stats.retry_wait_ms += elapsed_ms


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
        path = request.path
        if SAMPLE_PATH_PREFIXES and not path.startswith(SAMPLE_PATH_PREFIXES):
            return False
        if SAMPLE_EXCLUDE_PREFIXES and path.startswith(SAMPLE_EXCLUDE_PREFIXES):
            return False
        # >= 1.0 short-circuits to a census rather than 1_000_000 coin flips a day.
        return SAMPLE_RATE >= 1.0 or random.random() < SAMPLE_RATE
    except Exception:
        logger.debug("telemetry sample decision failed", exc_info=True)
        return False


def route_template(request) -> str:
    """The URL pattern the request matched, as a template — never the raw path.

    ``ResolverMatch.route`` is the joined pattern across ``include()``s, e.g.
    ``labs/workflow/<int:workflow_id>/``: it comes from the urlconf, so it carries
    no ids, usernames or query strings. Anything that did not resolve is
    ``UNMATCHED_ROUTE``. There is deliberately no fallback to ``request.path``.
    """
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", None) if match is not None else None
    if not isinstance(route, str):
        return UNMATCHED_ROUTE
    if not route:
        return "/"
    return route[:_MAX_ROUTE_LEN]


def status_class(status_code) -> str:
    if isinstance(status_code, int) and 100 <= status_code <= 599:
        return f"{status_code // 100}xx"
    return "unknown"


def _bounded_method(method) -> str:
    return method if method in _KNOWN_METHODS else "OTHER"


def route_metric_line(route: str, method: str, status: str, duration_ms: int) -> dict:
    """The EMF object for one request. Every top-level key lands in the log event,
    so this holds the two metric values, the three dimensions, and ``_aws`` — and
    nothing else."""
    return {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": METRICS_NAMESPACE,
                    "Dimensions": [list(METRIC_DIMENSIONS)],
                    "Metrics": [
                        {"Name": "RequestLatency", "Unit": "Milliseconds"},
                        {"Name": "RequestCount", "Unit": "Count"},
                    ],
                }
            ],
        },
        "route": route,
        "method": method,
        "status_class": status,
        "RequestLatency": duration_ms,
        "RequestCount": 1,
    }


def emit_route_metric(request, response, duration_ms: int) -> None:
    """Write one EMF line for this request. Called for EVERY request.

    ``response`` is None when the inner stack raised instead of returning (Django
    normally converts exceptions to a 500 response further in, so this is rare);
    it is counted as ``5xx`` rather than dropped, or the error rate would read low.
    The line is a bare JSON object on its own line — the logger's handler uses the
    message-only formatter — which is what CloudWatch needs to extract it.
    """
    if not ROUTE_METRICS_ENABLED:
        return
    status = "5xx" if response is None else status_class(getattr(response, "status_code", None))
    line = route_metric_line(route_template(request), _bounded_method(request.method), status, duration_ms)
    metrics_logger.info(json.dumps(line, separators=(",", ":")))


# Memory
# ------
# Per-PROCESS numbers (see "Memory, not just time" above). Both readers return None
# rather than guess where the source does not exist (macOS dev, a sandbox), and
# neither may raise: they run on every request.

try:
    _PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
except (ValueError, OSError, AttributeError):  # pragma: no cover - non-POSIX
    _PAGE_KB = 4


def current_rss_kb() -> int | None:
    """Resident set of this process, in KB, from ``/proc/self/statm`` (Linux only)."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * _PAGE_KB
    except Exception:
        return None


def peak_rss_kb() -> int | None:
    """This process's high-water resident set in KB (``ru_maxrss``)."""
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS bytes.
        return int(peak // 1024) if sys.platform == "darwin" else int(peak)
    except Exception:
        return None


@dataclass
class MemoryProbe:
    """RSS and high-water mark at the start of a span, and the change across it."""

    rss_start: int | None = field(default_factory=current_rss_kb)
    maxrss_start: int | None = field(default_factory=peak_rss_kb)

    def fields(self, rss_peak: int | None | object = ...) -> dict:
        rss_now, maxrss_now = current_rss_kb(), peak_rss_kb()
        out = {"rss_kb": rss_now, "maxrss_kb": maxrss_now}
        out["rss_delta_kb"] = rss_now - self.rss_start if None not in (rss_now, self.rss_start) else None
        out["maxrss_rise_kb"] = maxrss_now - self.maxrss_start if None not in (maxrss_now, self.maxrss_start) else None
        if rss_peak is not ...:
            out["rss_peak_kb"] = rss_peak
            out["rss_peak_rise_kb"] = rss_peak - self.rss_start if None not in (rss_peak, self.rss_start) else None
        return out


def _emf(namespace: str, dimensions: dict, metrics: dict, units: dict) -> None:
    """One EMF line. ``dimensions`` must be bounded values (route templates, never ids)."""
    line = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [list(dimensions)],
                    "Metrics": [{"Name": k, "Unit": units.get(k, "None")} for k in metrics],
                }
            ],
        },
        **dimensions,
        **metrics,
    }
    extra_metrics_logger.info(json.dumps(line, separators=(",", ":")))


# Heavy requests' memory, as a metric the read-only observe role can see. Only for
# lines that tripped a THRESHOLD (slow / fan-out), not for every request: one
# dimension (route), and the slow routes are a short list, so this is a handful of
# billed metrics rather than another copy of Labs/Requests.
HEAVY_MEMORY_METRICS = ("HeavyRequestRssDeltaKb", "HeavyRequestMaxRssRiseKb")


def emit_heavy_memory_metric(route: str, mem: dict) -> None:
    if not ROUTE_METRICS_ENABLED:
        return
    values = {
        "HeavyRequestRssDeltaKb": mem.get("rss_delta_kb"),
        "HeavyRequestMaxRssRiseKb": mem.get("maxrss_rise_kb"),
    }
    values = {k: v for k, v in values.items() if isinstance(v, int)}
    if values:
        _emf(METRICS_NAMESPACE, {"route": route}, values, {k: "Kilobytes" for k in values})


# Streams
# -------
STREAMS_NAMESPACE = "Labs/Streams"
# Sample RSS at most this often while a body streams: a FileResponse can yield
# thousands of 8 KB blocks, and one /proc read per block is pointless.
_STREAM_RSS_EVERY_S = 0.5
# A stream_end line is written when the stream was slow, sampled, or grew the
# worker by at least this much -- the #1575 falsifier is "a multi-hundred-MB
# working set", so 50 MB is well under the signal and well over noise.
STREAM_RSS_LOG_KB = getattr(settings, "TELEMETRY_STREAM_RSS_LOG_KB", 50 * 1024)


class _StreamTracker:
    def __init__(self, request, response, started: float, sampled: bool):
        self.route = route_template(request)
        self.method = _bounded_method(request.method)
        self.status = getattr(response, "status_code", None)
        self.started = started
        self.sampled = sampled
        self.mem = MemoryProbe()
        self.rss_peak = self.mem.rss_start
        self._last_rss_at = time.monotonic()
        self.chunks = 0
        self.bytes = 0
        self.outcome = "complete"
        self._done = False

    def chunk(self, part) -> None:
        self.chunks += 1
        try:
            self.bytes += len(part)
        except TypeError:
            pass
        now = time.monotonic()
        if now - self._last_rss_at >= _STREAM_RSS_EVERY_S:
            self._last_rss_at = now
            rss = current_rss_kb()
            if rss is not None and (self.rss_peak is None or rss > self.rss_peak):
                self.rss_peak = rss

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            rss = current_rss_kb()
            if rss is not None and (self.rss_peak is None or rss > self.rss_peak):
                self.rss_peak = rss
            duration_ms = int((time.perf_counter() - self.started) * 1000)
            mem = self.mem.fields(rss_peak=self.rss_peak)
            if ROUTE_METRICS_ENABLED:
                values = {"StreamFinished": 1, "StreamDuration": duration_ms}
                units = {"StreamFinished": "Count", "StreamDuration": "Milliseconds"}
                for key, name in (
                    ("rss_peak_rise_kb", "StreamRssPeakRiseKb"),
                    ("maxrss_rise_kb", "StreamMaxRssRiseKb"),
                ):
                    if isinstance(mem.get(key), int):
                        values[name] = mem[key]
                        units[name] = "Kilobytes"
                _emf(STREAMS_NAMESPACE, {"route": self.route}, values, units)
            grew = mem.get("rss_peak_rise_kb") or 0
            if (
                duration_ms >= SLOW_REQUEST_MS
                or self.sampled
                or grew >= STREAM_RSS_LOG_KB
                or self.outcome != "complete"
            ):
                logger.warning(
                    json.dumps(
                        {
                            "event": "stream_end",
                            "route": self.route,
                            "method": self.method,
                            "status": self.status,
                            "outcome": self.outcome,
                            "duration_ms": duration_ms,
                            "chunks": self.chunks,
                            "bytes": self.bytes,
                            "sampled": self.sampled,
                            **mem,
                        },
                        default=str,
                    )
                )
        except Exception:
            logger.debug("stream telemetry failed", exc_info=True)


def _wrap_stream(response, tracker: _StreamTracker) -> None:
    """Re-point the response body through ``tracker``, keeping it sync or async as it was.

    Django's documented pattern for middleware that touches a streaming body. The
    wrapper yields every part unchanged and in order; it only counts and times.
    """
    original = response.streaming_content

    if response.is_async:

        async def body():
            try:
                async for part in original:
                    tracker.chunk(part)
                    yield part
            except BaseException as exc:
                tracker.outcome = "error" if isinstance(exc, Exception) else "closed"
                raise
            finally:
                tracker.finish()

    else:

        def body():
            try:
                for part in original:
                    tracker.chunk(part)
                    yield part
            except BaseException as exc:
                tracker.outcome = "error" if isinstance(exc, Exception) else "closed"
                raise
            finally:
                tracker.finish()

    response.streaming_content = body()


def _track_stream(request, response, started: float, sampled: bool) -> None:
    if response is None or not getattr(response, "streaming", False):
        return
    tracker = _StreamTracker(request, response, started, sampled)
    if ROUTE_METRICS_ENABLED:
        _emf(STREAMS_NAMESPACE, {"route": tracker.route}, {"StreamStarted": 1}, {"StreamStarted": "Count"})
    _wrap_stream(response, tracker)


class StreamTrace:
    """Structured progress lines for one long in-request job (``event = "pipeline_stream"``).

    The pipeline SSE view fans across opportunities inside one request; when its
    worker is OOM-killed nothing completes, so a completion-only line can never
    describe it (#1575). This writes ``phase: start`` up front, ``phase: step`` after
    every read (rows, cache hit, RSS so far) and ``phase: end`` in a ``finally`` --
    so a killed stream is the one with a ``start`` and no ``end``, and its last
    ``step`` says how big it had got. Counts, config ids and memory only: never
    opportunity ids, usernames or row content.
    """

    def __init__(self, kind: str, **ids):
        self.stream_id = os.urandom(6).hex()
        self.kind = kind
        self.ids = {k: v for k, v in ids.items() if isinstance(v, int)}
        self.started = time.perf_counter()
        self.mem = MemoryProbe()
        self.rss_peak = self.mem.rss_start
        self.steps = 0
        self.rows = 0
        self.outcome = "complete"
        self._emit("start", {"pid": os.getpid()})

    def _emit(self, phase: str, extra: dict) -> None:
        try:
            rss = current_rss_kb()
            if rss is not None and (self.rss_peak is None or rss > self.rss_peak):
                self.rss_peak = rss
            logger.warning(
                json.dumps(
                    {
                        "event": self.kind,
                        "phase": phase,
                        "stream_id": self.stream_id,
                        **self.ids,
                        "elapsed_ms": int((time.perf_counter() - self.started) * 1000),
                        "total_rows": self.rows,
                        **extra,
                        **self.mem.fields(rss_peak=self.rss_peak),
                    },
                    default=str,
                )
            )
        except Exception:
            logger.debug("stream trace failed", exc_info=True)

    def step(self, *, rows: int = 0, **fields) -> None:
        self.steps += 1
        self.rows += rows if isinstance(rows, int) else 0
        clean = {k: v for k, v in fields.items() if isinstance(v, (int, bool, float))}
        self._emit("step", {"step": self.steps, "rows": rows, **clean})

    def end(self) -> None:
        self._emit("end", {"steps": self.steps, "outcome": self.outcome})


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
        mem = MemoryProbe()

        def count_queries(execute, sql, params, many, context):
            stats.db_queries += 1
            q_started = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                stats.db_ms += (time.perf_counter() - q_started) * 1000

        response = None
        try:
            # execute_wrapper is the supported hook and, unlike connection.queries,
            # works with DEBUG=False — which is the only mode that matters here.
            with connection.execute_wrapper(count_queries):
                response = self.get_response(request)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            cpu_ms = int((time.thread_time() - thread_started) * 1000)
            try:
                self._maybe_log(request, stats, duration_ms, cpu_ms, sampled, response=response, mem=mem)
            except Exception:
                logger.debug("request telemetry failed", exc_info=True)
            # A streaming body has not run yet: measure it to the end (#1575).
            try:
                _track_stream(request, response, started, sampled)
            except Exception:
                logger.debug("stream telemetry failed", exc_info=True)
            try:
                from connect_labs.utils import requester_concentration

                requester_concentration.observe(request, route_template(request))
            except Exception:
                logger.debug("requester concentration failed", exc_info=True)
            # Unconditional, unlike _maybe_log: a count that only includes the
            # requests that breached a threshold is not a count.
            try:
                emit_route_metric(request, response, duration_ms)
            except Exception:
                logger.debug("route metric failed", exc_info=True)
            _stats.reset(token)

        return response

    def _maybe_log(
        self,
        request,
        stats: RequestStats,
        duration_ms: int,
        cpu_ms: int = 0,
        sampled: bool = False,
        response=None,
        mem: MemoryProbe | None = None,
    ) -> None:
        if sampled and getattr(request, "resolver_match", None) is None:
            # Scanners walking random paths are not a population worth sampling.
            sampled = False
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

        mem_fields = mem.fields() if mem is not None else {}
        if reasons != ["sample"]:
            emit_heavy_memory_metric(route_template(request), mem_fields)
        user = getattr(request, "user", None)
        payload = {
            "event": "slow_request",
            "reason": ",".join(reasons),
            "method": request.method,
            "path": request.path,
            # The route TEMPLATE and the status: the two fields the scrubbed copy
            # in /labs-jj/observe/request-telemetry keys on (infra/observe_pipeline/
            # request_telemetry_scrubber.py drops `path` and `username`, and drops
            # any line without `route` rather than fall back to the path).
            "route": route_template(request),
            "status": getattr(response, "status_code", None) if response is not None else 500,
            "duration_ms": duration_ms,
            "outbound_calls": stats.outbound_calls,
            "outbound_ms": int(stats.outbound_ms),
            "outbound_by_host": stats.top_outbound(),
            "db_queries": stats.db_queries,
            "db_ms": int(stats.db_ms),
            # Deliberate backoff sleeps on the request thread (#1435). Broken out
            # of the residual because a sleep is not our CPU, and counted because
            # retry_waits > 0 is what identifies the tail as upstream retries.
            "retry_waits": stats.retry_waits,
            "retry_wait_ms": int(stats.retry_wait_ms),
            # What is left after waiting on Postgres, on upstream HTTP, and on our
            # own backoff. A large remainder means the time is in our own Python,
            # and none of the other three numbers will lead you to it.
            "self_ms": max(
                0,
                duration_ms - int(stats.outbound_ms) - int(stats.db_ms) - int(stats.retry_wait_ms),
            ),
            # CPU this request's thread actually burned. self_ms says how much time
            # is unexplained; cpu_ms says whether that time was us computing or us
            # not running at all. See the module docstring.
            "cpu_ms": cpu_ms,
            # The authoritative filter for the unbiased population. Do NOT select on
            # reason == "sample": a sampled request that is also slow reads
            # "slow,sample" and would be dropped, biasing the sample by query.
            "sampled": sampled,
            # Worker memory across the request (see "Memory, not just time"):
            # rss_kb, rss_delta_kb, maxrss_kb, maxrss_rise_kb.
            **mem_fields,
            # Streaming responses return here at HEADERS; their real duration is
            # on the matching stream_end line.
            "streaming": bool(getattr(response, "streaming", False)),
            "username": getattr(user, "username", None) if user and user.is_authenticated else None,
        }
        # Never log the query string: labs URLs carry opportunity and record ids
        # and this stream is not the audit trail.
        logger.warning(json.dumps(payload, default=str))
