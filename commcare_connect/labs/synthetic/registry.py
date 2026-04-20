"""Per-worker TTL-cached registry of synthetic opportunities.

The hot path (real-opp export calls) must cost essentially nothing — one dict
lookup. We refresh the full enabled-opp set every 60 seconds per worker.
"""

from __future__ import annotations

import time

from commcare_connect.labs.synthetic.models import SyntheticOpportunity

_TTL_SECONDS = 60
_CACHE: dict = {"loaded_at": 0.0, "opps_by_id": {}}


def get_synthetic_opp(opportunity_id: int) -> SyntheticOpportunity | None:
    """Return the enabled SyntheticOpportunity row for `opportunity_id`, or None."""
    # Note: in multi-threaded gthread workers there's a small race between
    # updating `opps_by_id` and `loaded_at`. Worst case is a redundant DB
    # query; never incorrect data. We deliberately skip locking to keep the
    # hot path overhead near zero.
    now = time.monotonic()
    if now - _CACHE["loaded_at"] > _TTL_SECONDS:
        rows = SyntheticOpportunity.objects.filter(enabled=True)
        _CACHE["opps_by_id"] = {r.opportunity_id: r for r in rows}
        _CACHE["loaded_at"] = now
    return _CACHE["opps_by_id"].get(opportunity_id)


def invalidate_cache() -> None:
    """Force the next `get_synthetic_opp` call to re-query the database."""
    _CACHE["loaded_at"] = 0.0
    _CACHE["opps_by_id"] = {}
