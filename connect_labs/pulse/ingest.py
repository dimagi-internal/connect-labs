"""Two-speed ingest from Connect's export API.

The two speeds are forced by measured cost, not taste. Per row on the wire:

    user_visits      16,119 bytes   (99% of it form_json, which we discard)
    completed_works      561 bytes
    user_data            637 bytes
    what we store        381 bytes

``user_visits`` has no field-selection and no date filter, so there is no way
to ask for less. A full 1.65M-visit history would move ~26.5 GB to store
~628 MB. But it is also the *only* source of GPS, flags and per-event timing —
i.e. the map, the ticker and the trust cards.

So:

* **Cheap tier** — every opportunity, often. ``opp_org_program_list`` returns
  all ~494 opps *with* lifetime visit counts in a single request, plus
  ``completed_works`` for money. Near-free, and it powers every scalar.
* **Expensive tier** — ``user_visits``, tailed by ``last_id``, only for opps
  that are actually producing work, at a cadence set by how recently they did.

Keyset pagination is what makes the expensive tier viable at all: ``last_id``
turns the endpoint into a change feed, so a poll returns only what is new.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from connect_labs.pulse.models import (
    TIER_COLD,
    TIER_DORMANT,
    TIER_HOT,
    TIER_WARM,
    PulseCursor,
    PulseEvent,
    PulseIngestHealth,
    PulseOpportunity,
    PulseScalar,
)
from connect_labs.pulse.normalize import is_on_map, parse_location, service_slug_for, visit_to_event_fields

logger = logging.getLogger(__name__)

VISITS_ENDPOINT = "user_visits"
PAGE_SIZE = 1000

# Cap rows consumed per opportunity per poll. Prevents one very busy opp from
# monopolising a worker; the cursor simply resumes next tick.
MAX_ROWS_PER_TAIL = 5000

SCALAR_SCOPE = "scope"
SCALAR_OFF_MAP = "off_map_points"


# ---------------------------------------------------------------------------
# Cheap tier
# ---------------------------------------------------------------------------


def refresh_opportunities(client) -> dict:
    """Sync every visible opportunity from ``opp_org_program_list``.

    One request returns orgs, programmes and opps *including* each opp's
    lifetime ``visit_count`` — so the headline scale figures cost nothing.
    """
    from connect_labs.pulse.client import fetch_json

    payload = fetch_json(client, "/export/opp_org_program_list/")
    orgs = payload.get("organizations") or []
    programs = payload.get("programs") or []
    opps = payload.get("opportunities") or []

    program_org = {p["id"]: p.get("organization") or "" for p in programs if p.get("id") is not None}

    seen = 0
    for row in opps:
        opp_id = row.get("id")
        if opp_id is None:
            continue
        name = row.get("name") or ""
        PulseOpportunity.objects.update_or_create(
            opportunity_id=opp_id,
            defaults={
                "name": name[:300],
                "org_slug": (row.get("organization") or program_org.get(row.get("program"), ""))[:120],
                "program_id": row.get("program"),
                "is_active": bool(row.get("is_active")),
                "end_date": row.get("end_date") or None,
                "lifetime_visit_count": row.get("visit_count") or 0,
                "service_slug": service_slug_for(name),
            },
        )
        seen += 1

    scope = {
        "orgs": len(orgs),
        "programs": len(programs),
        "opportunities": len(opps),
        "active_opportunities": sum(1 for o in opps if o.get("is_active")),
        "lifetime_visits": sum(o.get("visit_count") or 0 for o in opps),
    }
    PulseScalar.objects.update_or_create(key=SCALAR_SCOPE, defaults={"value": scope})
    logger.info("[pulse] refreshed %s opportunities; scope=%s", seen, scope)
    return scope


def _to_decimal(raw) -> Decimal | None:
    if raw in (None, "", "None"):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None


def refresh_rate(client, opp: PulseOpportunity, sample: int = 1000) -> Decimal | None:
    """Measure USD actually accrued to the worker per approved unit of work.

    Preferred over converting ``budget_per_visit`` at some FX rate, because
    this is what was really paid. The two agree closely in practice (measured:
    $30.04 vs $30.12 budgeted for KMC Kenya, $0.36 vs $0.38 for Back-to-School),
    and a large divergence means something upstream changed — so it is logged
    rather than silently accepted.

    Caveat this cannot fix: ~0.9% of approved works cover more than one service
    (``saved_approved_count`` > 1), so this is per *work*, not strictly per
    service. Over real data the two differ by under 1% ($2.424 vs $2.400).
    """
    endpoint = f"/export/opportunity/{opp.opportunity_id}/completed_works/"
    total = Decimal(0)
    count = 0
    for page in client.paginate(endpoint, params={"cursor_order": "reverse", "page_size": PAGE_SIZE}, partial_ok=True):
        for row in page:
            if row.get("status") != "approved":
                continue
            usd = _to_decimal(row.get("saved_payment_accrued_usd"))
            if usd is None:
                continue
            total += usd
            count += 1
        if count >= sample:
            break  # declared via partial_ok — a deliberate stop, not a failure

    if not count:
        return None
    rate = (total / count).quantize(Decimal("0.0001"))
    opp.usd_per_service = rate
    opp.save(update_fields=["usd_per_service", "updated_at"])
    return rate


# ---------------------------------------------------------------------------
# Expensive tier — visit tail
# ---------------------------------------------------------------------------


def tier_for(newest_sync_ts, now=None) -> str:
    """Poll cadence from recency of work. ~14 opps are hot at any given time."""
    if newest_sync_ts is None:
        return TIER_DORMANT
    age = (now or timezone.now()) - newest_sync_ts
    if age <= timedelta(hours=6):
        return TIER_HOT
    if age <= timedelta(days=7):
        return TIER_WARM
    if age <= timedelta(days=90):
        return TIER_COLD
    return TIER_DORMANT


def _store_events(rows, opp) -> tuple[int, int]:
    """Normalise and persist a batch. Returns (stored, off_map)."""
    fields_list = []
    off_map = 0
    for row in rows:
        point = parse_location(row.get("location"))
        if point is not None and not is_on_map(*point):
            off_map += 1
        fields = visit_to_event_fields(row, opp)
        if fields is not None:
            fields_list.append(fields)

    if not fields_list:
        return 0, off_map

    # ignore_conflicts: overlapping polls are normal (a cursor is re-read after
    # a failure), and re-seeing a visit must be a no-op rather than an error.
    created = PulseEvent.objects.bulk_create(
        [PulseEvent(**f) for f in fields_list],
        ignore_conflicts=True,
        batch_size=500,
    )
    return len(created), off_map


def tail_visits(client, cursor: PulseCursor, max_rows: int = MAX_ROWS_PER_TAIL) -> dict:
    """Pull everything new for one opportunity since ``cursor.last_id``.

    Forward keyset order, so the API returns only rows created since the last
    poll. Capped per call; the cursor resumes on the next tick.
    """
    opp = PulseOpportunity.objects.filter(opportunity_id=cursor.opportunity_id).first()
    endpoint = f"/export/opportunity/{cursor.opportunity_id}/{VISITS_ENDPOINT}/"
    params = {"cursor_order": "forward", "page_size": PAGE_SIZE}
    if cursor.last_id:
        params["last_id"] = cursor.last_id

    stored = seen = off_map = 0
    max_id = cursor.last_id or 0
    newest = cursor.newest_sync_ts

    for page in client.paginate(endpoint, params=params, partial_ok=True):
        if not page:
            continue
        batch_stored, batch_off_map = _store_events(page, opp)
        stored += batch_stored
        off_map += batch_off_map
        seen += len(page)
        for row in page:
            rid = row.get("id")
            if rid and rid > max_id:
                max_id = rid
        if seen >= max_rows:
            break  # declared via partial_ok

    if stored:
        newest = PulseEvent.objects.filter(opportunity_id=cursor.opportunity_id).order_by("-sync_ts").first().sync_ts

    cursor.last_id = max_id or cursor.last_id
    cursor.newest_sync_ts = newest
    cursor.last_polled_at = timezone.now()
    cursor.tier = tier_for(newest)
    cursor.consecutive_failures = 0
    cursor.last_error = ""
    cursor.save()

    if off_map:
        _bump_off_map(off_map)

    return {"opportunity_id": cursor.opportunity_id, "seen": seen, "stored": stored, "off_map": off_map}


def _bump_off_map(n: int) -> None:
    """Count coordinates dropped for being outside known operating regions.

    Rising numbers here mean either worsening GPS or — more interestingly — a
    country Connect now works in that ``_COUNTRY_BOXES`` doesn't know about.
    Either way it should be visible rather than silent.
    """
    scalar, _ = PulseScalar.objects.get_or_create(key=SCALAR_OFF_MAP, defaults={"value": {"count": 0}})
    scalar.value = {"count": int(scalar.value.get("count", 0)) + n}
    scalar.save(update_fields=["value", "updated_at"])


def ensure_cursors() -> int:
    """Give every known opportunity a visits cursor."""
    existing = set(PulseCursor.objects.filter(endpoint=VISITS_ENDPOINT).values_list("opportunity_id", flat=True))
    missing = [
        PulseCursor(opportunity_id=opp_id, endpoint=VISITS_ENDPOINT, tier=TIER_COLD)
        for opp_id in PulseOpportunity.objects.exclude(opportunity_id__in=existing).values_list(
            "opportunity_id", flat=True
        )
    ]
    if missing:
        PulseCursor.objects.bulk_create(missing, ignore_conflicts=True)
    return len(missing)


def due_cursors(limit: int = 40):
    """Cursors whose tier says they are ready to poll, most-recently-active first."""
    now = timezone.now()
    candidates = PulseCursor.objects.filter(endpoint=VISITS_ENDPOINT).order_by("-newest_sync_ts")
    return [c for c in candidates if c.is_due(now)][:limit]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def record_success(tier: str) -> None:
    health, _ = PulseIngestHealth.objects.get_or_create(tier=tier)
    now = timezone.now()
    health.last_success_at = now
    health.last_attempt_at = now
    health.consecutive_failures = 0
    health.last_error = ""
    health.save()


def record_failure(tier: str, error: str) -> None:
    health, _ = PulseIngestHealth.objects.get_or_create(tier=tier)
    now = timezone.now()
    health.last_attempt_at = now
    health.last_error_at = now
    health.last_error = str(error)[:2000]
    health.consecutive_failures += 1
    health.save()
    logger.error("[pulse] %s tier failed (%sx): %s", tier, health.consecutive_failures, error)


# ---------------------------------------------------------------------------
# Rollups
# ---------------------------------------------------------------------------


def rebuild_rollups(since=None) -> int:
    """Recompute hourly aggregates so cards never scan raw events."""
    from django.db.models import Count, Q, Sum
    from django.db.models.functions import TruncHour

    from connect_labs.pulse.models import PulseRollup

    qs = PulseEvent.objects.all()
    if since is not None:
        qs = qs.filter(field_ts__gte=since)

    rows = (
        qs.annotate(bucket=TruncHour("field_ts"))
        .values("bucket", "opportunity_id", "status")
        .annotate(n=Count("id"), flagged_n=Count("id", filter=Q(flagged=True)), usd=Sum("usd_to_worker"))
    )

    written = 0
    with transaction.atomic():
        for row in rows:
            PulseRollup.objects.update_or_create(
                bucket_hour=row["bucket"],
                opportunity_id=row["opportunity_id"],
                status=row["status"],
                defaults={"n": row["n"], "flagged_n": row["flagged_n"], "usd": row["usd"] or 0},
            )
            written += 1
    return written
