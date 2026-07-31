"""Celery entry points for Pulse ingest.

Three jobs on different clocks:

* ``poll_cheap_tier`` — every 5 min. All opportunities; scope scalars and money.
* ``poll_visit_tail`` — every minute. Only cursors whose tier says they're due.
* ``backfill_visits`` — one-shot, manual. Deliberately not on beat.

Backfill is a separate task from tailing on purpose: it is slow (hours for deep
history) and must never stall the live tail behind it.
"""

from __future__ import annotations

import logging

from config import celery_app
from connect_labs.pulse import ingest
from connect_labs.pulse.client import PulseAuthError, get_client
from connect_labs.pulse.models import PulseCursor, PulseOpportunity

logger = logging.getLogger(__name__)

TIER_CHEAP = "cheap"
TIER_TAIL = "tail"
TIER_WORKS = "works"


@celery_app.task(name="connect_labs.pulse.tasks.poll_cheap_tier")
def poll_cheap_tier(rate_sample_limit: int = 25) -> dict:
    """Refresh opportunity metadata, scope scalars and per-service rates.

    Cheap enough to cover every opportunity: one request for the whole list,
    then ``completed_works`` (561 B/row) for the opps most likely to have moved.
    """
    try:
        with get_client() as client:
            scope = ingest.refresh_opportunities(client)
            ingest.ensure_cursors()

            # Rates change slowly; refresh the active opps, most stale first.
            active = PulseOpportunity.objects.filter(is_active=True).order_by("updated_at")[:rate_sample_limit]
            rated = 0
            for opp in active:
                try:
                    if ingest.refresh_rate(client, opp) is not None:
                        rated += 1
                except Exception as exc:  # one bad opp must not kill the sweep
                    logger.warning("[pulse] rate refresh failed for opp %s: %s", opp.opportunity_id, exc)

        # Country comes from visit GPS, not from the opportunity record, so it
        # has to be derived after events land rather than during the sync.
        countries = ingest.refresh_opportunity_countries()

        # Delivery type is denormalised onto stored rows, so a change in how it
        # is derived has to be pushed to history as well as applied going
        # forward. No-ops once everything agrees.
        services = ingest.resync_service_slugs()

        ingest.record_success(TIER_CHEAP)
        return {
            "scope": scope,
            "rates_refreshed": rated,
            "countries_set": countries,
            "services_resynced": services,
        }
    except PulseAuthError as exc:
        # The expired-refresh-token case. Must be loud: this is the failure that
        # otherwise looks exactly like "no new data".
        ingest.record_failure(TIER_CHEAP, f"auth: {exc}")
        raise
    except Exception as exc:
        ingest.record_failure(TIER_CHEAP, str(exc))
        raise


@celery_app.task(name="connect_labs.pulse.tasks.poll_visit_tail")
def poll_visit_tail(limit: int = 40) -> dict:
    """Tail user_visits for every cursor that is due."""
    cursors = ingest.due_cursors(limit=limit)
    if not cursors:
        ingest.record_success(TIER_TAIL)
        return {"polled": 0, "stored": 0}

    stored = polled = 0
    try:
        with get_client() as client:
            for cursor in cursors:
                try:
                    result = ingest.tail_visits(client, cursor)
                    stored += result["stored"]
                    polled += 1
                except Exception as exc:
                    # Isolate per-opportunity failures: one opp erroring must
                    # not stop the rest of the sweep or freeze the display.
                    cursor.consecutive_failures += 1
                    cursor.last_error = str(exc)[:2000]
                    cursor.save(update_fields=["consecutive_failures", "last_error"])
                    logger.warning("[pulse] tail failed for opp %s: %s", cursor.opportunity_id, exc)

        ingest.record_success(TIER_TAIL)
        if stored:
            ingest.rebuild_rollups(since=_recent_window())
        return {"polled": polled, "stored": stored}
    except PulseAuthError as exc:
        ingest.record_failure(TIER_TAIL, f"auth: {exc}")
        raise
    except Exception as exc:
        ingest.record_failure(TIER_TAIL, str(exc))
        raise


def _recent_window():
    from django.utils import timezone

    return timezone.now() - timezone.timedelta(days=4)


