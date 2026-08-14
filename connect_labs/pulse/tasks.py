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
import time
from datetime import datetime
from datetime import timezone as dt_timezone

from config import celery_app
from connect_labs.pulse import ingest
from connect_labs.pulse.client import PulseAuthError, get_client
from connect_labs.pulse.models import PulseCursor, PulseOpportunity

logger = logging.getLogger(__name__)

# "Complete to the beginning of time" — recorded when a stream is exhausted, so
# no future request, however deep, re-walks an opportunity with no more history.
_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

TIER_CHEAP = "cheap"
TIER_TAIL = "tail"
TIER_WORKS = "works"


@celery_app.task(name="connect_labs.pulse.tasks.poll_cheap_tier")
def poll_cheap_tier() -> dict:
    """Refresh opportunity metadata and the scope scalars. One request.

    Everything whose inputs move on the order of hours -- per-service rates, the
    derived country, the denormalised delivery type -- lives in
    ``poll_slow_maintenance`` instead. They were on this five-minute path, and
    measured on prod the task took 17.8s on average (44.6s at worst) to change
    ten rows, because the two backfills issue one UPDATE per opportunity against
    a million-row table whether or not anything disagrees.
    """
    try:
        with get_client() as client:
            scope = ingest.refresh_opportunities(client)
            ingest.ensure_cursors()

        ingest.record_success(TIER_CHEAP)
        return {"scope": scope}
    except PulseAuthError as exc:
        # The expired-refresh-token case. Must be loud: this is the failure that
        # otherwise looks exactly like "no new data".
        ingest.record_failure(TIER_CHEAP, f"auth: {exc}")
        raise
    except Exception as exc:
        ingest.record_failure(TIER_CHEAP, str(exc))
        raise


@celery_app.task(name="connect_labs.pulse.tasks.poll_slow_maintenance")
def poll_slow_maintenance(rate_sample_limit: int = 25) -> dict:
    """The sweeps whose inputs move in hours, not minutes.

    Split off the cheap tier, which runs every five minutes. None of this is
    time-critical:

    * per-service rates are measured payouts that change when a programme
      renegotiates, not between polls;
    * an opportunity's country is derived from the modal country of its visits,
      so it only moves when a genuinely new opportunity starts delivering;
    * the delivery-type resync exists to push a *definition* change onto
      history, which happens when the code changes, not when data arrives.

    Each also costs one query per opportunity against ``PulseWork`` (1M rows)
    regardless of whether anything differs, so the cadence is what matters.
    """
    rated = 0
    try:
        with get_client() as client:
            active = PulseOpportunity.objects.filter(is_active=True).order_by("updated_at")[:rate_sample_limit]
            for opp in active:
                try:
                    if ingest.refresh_rate(client, opp) is not None:
                        rated += 1
                except Exception as exc:  # one bad opp must not kill the sweep
                    logger.warning("[pulse] rate refresh failed for opp %s: %s", opp.opportunity_id, exc)

        countries = ingest.refresh_opportunity_countries()
        services = ingest.resync_service_slugs()
        return {"rates_refreshed": rated, "countries_set": countries, "services_resynced": services}
    except PulseAuthError as exc:
        ingest.record_failure(TIER_CHEAP, f"auth: {exc}")
        raise


# One invocation sweeps repeatedly instead of the beat firing more often.
#
# Measured on prod: Celery costs 3-7s of dispatch overhead per invocation --
# calling poll_cheap_tier directly took 5.2-7.6s while Celery reported 8.3-14.1s
# for the same work. So buying freshness by shortening the beat would spend most
# of the extra budget on dispatch, not on polling. Sweeping inside one
# invocation pays that overhead once a minute however fresh the view gets.
#
# Bounded well under the 60s beat so two invocations can never overlap and
# double-poll the same cursors.
SWEEP_INTERVAL_SECONDS = 15
SWEEP_DEADLINE_SECONDS = 50


