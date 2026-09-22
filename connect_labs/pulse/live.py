"""Cheap answers for the live views: is there anything new, and is ingest healthy.

Every open night map asks "anything since visit N?" every few seconds, and
nearly every answer is "no". Answering that used to cost eight queries per
screen per poll, six of them identical for every viewer -- the test-opportunity
list, the ingest health rows, the poller account, the scope scalars. So the
two shared facts are held in the cache here, where one computation serves
every screen:

* the HEAD -- the newest visit id Pulse holds. A poll whose cursor is already
  at the head has nothing new in any scope and can answer without touching
  the event table at all.
* the INGEST STATE -- the LIVE/stale verdict and its explanation.

Both are dropped the moment ingest changes them (``events_arrived`` /
``ingest_changed``, called from the worker), so the cache never delays a new
service reaching the screen. The TTL is only the backstop for what changes
with no write at all: a stream going stale because time passed.

The cache is Redis in every deployed environment, shared by the web tier and
the Celery worker, which is what makes invalidating from ingest reach the
views. Tests set ``PULSE_LIVE_CACHE_SECONDS = 0``, which turns the cache off.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models import Max

logger = logging.getLogger(__name__)

HEAD_KEY = "pulse:live:head:v1"
INGEST_KEY = "pulse:live:ingest:v1"


def _ttl() -> int:
    return getattr(settings, "PULSE_LIVE_CACHE_SECONDS", 15)


def cached(key: str, build):
    """``build()``'s answer, shared across requests for the live TTL."""
    ttl = _ttl()
    if not ttl:
        return build()
    hit = cache.get(key)
    if hit is None:
        hit = build()
        cache.set(key, hit, ttl)
    return hit


def head() -> int:
    """The newest visit id Pulse holds, or 0 when it holds none."""
    from connect_labs.pulse.models import PulseEvent

    return cached(HEAD_KEY, lambda: PulseEvent.objects.aggregate(m=Max("connect_visit_id"))["m"] or 0)


def _forget(key: str) -> None:
    # Called from inside ingest. A cache outage must cost freshness, never an
    # ingest run: the TTL recovers the first, nothing recovers the second.
    try:
        cache.delete(key)
    except Exception:  # noqa: BLE001
        logger.warning("[pulse] could not drop %s from the cache", key, exc_info=True)


def events_arrived() -> None:
    """New visits were stored: the head has moved."""
    _forget(HEAD_KEY)


def ingest_changed() -> None:
    """An ingest tier succeeded or failed: the LIVE verdict may have changed."""
    _forget(INGEST_KEY)
