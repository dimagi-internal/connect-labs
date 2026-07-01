"""Cache a synthetic opp's user_visits count onto its registry row.

The labs-context opportunity picker shows ``opp.visit_count``; for a synthetic
opp the real count lives in its GDrive ``user_visits.json`` fixture, which is too
costly to fetch on every request (see ``labs.context._merge_labs_only_opps``). So
we compute it from the fixture once (at generation, or via the
``refresh_synthetic_visit_counts`` management command) and persist it here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def refresh_visit_count(opp) -> int | None:
    """Load ``opp``'s synthetic user_visits via the fixture store and store the
    count on the ``SyntheticOpportunity`` row. Returns the count, or None on any
    failure (the row is left unchanged so a transient Drive error can't zero it)."""
    from connect_labs.labs.integrations.connect import factory

    try:
        store = factory._get_fixture_store()
        visits = store.load_endpoint(opp.opportunity_id, "user_visits")
    except Exception:  # noqa: BLE001 — Drive/transport hiccup shouldn't propagate
        logger.exception("refresh_visit_count: could not load user_visits for opp %s", opp.opportunity_id)
        return None

    count = len(visits) if isinstance(visits, list) else 0
    if opp.visit_count != count:
        opp.visit_count = count
        opp.save(update_fields=["visit_count", "updated_at"])
    return count