@celery_app.task(name="connect_labs.pulse.tasks.poll_visit_tail")
def poll_visit_tail(
    limit: int = 40,
    sweep_interval: float = SWEEP_INTERVAL_SECONDS,
    deadline: float = SWEEP_DEADLINE_SECONDS,
) -> dict:
    """Tail user_visits for every due cursor, sweeping repeatedly for ~50s.

    The live view claims to show delivery as it happens, so what matters is the
    lag between a visit reaching Connect and reaching this screen. That is the
    cursor's due interval plus the wait for the next sweep -- previously 60 + 60
    at worst. Now 15 + 15.
    """
    started = time.monotonic()
    stored = polled = sweeps = 0
    try:
        with get_client() as client:
            while True:
                cursors = ingest.due_cursors(limit=limit)
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
                sweeps += 1

                # Recorded per sweep, not once at the end: health should reflect
                # the most recent sweep, not the start of a minute-long task.
                ingest.record_success(TIER_TAIL)

                elapsed = time.monotonic() - started
                if elapsed + sweep_interval >= deadline:
                    break
                time.sleep(sweep_interval)

        if stored:
            ingest.rebuild_rollups(since=_recent_window())
        return {"polled": polled, "stored": stored, "sweeps": sweeps}
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
def backfill_visits(
    self,
    days: int = 90,
    opportunity_ids: list[int] | None = None,
    page_pause: float | None = None,
    max_seconds: int | None = None,
    max_pages_per_opp: int | None = None,
) -> dict:
    """Walk history backwards, one opportunity at a time, resumably.

    Manual and one-shot, and the expensive part of the whole system: ~4.6 KB
    per row, because ``user_visits`` ships every form's full JSON and offers no
    way to ask for less. Measured against production: ~330 rows/sec, ~2.25 GB
    and ~84 minutes of pulling for the full 1.67M-visit history.

    Three properties make that survivable, and all three are deliberate:

    * **Per opportunity.** Each opportunity has its own cursor, so the unit of
      work is small and a failure is contained to one programme.
    * **Resumable at page granularity.** ``_backfill_one`` commits its cursor
      after every page, so an interrupted run resumes where it stopped rather
      than restarting the opportunity.
    * **Paced.** ``page_pause`` seconds between requests, so a full walk is a
      steady trickle against a production endpoint rather than a flood.

    ``max_seconds`` bounds a single invocation so this can be run repeatedly in
    modest slices; opportunities not reached simply stay incomplete and are
    picked up next time.
    """
    from django.conf import settings
    from django.utils import timezone

    if page_pause is None:
        page_pause = float(getattr(settings, "PULSE_BACKFILL_PAGE_PAUSE", 0.25))

    from django.db.models import Q

    started = time.monotonic()
    cutoff = timezone.now() - timezone.timedelta(days=days)

    # Skip only what is provably complete to AT LEAST this depth. Selecting on
    # `backfill_complete` alone let a shallow pass permanently cap every deeper
    # one -- see PulseCursor.backfill_complete_to. A NULL depth is treated as
    # unknown and re-checked, which costs one request and cannot hide history.
    qs = PulseCursor.objects.filter(endpoint=ingest.VISITS_ENDPOINT).exclude(
        Q(backfill_complete=True) & Q(backfill_complete_to__isnull=False) & Q(backfill_complete_to__lte=cutoff)
    )
    if opportunity_ids:
        qs = qs.filter(opportunity_id__in=opportunity_ids)

    total = 0
    done = 0
    failed = 0
    stopped_early = False

    with get_client(timeout=300.0) as client:
        # Ordered oldest-activity-last so the programmes a funder is most
        # likely to ask about are filled in first, and a bounded slice is still
        # useful rather than arbitrary.
        for cursor in qs.order_by("-newest_sync_ts"):
            if max_seconds is not None and (time.monotonic() - started) >= max_seconds:
                stopped_early = True
                break
            try:
                total += _backfill_one(client, cursor, cutoff, page_pause=page_pause, max_pages=max_pages_per_opp)
                done += 1
            except Exception as exc:
                failed += 1
                logger.warning("[pulse] backfill failed for opp %s: %s", cursor.opportunity_id, exc)

    # Roll up everything just pulled, not merely the requested window: the rows
    # are about to age out into the grid, and the rollups are the only permanent
    # record of visit volume by status. Scoping this to `cutoff` was how a deep
    # pull could leave no trace of the history it had just fetched.
    ingest.rebuild_rollups(since=cutoff if days <= 90 else None)

    remaining = PulseCursor.objects.filter(endpoint=ingest.VISITS_ENDPOINT, backfill_complete=False).count()
    return {
        "stored": total,
        "opportunities_completed": done,
        "opportunities_failed": failed,
        "opportunities_remaining": remaining,
        "stopped_early": stopped_early,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "cutoff": cutoff.isoformat(),
    }


