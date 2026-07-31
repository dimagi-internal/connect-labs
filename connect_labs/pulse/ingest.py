"""Two-speed ingest from Connect's export API.

The two speeds are forced by measured cost, not taste. Measured across six
programmes (1,200 sampled rows), per ``user_visits`` row:

    uncompressed        25,153 bytes   (94-103% of it form_json, discarded)
    gzipped on the wire  4,578 bytes   (form JSON is repetitive: 5.5x)
    what we store           381 bytes

    completed_works         561 bytes  } clean — no form_json
    user_data               637 bytes  }

``user_visits`` has no field-selection and no date filter, so there is no way
to ask for less: we download every anthropometric reading and every form
answer, then keep the timestamp, the point and the status. Across all 1.65M
visits that is ~41 GB uncompressed / **~7.5 GB actually transferred**, to store
~0.63 GB.

Cost varies ~10x by programme — Back-to-School is 6.5 KB/row raw, Readers is
62 KB/row — and the expensive ones also compress worst (4.5x vs 12.9x), because
their bulk is unique content rather than boilerplate.

But ``user_visits`` is also the *only* source of GPS, flags and per-event
timing — i.e. the map, the ticker and the trust cards.

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
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count as models_count
from django.utils import timezone

from connect_labs.pulse.models import (
    TIER_COLD,
    TIER_DORMANT,
    TIER_HOT,
    TIER_WARM,
    PulseCursor,
    PulseEvent,
    PulseGridCell,
    PulseIngestHealth,
    PulseOpportunity,
    PulseProgram,
    PulseScalar,
    PulseWork,
)
from connect_labs.pulse.normalize import (
    is_on_map,
    looks_like_test,
    parse_location,
    service_slug_for,
    visit_to_event_fields,
)

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

    # Mirror the programmes themselves. Previously this payload was read only
    # for org slugs, and its `name` and `delivery_type` were dropped -- which is
    # why service categorisation was a regex over opportunity names.
    program_delivery: dict[int, str] = {}
    for p in programs:
        pid = p.get("id")
        if pid is None:
            continue
        delivery = (p.get("delivery_type") or "").strip()
        program_delivery[pid] = delivery
        pname = p.get("name") or ""
        PulseProgram.objects.update_or_create(
            program_id=pid,
            defaults={
                "name": pname[:300],
                "delivery_type": delivery[:48],
                "org_slug": (p.get("organization") or "")[:120],
                "currency": (p.get("currency") or "")[:8],
                "is_test": looks_like_test(pname),
            },
        )

    seen = 0
    for row in opps:
        opp_id = row.get("id")
        if opp_id is None:
            continue
        name = row.get("name") or ""
        # Connect's own delivery_type wins; the name regex is the fallback for
        # an opportunity whose programme has none set.
        delivery = program_delivery.get(row.get("program")) or ""
        PulseOpportunity.objects.update_or_create(
            opportunity_id=opp_id,
            defaults={
                "name": name[:300],
                "org_slug": (row.get("organization") or program_org.get(row.get("program"), ""))[:120],
                "program_id": row.get("program"),
                "is_active": bool(row.get("is_active")),
                "end_date": row.get("end_date") or None,
                "lifetime_visit_count": row.get("visit_count") or 0,
                "service_slug": delivery[:48] or service_slug_for(name),
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


def refresh_opportunity_countries() -> int:
    """Derive each opportunity's country from where its work actually happened.

    Nothing in the export says which country an opportunity operates in — the
    name sometimes hints ("KMC - UG - ...") but not reliably, and it is free
    text. The only ground truth is the GPS on its visits.

    So: take the modal country across an opp's events. Without this,
    ``PulseOpportunity.country`` stays empty and every country-scoped card
    (notably "$ by country" on the financial view) silently renders nothing —
    a field that exists but is never populated is worse than one that is
    absent, because it fails quietly rather than loudly.

    Caveat worth knowing: an opportunity quiet for longer than the event
    retention window has no events left to derive from, so it keeps whatever
    country it was last assigned. That is why this runs on the cheap tier
    (every 5 min) rather than only at backfill time.
    """
    updated = 0
    rows = (
        PulseEvent.objects.exclude(country="")
        .values("opportunity_id", "country")
        .annotate(n=models_count("id"))
        .order_by("opportunity_id", "-n")
    )
    modal: dict[int, str] = {}
    for row in rows:
        modal.setdefault(row["opportunity_id"], row["country"])

    for opp_id, country in modal.items():
        updated += (
            PulseOpportunity.objects.filter(opportunity_id=opp_id).exclude(country=country).update(country=country)
        )

    # Works denormalise country from their opportunity, so a newly-derived
    # country has to be pushed onto rows already stored — otherwise money-by-
    # country stays empty for all historical work.
    backfilled = 0
    for opp in PulseOpportunity.objects.exclude(country=""):
        backfilled += (
            PulseWork.objects.filter(opportunity_id=opp.opportunity_id)
            .exclude(country=opp.country)
            .update(country=opp.country)
        )

    if updated or backfilled:
        logger.info("[pulse] set country on %s opportunities from visit GPS; backfilled %s works", updated, backfilled)
    return updated


def sample_opportunity_countries(client, limit: int = 600) -> int:
    """Give historical opportunities a country from a single sampled visit.

    ``refresh_opportunity_countries`` derives country from stored events, but
    events are a rolling window — an opportunity that stopped delivering before
    the window has none, so it never gets a country and its (possibly large)
    historical spend is invisible to every country-scoped card.

    One ``cursor_order=reverse&page_size=1`` request returns that opportunity's
    most recent visit, which carries GPS. That is ~0.45s and a few KB per
    opportunity — cheap enough to cover the whole estate — and it means the
    money spine gets geography without pulling a single extra visit into
    storage. The sampled record is read for its coordinate and discarded.
    """
    from connect_labs.pulse.normalize import country_for

    targets = PulseOpportunity.objects.filter(country="").values_list("opportunity_id", flat=True)[:limit]
    resolved = 0
    for opp_id in list(targets):
        endpoint = f"/export/opportunity/{opp_id}/{VISITS_ENDPOINT}/"
        try:
            page = next(
                iter(client.paginate(endpoint, params={"cursor_order": "reverse", "page_size": 1}, partial_ok=True)),
                [],
            )
        except Exception as exc:
            logger.debug("[pulse] country sample failed for opp %s: %s", opp_id, exc)
            continue
        if not page:
            continue
        point = parse_location(page[0].get("location"))
        if not point:
            continue
        country = country_for(*point)
        if not country:
            continue
        PulseOpportunity.objects.filter(opportunity_id=opp_id).update(country=country)
        PulseWork.objects.filter(opportunity_id=opp_id).exclude(country=country).update(country=country)
        resolved += 1

    if resolved:
        logger.info("[pulse] resolved country for %s historical opportunities by sampling", resolved)
    return resolved


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
# Works stream — the money/status spine, and where deep history lives
# ---------------------------------------------------------------------------

WORKS_ENDPOINT = "completed_works"

# `completed_works` omits `id` from the payload but the server still keysets on
# it — the `next` link carries `last_id=…`. Recovering it from there is what
# makes this stream incrementally tailable instead of a full re-read.
_LAST_ID_RE = re.compile(r"[?&]last_id=(\d+)")


def _last_id_from_next(next_url: str | None) -> int | None:
    if not next_url:
        return None
    match = _LAST_ID_RE.search(next_url)
    return int(match.group(1)) if match else None


def sync_works(client, cursor: PulseCursor, max_rows: int = 20000) -> dict:
    """Pull completed_works for one opportunity, forward from the cursor.

    At ~53 bytes/row gzipped this stream is cheap enough to carry full history —
    all 1.65M visits' worth is ~87 MB — which is why deep history lives here and
    not in the visit stream.

    Note on freshness: works *mutate* (pending → approved, payment_date filled
    in later). Tailing by id only sees new rows, so status changes on older work
    need a periodic full re-read. That is affordable precisely because the whole
    stream is 87 MB; ``resync_works`` does it by resetting the cursor.
    """
    opp = PulseOpportunity.objects.filter(opportunity_id=cursor.opportunity_id).first()
    endpoint = f"/export/opportunity/{cursor.opportunity_id}/{WORKS_ENDPOINT}/"
    params = {"cursor_order": "forward", "page_size": PAGE_SIZE}
    if cursor.last_id:
        params["last_id"] = cursor.last_id

    stored = seen = 0
    newest = cursor.newest_sync_ts
    last_id = cursor.last_id

    for page, next_url in _paginate_with_next(client, endpoint, params):
        if not page:
            continue
        stored += _store_works(page, opp)
        seen += len(page)
        last_id = _last_id_from_next(next_url) or last_id
        for row in page:
            ts = _parse_ts_safe(row.get("date_created"))
            if ts and (newest is None or ts > newest):
                newest = ts
        if seen >= max_rows:
            break  # declared via partial_ok

    cursor.last_id = last_id or cursor.last_id
    cursor.newest_sync_ts = newest
    cursor.last_polled_at = timezone.now()
    cursor.tier = tier_for(newest)
    cursor.consecutive_failures = 0
    cursor.last_error = ""
    cursor.save()

    return {"opportunity_id": cursor.opportunity_id, "seen": seen, "stored": stored}


def _paginate_with_next(client, endpoint, params):
    """Yield (page, next_url) so callers can recover the cursor from the link.

    ``ExportAPIClient.paginate`` hides the envelope, and for this endpoint the
    envelope is the only place the cursor exists.
    """
    from connect_labs.pulse.client import fetch_json

    query = "&".join(f"{k}={v}" for k, v in params.items())
    path = f"{endpoint}?{query}"
    while path:
        payload = fetch_json(client, path)
        next_url = payload.get("next")
        yield payload.get("results") or [], next_url
        if not next_url:
            return
        # The server emits http:// next links behind the proxy; keep the path.
        path = next_url.split(".com", 1)[1] if ".com" in next_url else None


def _parse_ts_safe(raw):
    from connect_labs.pulse.normalize import _parse_ts

    return _parse_ts(raw)


def _store_works(rows, opp) -> int:
    from connect_labs.pulse.normalize import work_to_fields

    fields = [f for f in (work_to_fields(row, opp) for row in rows) if f is not None]
    if not fields:
        return 0

    # Postgres rejects ON CONFLICT DO UPDATE when one statement proposes the
    # same key twice ("cannot affect row a second time"), which would fail the
    # whole batch rather than the duplicate. Collapse to the last occurrence —
    # rows arrive in ascending id order, so the last one is the freshest state.
    deduped: dict[str, dict] = {}
    for row in fields:
        deduped[row["work_key"]] = row
    fields = list(deduped.values())
    created = PulseWork.objects.bulk_create(
        [PulseWork(**f) for f in fields],
        # Works mutate; a re-seen row should update its status and payment date
        # rather than be discarded, or "approved" would never arrive.
        update_conflicts=True,
        update_fields=["status", "status_ts", "payment_date", "approved_count", "usd_to_worker", "usd_to_org"],
        unique_fields=["work_key"],
        batch_size=500,
    )
    return len(created)


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


def seed_cursor(client, cursor: PulseCursor) -> PulseCursor:
    """Point a brand-new cursor at *now* rather than at the beginning of time.

    Without this, a fresh cursor has ``last_id=None`` and the forward tail
    starts from the oldest visit an opportunity ever recorded — so a first run
    would drag full history (~7.5 GB gzipped across the estate) through
    the *live tail* path, which is capped and cadenced for small deltas.

    History is backfill's job, walking backwards on its own schedule. Tailing
    should only ever mean "what happened since I last looked". One 1-row
    reverse request costs ~0.45s and establishes that.
    """
    endpoint = f"/export/opportunity/{cursor.opportunity_id}/{VISITS_ENDPOINT}/"
    newest = None
    for page in client.paginate(endpoint, params={"cursor_order": "reverse", "page_size": 1}, partial_ok=True):
        if page:
            newest = page[0]
        break  # declared via partial_ok

    if newest is None:
        # Opportunity has never recorded a visit. Leave last_id unset so the
        # next poll re-checks cheaply rather than assuming an id.
        cursor.last_polled_at = timezone.now()
        cursor.tier = TIER_DORMANT
        cursor.save(update_fields=["last_polled_at", "tier"])
        return cursor

    from connect_labs.pulse.normalize import _parse_ts

    cursor.last_id = newest.get("id")
    cursor.backfill_oldest_id = newest.get("id")
    cursor.newest_sync_ts = _parse_ts(newest.get("date_created"))
    cursor.last_polled_at = timezone.now()
    cursor.tier = tier_for(cursor.newest_sync_ts)
    cursor.save()
    return cursor


def tail_visits(client, cursor: PulseCursor, max_rows: int = MAX_ROWS_PER_TAIL) -> dict:
    """Pull everything new for one opportunity since ``cursor.last_id``.

    Forward keyset order, so the API returns only rows created since the last
    poll. Capped per call; the cursor resumes on the next tick.

    A cursor that has never been positioned is seeded to the present first —
    see ``seed_cursor`` for why that matters.
    """
    if cursor.last_id is None and cursor.last_polled_at is None:
        seed_cursor(client, cursor)
        return {"opportunity_id": cursor.opportunity_id, "seen": 0, "stored": 0, "off_map": 0, "seeded": True}
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
    """Give every known opportunity a cursor on each stream."""
    created = 0
    for endpoint in (VISITS_ENDPOINT, WORKS_ENDPOINT):
        existing = set(PulseCursor.objects.filter(endpoint=endpoint).values_list("opportunity_id", flat=True))
        missing = [
            PulseCursor(opportunity_id=opp_id, endpoint=endpoint, tier=TIER_COLD)
            for opp_id in PulseOpportunity.objects.exclude(opportunity_id__in=existing).values_list(
                "opportunity_id", flat=True
            )
        ]
        if missing:
            PulseCursor.objects.bulk_create(missing, ignore_conflicts=True)
            created += len(missing)
    return created


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


def fold_events_to_grid(before=None, batch: int = 5000) -> dict:
    """Fold visit coordinates into ~1 km cells, then drop the visit rows.

    This is what lets the map show years of coverage while labs retains no deep
    archive of beneficiary-level records. A cell recording "412 services here"
    cannot be resolved back to a household, and it accumulates indefinitely —
    so the map gets *denser* over time even as the underlying rows expire.

    Idempotent: folding is keyed on the cell, and folded rows are deleted in the
    same transaction, so a retry cannot double-count.
    """
    from django.conf import settings

    retention_days = getattr(settings, "PULSE_EVENT_RETENTION_DAYS", 30)
    cutoff = before or (timezone.now() - timedelta(days=retention_days))

    folded = deleted = 0
    while True:
        rows = list(
            PulseEvent.objects.filter(field_ts__lt=cutoff).values(
                "id", "lat", "lon", "country", "field_ts", "service_slug", "status", "flagged"
            )[:batch]
        )
        if not rows:
            break

        cells: dict[tuple[int, int, str], dict] = {}
        for row in rows:
            if row["lat"] is None or row["lon"] is None:
                continue
            key = (int(round(row["lat"] * 100)), int(round(row["lon"] * 100)), row["service_slug"] or "")
            cell = cells.setdefault(
                key,
                {
                    "n": 0,
                    "approved_n": 0,
                    "flagged_n": 0,
                    "country": row["country"] or "",
                    "first": row["field_ts"],
                    "last": row["field_ts"],
                },
            )
            cell["n"] += 1
            if row["status"] == "approved":
                cell["approved_n"] += 1
            if row["flagged"]:
                cell["flagged_n"] += 1
            if row["field_ts"] < cell["first"]:
                cell["first"] = row["field_ts"]
            if row["field_ts"] > cell["last"]:
                cell["last"] = row["field_ts"]

        with transaction.atomic():
            for (lat_q, lon_q, service_slug), data in cells.items():
                existing = (
                    PulseGridCell.objects.select_for_update()
                    .filter(lat_q=lat_q, lon_q=lon_q, service_slug=service_slug)
                    .first()
                )
                if existing is None:
                    PulseGridCell.objects.create(
                        lat_q=lat_q,
                        lon_q=lon_q,
                        service_slug=service_slug,
                        country=data["country"],
                        n=data["n"],
                        approved_n=data["approved_n"],
                        flagged_n=data["flagged_n"],
                        first_ts=data["first"],
                        last_ts=data["last"],
                    )
                else:
                    existing.n += data["n"]
                    existing.approved_n += data["approved_n"]
                    existing.flagged_n += data["flagged_n"]
                    if existing.first_ts is None or data["first"] < existing.first_ts:
                        existing.first_ts = data["first"]
                    if existing.last_ts is None or data["last"] > existing.last_ts:
                        existing.last_ts = data["last"]
                    existing.save(update_fields=["n", "approved_n", "flagged_n", "first_ts", "last_ts"])
                folded += data["n"]

            deleted += PulseEvent.objects.filter(id__in=[r["id"] for r in rows]).delete()[0]

        if len(rows) < batch:
            break

    if folded or deleted:
        logger.info("[pulse] folded %s points into grid, dropped %s events", folded, deleted)
    return {"folded": folded, "deleted": deleted, "cutoff": cutoff.isoformat()}


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
