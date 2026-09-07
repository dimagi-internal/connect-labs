"""One rebuild at a time per (opportunity, pipeline) — the RawVisitCache stampede guard.

A raw-cache MISS paginates an opportunity's ENTIRE ``user_visits`` export inside the
request. That is expensive but survivable once. The failure mode is doing it N times
at once: the web tier runs uvicorn (ASGI) workers, which accept an unbounded number of
concurrent requests per process, so several readers can miss on the same opportunity
within seconds of each other and each start its own full walk. Peak memory is then
N x per-request against a 4096 MB task, and the kernel OOM-killer reaps gunicorn
workers — taking their in-flight requests with them.

Measured 2026-09-07 (#1361): around two worker kills, 53 ``page_size=2500`` fetches
over 16 distinct cursors, every cursor fetched 2-6 times, across both web tasks —
i.e. several workers each walking the same ~40k-visit rebuild concurrently. 32 kills
that day against a baseline of nine.

**What this does NOT fix.** Only the concurrency multiplier. The same opportunity
still misses again minutes later, because validity is a visit-count-with-tolerance
test and a busy opportunity drifts past tolerance continuously — 200-390 full
repaginations a day. That is the other half of #1361 and wants a delta fetch, not a
lock.

Why an advisory lock rather than a row or a flag: it needs no schema, it is scoped to
the transaction-independent *session*, and PostgreSQL releases it automatically if the
process dies — which is the case that matters most here, since the process dying is
the very thing we are guarding against. A lock held by an OOM-killed worker would
otherwise wedge that opportunity until someone noticed.

**Losers do not wait.** They are served the existing (TTL-expired but present) rows,
the same way the shrink-anomaly guard in ``backend.py`` already keeps and serves an old
cache. Waiting was the obvious design and is the wrong one here: ``ATOMIC_REQUESTS =
True`` pins each request's connection inside an open transaction, so N waiting readers
are N pinned Postgres connections — exactly the shape that exhausted the instance's
slots on 2026-07-30 (see ``audit.views.AuditImageView``'s docstring). Serving stale
returns the connection sooner than either waiting or rebuilding, and it is also, for
the reader, far faster than the 10-20s walk it replaces.

**The release is explicit, and the lock is deliberately NOT held across the commit.**
Under ``ATOMIC_REQUESTS`` the leader's rows are invisible to anyone else until its whole
request commits, which happens after this code has returned. So a loser that acquires
the lock the instant the leader releases it may still see no fresh cache. That is
harmless *because* losers serve stale rather than wait: the worst case at that boundary
is one extra rebuild, not a wrong answer. A design where losers waited for the data
would have to hold the lock past the commit, which cannot be done from inside the
request and must not be faked by leaking the lock to connection teardown — that breaks
outside a web request, where Celery's connections are long-lived and a leaked lock
would wedge the opportunity for the life of the worker.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager

from django.db import DEFAULT_DB_ALIAS, connections

logger = logging.getLogger(__name__)

# Namespaces the key space so these locks cannot collide with any other advisory
# lock taken elsewhere in the app or by a future caller. Changing it makes old
# and new code fail to exclude each other, so treat it as a constant.
_LOCK_NAMESPACE = "connect_labs.raw_visit_cache.rebuild"

_SIGNED_64_OFFSET = 1 << 63


def raw_rebuild_lock_key(opportunity_id: int, pipeline_id: int | None) -> int:
    """Stable signed-64-bit advisory-lock key for one (opportunity, pipeline) slot.

    Mirrors ``SQLCacheManager._raw_filter()``, which scopes the raw cache by exactly
    this pair — the lock must partition the same way the cache does, or two pipelines
    on one opportunity would serialise against each other for no reason.

    ``pipeline_id`` is legitimately ``None`` for callers that know only the data source
    (see ``SQLCacheManager.__init__``); those share a single None-tagged slot, and so
    share a lock, which is correct.
    """
    raw = f"{_LOCK_NAMESPACE}:{opportunity_id}:{pipeline_id}".encode()
    unsigned = int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big")
    return unsigned - _SIGNED_64_OFFSET


@contextmanager
def claim_raw_rebuild(opportunity_id: int, pipeline_id: int | None, *, using: str = DEFAULT_DB_ALIAS):
    """Yield True if this caller owns the rebuild for this slot, False if someone else does.

    Never blocks. A False means "another connection is rebuilding right now" and the
    caller should serve what it already has rather than start a second walk.

    Fails OPEN: if the lock cannot be taken for any unexpected reason, this yields True
    and the caller behaves exactly as it did before this guard existed. A stampede is a
    performance failure; refusing to serve would be a correctness one.
    """
    key = raw_rebuild_lock_key(opportunity_id, pipeline_id)
    acquired = False
    try:
        with connections[using].cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
            acquired = bool(cursor.fetchone()[0])
    except Exception:
        logger.exception(
            "[SingleFlight] could not take the rebuild lock for opp %s pipeline %s — " "proceeding unguarded",
            opportunity_id,
            pipeline_id,
        )
        yield True
        return

    try:
        yield acquired
    finally:
        if acquired:
            try:
                with connections[using].cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
            except Exception:
                # The connection is already gone, which released it anyway.
                logger.warning(
                    "[SingleFlight] could not release the rebuild lock for opp %s pipeline %s",
                    opportunity_id,
                    pipeline_id,
                )
