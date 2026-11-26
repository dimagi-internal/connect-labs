"""Connect Web's one caching module: what labs reuses of the worker's Connect / HQ data, and for how long.

**Why.** Almost every page and form action needs the worker's opportunity list (to scope the
request to the worker's own opportunity), and Connect's list can take seconds. Without a cache,
one form page plus its first navigation fetched it twice, and every progress poll fetched the
list and both progress reads.

**Namespace: the pool worker.** The data is the worker's, not a checkout's, so the keys are
``cw:v1:w:<pool_worker_id>:<generation>:<name>``. The generation counter
``cw:v1:w:<pool_worker_id>:gen`` is part of every key: `bump` (one INCR) drops everything at once.
`forget` deletes named entries.

=========================== ======== ===========================================================
key                         TTL      dropped by
=========================== ======== ===========================================================
``opps``                    60 s     start_learning, claim, confirm_payments, a submitted form
``learn:<opp>``             30 s     start_learning, claim, a submitted learn form
``deliv:<opp>``             30 s     claim, confirm_payments, a submitted deliver form
``target:<opp>:<kind>``     10 min   claim, an engine error, no released build
``build:<host>:<domain>:…`` 5 min    an engine error (shared by every worker: no worker data)
  … no released build       30 s     (negative entry, the only cached error)
``awaiting:<opp>``          5 min    set when a form is submitted: progress reads bypass the
                                     cache while it is set (Connect hears about the form late)
``synced:<domain>``         1 day    when labs last asked Formplayer to sync (restore) the
                                     worker in that domain — see `formplayer.ensure_fresh`
=========================== ======== ===========================================================

**Per request**, values are also memoised on the `WorkerSession` object a view loaded, so one
request never fetches the same thing twice, and a short single-flight lock (``cache.add``, 15 s)
keeps the player and the progress poll from both fetching the list at once.

**Never cached:** the result of a POST, Formplayer responses or form state, tokens (they stay in
Postgres, encrypted — Redis is shared and unencrypted), and errors (except the 30-second "no
released build"). **A cache failure is never an error:** every read falls back to a direct
fetch, every write is best-effort (labs' Redis client already swallows connection errors with
``IGNORE_EXCEPTIONS``; this module also catches anything else a backend raises).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from django.core.cache import cache as django_cache

logger = logging.getLogger(__name__)

T = TypeVar("T")

PREFIX = "cw:v1"

OPPS_TTL = 60
LEARN_TTL = 30
DELIVERY_TTL = 30
TARGET_TTL = 10 * 60
BUILD_TTL = 5 * 60
NO_BUILD_TTL = 30
AWAITING_TTL = 5 * 60
SYNCED_TTL = 24 * 3600
#: The generation counter lives a long time; if it is ever evicted it restarts at "now".
GEN_TTL = 7 * 24 * 3600

SINGLE_FLIGHT_SECONDS = 15
#: How long a request waits for another's fetch of the same thing before fetching itself.
SINGLE_FLIGHT_WAIT = 12.0
_POLL = 0.1

_MISS = object()


# ---------------------------------------------------------------------------
# Best-effort primitives: a cache failure is a miss, never an error
# ---------------------------------------------------------------------------


def _get(key: str) -> Any:
    try:
        value = django_cache.get(key, _MISS)
    except Exception:  # noqa: BLE001 — a cache outage must degrade to a fetch
        logger.warning("connect_web cache: get failed", exc_info=True)
        return _MISS
    return _MISS if value is None else value


def _set(key: str, value: Any, ttl: int) -> None:
    try:
        django_cache.set(key, value, ttl)
    except Exception:  # noqa: BLE001
        logger.warning("connect_web cache: set failed", exc_info=True)


def _delete(*keys: str) -> None:
    try:
        django_cache.delete_many(list(keys))
    except Exception:  # noqa: BLE001
        logger.warning("connect_web cache: delete failed", exc_info=True)


def add(key: str, value: Any, ttl: int) -> bool | None:
    """``cache.add``: True if this call set it, False if it was there, None if the cache is down."""
    try:
        result = django_cache.add(key, value, ttl)
    except Exception:  # noqa: BLE001
        logger.warning("connect_web cache: add failed", exc_info=True)
        return None
    # django_redis answers None (not False) when it swallowed a connection error.
    return None if result is None else bool(result)


def delete(key: str) -> None:
    _delete(key)


def release(key: str, owner: str) -> None:
    """Delete a lock set with ``add(key, owner, …)`` — unless it expired and someone else holds it now."""
    if _get(key) == owner:
        _delete(key)


# ---------------------------------------------------------------------------
# The worker's namespace
# ---------------------------------------------------------------------------


def _worker_id(worker) -> int:
    """A pool worker id from a `PoolWorker`, a `WorkerSession` or an int."""
    if isinstance(worker, int):
        return worker
    return getattr(worker, "pool_worker_id", None) or worker.pk


def _gen_key(worker_id: int) -> str:
    return f"{PREFIX}:w:{worker_id}:gen"


def generation(worker) -> int | None:
    """The worker's current generation, or None when the cache can't be used (memoised per request)."""
    memo = _memo(worker)
    if "__gen" in memo:
        return memo["__gen"]
    gen_key = _gen_key(_worker_id(worker))
    value = _get(gen_key)
    if value is _MISS:
        # Start at "now" (ms) so a counter evicted and restarted never reuses an old generation.
        add(gen_key, int(time.time() * 1000), GEN_TTL)
        value = _get(gen_key)
    gen = value if isinstance(value, int) else None
    if gen is not None:
        memo["__gen"] = gen
    return gen


def key(worker, name: str) -> str | None:
    gen = generation(worker)
    return None if gen is None else f"{PREFIX}:w:{_worker_id(worker)}:{gen}:{name}"


def bump(worker) -> None:
    """Drop everything cached for the worker (one INCR)."""
    gen_key = _gen_key(_worker_id(worker))
    try:
        django_cache.incr(gen_key)
    except ValueError:  # no counter yet: nothing to drop
        pass
    except Exception:  # noqa: BLE001
        logger.warning("connect_web cache: bump failed", exc_info=True)
    _clear_memo(worker)


def forget(worker, *names: str) -> None:
    """Drop named entries for the worker (and from this request's memo)."""
    keys = [k for k in (key(worker, name) for name in names) if k]
    if keys:
        _delete(*keys)
    memo = _memo(worker)
    for name in names:
        memo.pop(name, None)
        memo.get("__fetched", set()).discard(name)


# ---------------------------------------------------------------------------
# Per-request memo: on the WorkerSession object the view loaded
# ---------------------------------------------------------------------------


def _memo(holder) -> dict:
    if holder is None or isinstance(holder, int):
        return {}
    memo = holder.__dict__.get("_cw_memo")
    if memo is None:
        memo = {}
        holder.__dict__["_cw_memo"] = memo
    return memo


def _clear_memo(holder) -> None:
    if holder is not None and not isinstance(holder, int):
        holder.__dict__.pop("_cw_memo", None)


def fetched(session, name: str) -> bool:
    """True when `name` was fetched from its source (not read from the cache) in this request."""
    return name in _memo(session).get("__fetched", ())


def lookup(session, name: str, *, bypass: bool = False) -> Any:
    """The memo's or the cache's value for `name`, or None. Never fetches.

    With `bypass`, only a value fetched from its source in this request counts.
    """
    memo = _memo(session)
    if name in memo and (not bypass or fetched(session, name)):
        return memo[name]
    if bypass:
        return None
    k = key(session, name)
    value = _get(k) if k else _MISS
    if value is _MISS:
        return None
    memo[name] = value
    return value


def remember(session, name: str, value: Any, ttl: int) -> None:
    """Store a value just fetched from its source (memo and cache)."""
    memo = _memo(session)
    memo[name] = value
    memo.setdefault("__fetched", set()).add(name)
    k = key(session, name)
    if k:
        _set(k, value, ttl)


def cached(session, name: str, ttl: int, fetch: Callable[[], T], *, bypass: bool = False) -> T:
    """`name` for the session's worker: from this request's memo, the cache, or `fetch()`.

    `bypass` skips reading the cache (the fresh value is still stored). Only a successful fetch is
    stored; an exception propagates and nothing is cached. Concurrent requests for the same
    missing entry wait briefly for the first one's fetch (single flight) instead of all fetching.
    """
    memo = _memo(session)
    if name in memo and (not bypass or fetched(session, name)):  # never fetch twice in one request
        return memo[name]
    k = key(session, name)
    if k is None or bypass:
        value = fetch()
        remember(session, name, value, ttl)
        return value
    hit = _get(k)
    if hit is not _MISS:
        memo[name] = hit
        return hit
    lock_key = f"{k}:lock"
    got = add(lock_key, 1, SINGLE_FLIGHT_SECONDS)
    if got is False:
        hit = _wait_for(k)
        if hit is not _MISS:
            memo[name] = hit
            return hit
    try:
        value = fetch()
    finally:
        if got:
            _delete(lock_key)
    remember(session, name, value, ttl)
    return value


def _wait_for(k: str) -> Any:
    deadline = time.monotonic() + SINGLE_FLIGHT_WAIT
    lock_key = f"{k}:lock"
    while time.monotonic() < deadline:
        time.sleep(_POLL)
        hit = _get(k)
        if hit is not _MISS:
            return hit
        if _get(lock_key) is _MISS:  # the other fetch finished without a value (it failed)
            return _MISS
    return _MISS


# ---------------------------------------------------------------------------
# Named entries
# ---------------------------------------------------------------------------


def learn_name(opportunity_uuid: str) -> str:
    return f"learn:{opportunity_uuid}"


def delivery_name(opportunity_uuid: str) -> str:
    return f"deliv:{opportunity_uuid}"


def target_name(opportunity_id: str, app_kind: str) -> str:
    return f"target:{opportunity_id}:{app_kind}"


def _awaiting_key(worker, opportunity_uuid: str) -> str:
    # Outside the generation: a bump must not end the wait for Connect.
    return f"{PREFIX}:w:{_worker_id(worker)}:awaiting:{opportunity_uuid}"


def set_awaiting(worker, opportunity_uuid: str) -> None:
    _set(_awaiting_key(worker, opportunity_uuid), 1, AWAITING_TTL)


def awaiting(worker, opportunity_uuid: str) -> bool:
    return _get(_awaiting_key(worker, opportunity_uuid)) is not _MISS


def _synced_key(worker, domain: str) -> str:
    return f"{PREFIX}:w:{_worker_id(worker)}:synced:{domain}"


def last_synced(worker, domain: str) -> float | None:
    value = _get(_synced_key(worker, domain))
    return value if isinstance(value, int | float) else None


def mark_synced(worker, domain: str) -> None:
    _set(_synced_key(worker, domain), time.time(), SYNCED_TTL)


# -- the released build id: shared, not per worker ------------------------------------------------

NO_BUILD = "-"  # the negative entry's value


def _build_key(host: str, domain: str, cc_app_id: str) -> str:
    return f"{PREFIX}:build:{host}:{domain}:{cc_app_id}"


def get_build(host: str, domain: str, cc_app_id: str) -> str | None:
    """A cached build id, `NO_BUILD` for a recent "no released build", or None."""
    value = _get(_build_key(host, domain, cc_app_id))
    return None if value is _MISS else value


def set_build(host: str, domain: str, cc_app_id: str, build_id: str) -> None:
    _set(_build_key(host, domain, cc_app_id), build_id, BUILD_TTL)


def set_no_build(host: str, domain: str, cc_app_id: str) -> None:
    _set(_build_key(host, domain, cc_app_id), NO_BUILD, NO_BUILD_TTL)


def forget_build(host: str, domain: str, cc_app_id: str) -> None:
    _delete(_build_key(host, domain, cc_app_id))
