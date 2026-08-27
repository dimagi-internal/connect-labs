"""One entry point for "this opp's fixtures changed — forget everything derived
from them".

Four independently-keyed caches sit between a Drive folder and a rendered
dashboard, and none of them is keyed on fixture *content*:

| Cache | Key | Where |
|---|---|---|
| registry (is this opp synthetic, which folder) | `opp_id` | `registry.invalidate_cache` |
| FixtureStore (parsed fixture JSON) | `(opp_id, folder_id, endpoint_key)` | `fixture_store.reload` |
| RawVisitCache | `(opportunity_id, pipeline_id)` | `SQLCacheManager.delete_all_cache` |
| Computed visit/FLW/entity rows | `(opportunity_id, config_hash, …)` | same |

`synthetic_register` used to clear only the first, so replacing fixture bytes at
a stable folder id was invisible to the other three. The reported cost (#1034)
was six escalating attempts to make a regenerated dataset reach a dashboard —
new folder id AND a new pipeline AND changed schema content — with every
intermediate state rendering *cleanly* while serving superseded numbers. A
confident, wrong dashboard is the worst failure shape this system has.

The computed caches are keyed on `config_hash`, so two pipelines with identical
schemas share rows: minting a fresh pipeline does not escape them and deleting
by opportunity is what actually works.

The issue argued for making the caches content-keyed instead, on the grounds that
`FixtureStore` is a per-process singleton and a reload in the MCP worker leaves
the five web workers stale. #1300 closed that hole — `reload` now bumps a shared
generation counter, so it means the same thing in every process — which is what
makes this reload-based approach sufficient.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def invalidate_synthetic_caches(opp_id: int, *, drop_sql_cache: bool = True) -> dict:
    """Forget every cached artifact derived from opp ``opp_id``'s fixtures.

    Safe to call on a non-synthetic opp and safe to call repeatedly. Each layer
    is independently guarded: a Drive hiccup or a missing table must not fail the
    registration that just succeeded, since a half-invalidated cache is still
    strictly better than the stale one it replaced.

    ``drop_sql_cache=False`` skips the analysis tables, for callers that have
    only changed registry metadata (a label, an enabled flag) and know the
    fixtures themselves are untouched.
    """
    from connect_labs.labs.synthetic.registry import invalidate_cache

    outcome: dict[str, object] = {}

    try:
        invalidate_cache()
        outcome["registry"] = True
    except Exception:
        logger.exception("invalidate_synthetic_caches: registry cache for opp %s", opp_id)
        outcome["registry"] = False

    try:
        from connect_labs.labs.integrations.connect import factory

        factory._get_fixture_store().reload(opp_id)
        outcome["fixture_store"] = True
    except Exception:
        logger.exception("invalidate_synthetic_caches: fixture store for opp %s", opp_id)
        outcome["fixture_store"] = False

    if drop_sql_cache:
        try:
            from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

            outcome["sql_cache"] = SQLCacheManager.delete_all_cache(opp_id)
        except Exception:
            logger.exception("invalidate_synthetic_caches: SQL analysis cache for opp %s", opp_id)
            outcome["sql_cache"] = False

    logger.info("invalidate_synthetic_caches(opp=%s): %s", opp_id, outcome)
    return outcome
