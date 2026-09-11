"""Cached projections of a definition's saved-run history.

The trend on a periodic dashboard is one point per saved run, served by
`run_history_api`. To answer it the server fetches EVERY run of the definition
from the records API -- each carrying its whole snapshot -- and then keeps a few
hundred bytes of each. A KMC snapshot is ~4.5 MB, so a report with 21 saved runs
decodes ~95 MB to read 21 numbers, on every open, for every viewer: measured
2026-09-11, 6-10 seconds of pure CPU per call on a 1-vCPU web task, which is what
everything else on that task then queues behind.

The projection only changes when a run is completed or deleted, so it is cached
and those two writes invalidate it. Invalidation is a VERSION counter rather than
a delete: the entries are keyed by (definition, version, scope, requested paths),
so a bump orphans every variant at once and no reader can race a stale key back
into place. A cache outage degrades to today's behaviour -- django_redis is
configured with IGNORE_EXCEPTIONS, so `get` returns None and the answer is
recomputed.
"""

from __future__ import annotations

import hashlib

from django.core.cache import cache

# Long enough that a page reload, a drill and a colleague's open all hit it;
# short enough that a run completed by another process without going through
# `complete_run` (a backfill writing records directly) is not stale for long.
TTL_SECONDS = 600


def _version_key(definition_id: int) -> str:
    return f"wf:history:ver:{int(definition_id)}"


def version(definition_id: int) -> int:
    return cache.get(_version_key(definition_id)) or 1


def invalidate(definition_id) -> None:
    """Called when a run is completed or deleted: the next read recomputes."""
    if definition_id in (None, ""):
        return
    key = _version_key(definition_id)
    try:
        cache.incr(key)
    except ValueError:
        # No counter yet (or it expired): start one ABOVE the default, so entries
        # written under the implicit version 1 are orphaned rather than reused.
        cache.set(key, version(definition_id) + 1, None)


def entry_key(definition_id: int, scope_key: str, keys: list[str]) -> str:
    paths = hashlib.sha1(",".join(keys).encode()).hexdigest()[:12]
    return f"wf:history:{int(definition_id)}:v{version(definition_id)}:{scope_key}:{paths}"


def get(definition_id: int, scope_key: str, keys: list[str]):
    return cache.get(entry_key(definition_id, scope_key, keys))


def store(definition_id: int, scope_key: str, keys: list[str], payload) -> None:
    cache.set(entry_key(definition_id, scope_key, keys), payload, TTL_SECONDS)
