"""Cache a synthetic opp's user_visits count onto its registry row.

The labs-context opportunity picker shows ``opp.visit_count``; for a synthetic
opp the real count lives in its GDrive ``user_visits.json`` fixture, which is too
costly to fetch on every request (see ``labs.context._merge_labs_only_opps``). So
we compute it from the fixture once and persist it here.

Two callers, two different right answers on failure — which is the whole reason
there are two functions:

* ``refresh_visit_count`` re-derives a count that is still *believed correct*
  (the periodic management command). If Drive hiccups, the stored number is
  still the best available answer, so the row is left alone.
* ``resync_visit_count`` runs when the fixture folder has just been repointed,
  which makes the stored number *known wrong*. Leaving it would print a
  confidently incorrect count in the picker, so a failure nulls the field
  instead — the model already defines null as "not yet computed" (#1197).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _count_visits(opp) -> int | None:
    """Fixture visit count for ``opp``, or None if it could not be read."""
    from connect_labs.labs.integrations.connect import factory

    try:
        store = factory._get_fixture_store()
        visits = store.load_endpoint(opp.opportunity_id, "user_visits")
    except Exception:  # noqa: BLE001 — Drive/transport hiccup shouldn't propagate
        logger.exception("visit_count: could not load user_visits for opp %s", opp.opportunity_id)
        return None

    return len(visits) if isinstance(visits, list) else 0


def _store(opp, count: int | None) -> int | None:
    if opp.visit_count != count:
        opp.visit_count = count
        opp.save(update_fields=["visit_count", "updated_at"])
    return count


def refresh_visit_count(opp) -> int | None:
    """Recompute and persist ``opp``'s cached visit count.

    Returns the count, or None on any failure — in which case the row is left
    unchanged, so a transient Drive error can't zero a still-valid number.
    """
    count = _count_visits(opp)
    if count is None:
        return None
    return _store(opp, count)


def resync_visit_count(opp, *, previous_folder_id: str | None = None) -> int | None:
    """Bring the cached count back in line after a register/repoint.

    Only ``synthetic_generate_from_manifest`` ever refreshed this, so every other
    way of pointing an opp at a new fixture folder — ``synthetic_register``,
    ``synthetic_repoint_by_source``, ``upload_and_register``,
    ``register_labs_only_opp`` — left the old count on the row, and the labs
    chrome printed it next to the new data (#1197).

    ``previous_folder_id`` is a no-op guard: re-registering the same folder is
    idempotent and its count is still true, so it isn't re-fetched. (An in-place
    byte swap behind an unchanged folder id is a different problem, and one the
    fixture store's own cache owns — see #1034.)

    Unlike ``refresh_visit_count``, a failure here stores None rather than
    keeping the old value: after a repoint the old value is known to be wrong,
    and the picker renders null as 0 rather than as a stale count.
    """
    if previous_folder_id is not None and previous_folder_id == opp.gdrive_folder_id:
        return opp.visit_count
    return _store(opp, _count_visits(opp))