def _backfill_one(
    client, cursor: PulseCursor, cutoff, *, page_pause: float = 0.0, max_pages: int | None = None
) -> int:
    """Page backwards from newest until we cross the cutoff or run out.

    Progress is committed **after every page**, not at the end. The previous
    version saved once the whole opportunity was done, which meant a task killed
    mid-opportunity -- a deploy, an OOM, a lost token -- threw away everything it
    had pulled for that opportunity and restarted from the same place next time.
    On the largest programme (chc, ~1.25M visits) that is not a slow recovery,
    it is a run that can never finish. Saving per page makes the walk genuinely
    resumable: re-running continues from the oldest id already seen.

    ``page_pause`` puts a deliberate gap between requests. A full history walk
    is ~1.6M rows off a production export endpoint that serialises every visit's
    form JSON, so the polite pace matters more than the elapsed time.
    """
    opp = PulseOpportunity.objects.filter(opportunity_id=cursor.opportunity_id).first()
    endpoint = f"/export/opportunity/{cursor.opportunity_id}/{ingest.VISITS_ENDPOINT}/"
    params = {"cursor_order": "reverse", "page_size": ingest.PAGE_SIZE}
    if cursor.backfill_oldest_id:
        params["last_id"] = cursor.backfill_oldest_id

    stored = 0
    finished = False
    exhausted = False
    pages = 0

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
            cursor.save(update_fields=["backfill_oldest_id", "last_id"])

        pages += 1

        # Only consider timestamps that actually parsed. `min()` over the raw
        # values returns "" when any row lacks date_created, and "" is falsy --
        # so the cutoff check silently never fired and the walk ran past it.
        stamps = [r.get("date_created") for r in page if r.get("date_created")]
        if stamps and min(stamps) < cutoff.isoformat():
            finished = True
            break  # declared via partial_ok

        if max_pages is not None and pages >= max_pages:
            break  # bounded slice; not finished, so the cursor stays resumable

        if page_pause:
            time.sleep(page_pause)
    else:
        # The generator ended on its own. That is only evidence of "no more
        # history" if we actually SAW something -- an empty response is
        # ambiguous, and `backfill_complete` is sticky, so guessing wrong stops
        # an opportunity being walked ever again.
        #
        # This exact mistake cost a run: 409 opportunities returned zero rows
        # and were marked complete, including ones holding 100k+ unfetched
        # visits (opp 411: 1,000 held against 101,458 lifetime). Requiring a
        # page makes the failure mode "re-check an exhausted opp once per run",
        # which is one cheap empty request, instead of "silently stop pulling
        # real data", which is unrecoverable without a manual reset.
        finished = exhausted = pages > 0

    if finished:
        cursor.backfill_complete = True
        # Record how deep this counts as. Exhausting the stream means there is
        # genuinely nothing older, so it satisfies any future depth; stopping at
        # the cutoff only satisfies requests no deeper than that cutoff.
        cursor.backfill_complete_to = _EPOCH if exhausted else cutoff
        cursor.save(update_fields=["backfill_complete", "backfill_complete_to"])
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
    """Age out visit-level rows into the anonymous grid, then apply retention.

    Runs nightly. Two steps, deliberately separate: folding makes the map
    denser and is always safe to run, while pruning is the destructive half and
    obeys ``PULSE_EVENT_RETENTION_DAYS``. Setting that to ``None`` keeps the
    visit rows as a local, re-derivable fact store -- the map is unaffected
    either way, because it reads the grid.
    """
    result = ingest.fold_events_to_grid()
    result["deleted"] = ingest.prune_folded_events()
    return result