@celery_app.task(name="connect_labs.pulse.tasks.backfill_visits", bind=True)
def backfill_visits(self, days: int = 90, opportunity_ids: list[int] | None = None) -> dict:
    """Walk history backwards for the chosen opportunities.

    Manual and one-shot, and the expensive part of the whole system: ~4.6 KB
    gzipped per row, because ``user_visits`` ships every form's full JSON and
    offers no way to ask for less.

    90 days is the default because it covers every currently-live programme.
    Full history is ~7.5 GB and, at the ~470 events/sec measured against
    production, roughly 1-2 hours — tractable if the denser map is worth it.
    """
    from django.utils import timezone

    cutoff = timezone.now() - timezone.timedelta(days=days)
    qs = PulseCursor.objects.filter(endpoint=ingest.VISITS_ENDPOINT, backfill_complete=False)
    if opportunity_ids:
        qs = qs.filter(opportunity_id__in=opportunity_ids)

    total = 0
    with get_client(timeout=300.0) as client:
        for cursor in qs.order_by("-newest_sync_ts"):
            try:
                total += _backfill_one(client, cursor, cutoff)
            except Exception as exc:
                logger.warning("[pulse] backfill failed for opp %s: %s", cursor.opportunity_id, exc)

    ingest.rebuild_rollups(since=cutoff)
    return {"stored": total, "cutoff": cutoff.isoformat()}


def _backfill_one(client, cursor: PulseCursor, cutoff) -> int:
    """Page backwards from newest until we cross the cutoff."""
    opp = PulseOpportunity.objects.filter(opportunity_id=cursor.opportunity_id).first()
    endpoint = f"/export/opportunity/{cursor.opportunity_id}/{ingest.VISITS_ENDPOINT}/"
    params = {"cursor_order": "reverse", "page_size": ingest.PAGE_SIZE}
    if cursor.backfill_oldest_id:
        params["last_id"] = cursor.backfill_oldest_id

    stored = 0
    reached_cutoff = False
    for page in client.paginate(endpoint, params=params, partial_ok=True):
        if not page:
            continue
        batch_stored, off_map = ingest._store_events(page, opp)
        stored += batch_stored
        if off_map:
            ingest._bump_off_map(off_map)

        ids = [r.get("id") for r in page if r.get("id")]
        if ids:
            cursor.backfill_oldest_id = min(ids)
            # Forward cursor must also know about these rows, or the tail would
            # re-fetch history it already has.
            cursor.last_id = max(cursor.last_id or 0, max(ids))

        oldest_ts = min((r.get("date_created") or "") for r in page)
        if oldest_ts and oldest_ts < cutoff.isoformat():
            reached_cutoff = True
            break  # declared via partial_ok

    cursor.backfill_complete = reached_cutoff
    cursor.save(update_fields=["backfill_oldest_id", "last_id", "backfill_complete"])
    return stored


@celery_app.task(name="connect_labs.pulse.tasks.rebuild_rollups")
def rebuild_rollups(days: int = 7) -> int:
    from django.utils import timezone

    return ingest.rebuild_rollups(since=timezone.now() - timezone.timedelta(days=days))


@celery_app.task(name="connect_labs.pulse.tasks.poll_works")
def poll_works(limit: int = 40) -> dict:
    """Tail completed_works — the money and payment-status spine.

    Separate from the visit tail because it is ~25x cheaper per row and carries
    the full history, while the visit tail is deliberately kept to a rolling
    window. This is the stream that answers "how much was earned and paid".
    """
    cursors = [
        c for c in PulseCursor.objects.filter(endpoint=ingest.WORKS_ENDPOINT).order_by("-newest_sync_ts") if c.is_due()
    ][:limit]
    if not cursors:
        ingest.record_success(TIER_WORKS)
        return {"polled": 0, "stored": 0}

    stored = polled = 0
    try:
        with get_client() as client:
            for cursor in cursors:
                try:
                    stored += ingest.sync_works(client, cursor)["stored"]
                    polled += 1
                except Exception as exc:
                    cursor.consecutive_failures += 1
                    cursor.last_error = str(exc)[:2000]
                    cursor.save(update_fields=["consecutive_failures", "last_error"])
                    logger.warning("[pulse] works sync failed for opp %s: %s", cursor.opportunity_id, exc)
        ingest.record_success(TIER_WORKS)
        return {"polled": polled, "stored": stored}
    except PulseAuthError as exc:
        ingest.record_failure(TIER_WORKS, f"auth: {exc}")
        raise
    except Exception as exc:
        ingest.record_failure(TIER_WORKS, str(exc))
        raise


@celery_app.task(name="connect_labs.pulse.tasks.fold_events_to_grid")
def fold_events_to_grid() -> dict:
    """Age out visit-level rows into the anonymous grid.

    Runs nightly. The map keeps getting denser; labs stops holding the records
    that made it dense.
    """
    return ingest.fold_events_to_grid()
