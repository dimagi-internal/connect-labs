"""Read API — the only surface a Pulse card ever talks to.

Cards never reach Connect and never touch the ORM directly. That separation is
what makes "try a bunch of layouts" cheap: a new card is a renderer over these
three responses.

The one rule enforced *here* rather than in the frontend: **the server decides
whether LIVE is honest.** A page that decides for itself will happily show a
green badge over data that stopped arriving on Tuesday. ``summary`` returns
``live_ok`` and the client is not allowed to override it.
"""

from __future__ import annotations

import math
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views import View

from connect_labs.pulse.client import PulseAuthError, get_poller_user
from connect_labs.pulse.models import (
    TIER_HOT,
    TIER_INTERVALS_SECONDS,
    PulseEvent,
    PulseGridCell,
    PulseIngestHealth,
    PulseOpportunity,
    PulseProgram,
    PulseRollup,
    PulseScalar,
    PulseWork,
)
from connect_labs.pulse.normalize import COUNTRY_NAMES, FLAG_LABELS, SERVICE_LABELS, service_label

MAX_EVENTS = 2000
DEFAULT_REPLAY_HOURS = 48


def _ingest_state() -> dict:
    """Whether anything on screen can honestly be called live."""
    rows = list(PulseIngestHealth.objects.all())
    healthy = bool(rows) and all(r.is_healthy for r in rows)
    last_success = max((r.last_success_at for r in rows if r.last_success_at), default=None)

    staleness = None
    if last_success:
        staleness = int((timezone.now() - last_success).total_seconds())

    if not rows:
        message = "Ingest has never run."
    elif healthy:
        message = ""
    elif last_success is None:
        message = "Ingest has never succeeded — check the poller's Connect token."
    else:
        message = f"No successful ingest for {staleness // 60} minutes — showing stored data, not live."

    # Surface WHO we poll as. Scope follows that account's org membership, so a
    # wrong poller silently rescales every figure on the screen -- it happened
    # on the first prod deploy and nothing errored. Making it visible means the
    # next person can spot it from the page instead of from the numbers.
    #
    # An unresolvable poller also forfeits the LIVE badge immediately rather
    # than waiting for staleness to accumulate: no poller means no further
    # ingest, so whatever is on screen is already the last of it.
    try:
        poller = get_poller_user().username
        poller_error = ""
    except PulseAuthError as exc:
        poller = ""
        poller_error = str(exc).split("\n")[0]
        healthy = False
        message = message or "No Pulse poller configured — ingest cannot run."

    return {
        "live_ok": healthy,
        "poller": poller,
        "poller_error": poller_error,
        "last_success_at": last_success.isoformat() if last_success else None,
        "staleness_seconds": staleness,
        "hot_interval_seconds": TIER_INTERVALS_SECONDS[TIER_HOT],
        "message": message,
        "tiers": [
            {
                "tier": r.tier,
                "healthy": r.is_healthy,
                "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
                "consecutive_failures": r.consecutive_failures,
                "last_error": r.last_error[:300],
            }
            for r in rows
        ],
    }


def _event_row(e: PulseEvent) -> list:
    """Compact positional encoding — these ship thousands at a time."""
    return [
        e.connect_visit_id,
        int(e.field_ts.timestamp()),
        int(e.sync_ts.timestamp()),
        round(e.lat, 4) if e.lat is not None else None,
        round(e.lon, 4) if e.lon is not None else None,
        e.opportunity_id,
        e.status,
        e.flag_type or None,
        e.country or None,
        e.service_slug or None,
        e.worker_hash[:6] or None,
        float(e.usd_to_worker) if e.usd_to_worker is not None else None,
    ]


EVENT_FIELDS = [
    "visit_id",
    "field_ts",
    "sync_ts",
    "lat",
    "lon",
    "opportunity_id",
    "status",
    "flag_type",
    "country",
    "service_slug",
    "worker",
    "usd",
]


def _program_scope(request):
    """Resolve ``?program=<id>`` into the querysets every card is drawn from.

    Filtering has to happen HERE rather than in the page: the headline figures
    are server-side aggregates over the whole estate, so a client that hid rows
    would leave "1.6M services" sitting above a filtered map. Every count on
    screen has to be recomputed for the selected programme or none of them can
    be trusted.

    ``PulseEvent``, ``PulseWork`` and ``PulseOpportunity`` all carry an indexed
    ``program_id``. ``PulseRollup`` keys on opportunity, so it filters through
    one. ``PulseGridCell`` carries neither -- see ``grid_service`` below.
    """
    raw = (request.GET.get("program") or "").strip()
    program = None
    if raw:
        try:
            program = PulseProgram.objects.filter(program_id=int(raw)).first()
        except ValueError:
            program = None

    events = PulseEvent.objects.all()
    works = PulseWork.objects.all()
    opps = PulseOpportunity.objects.all()
    rollups = PulseRollup.objects.all()
    grid_service = None

    if program is not None:
        pid = program.program_id
        events = events.filter(program_id=pid)
        works = works.filter(program_id=pid)
        opps = opps.filter(program_id=pid)
        rollups = rollups.filter(
            opportunity_id__in=PulseOpportunity.objects.filter(program_id=pid).values("opportunity_id")
        )
        # Historic density cells predate programme attribution and their source
        # events are deleted, so they cannot be resolved to a programme. Their
        # delivery type survives on service_slug, which is the closest honest
        # filter: the map narrows to the right kind of work even where it
        # cannot narrow to the exact programme.
        grid_service = program.delivery_type or None

    return {
        "program": program,
        "events": events,
        "works": works,
        "opps": opps,
        "rollups": rollups,
        "grid_service": grid_service,
    }


def _scope_for(sc):
    """Headline scale — recomputed when a programme is selected.

    Unfiltered this is the stored cheap-tier scalar, which counts the whole
    estate for free. Filtered it MUST be recomputed: leaving "498 opportunities
    / 1.65M services" above a single programme's map is the same defect as the
    inferred poller and the head-sliced replay -- a true number answering a
    question nobody asked.
    """
    if sc["program"] is None:
        row = PulseScalar.objects.filter(key="scope").first()
        return row.value if row else {}

    opps = sc["opps"]
    agg = opps.aggregate(n=Count("id"), visits=Sum("lifetime_visit_count"))
    return {
        "opportunities": agg["n"] or 0,
        "active_opportunities": opps.filter(is_active=True).count(),
        "lifetime_visits": agg["visits"] or 0,
        "programs": 1,
        "orgs": opps.exclude(org_slug="").values("org_slug").distinct().count(),
    }


def _program_menu():
    """Programmes offered in the filter.

    Excludes internal scaffolding by name (``is_test``) and anything that has
    never received a visit. Both matter: the test programmes carry real volume
    -- one has 9,035 visits -- so they cannot be filtered out by size, and the
    empty ones would pad the menu with dozens of entries that resolve to a
    blank screen.
    """
    rows = (
        PulseOpportunity.objects.exclude(program_id=None)
        .values("program_id")
        .annotate(visits=Sum("lifetime_visit_count"), opps=Count("id"))
        .filter(visits__gt=0)
    )
    by_id = {r["program_id"]: r for r in rows}
    # Stored events are the retention window, not all history. Most of the
    # largest programmes by lifetime volume finished months ago, so ordering on
    # lifetime alone puts a blank map at the top of the menu -- picking "[Batch
    # 04] Dimagi-GiveWell CHC Program", 547,474 services, currently yields no
    # points at all because none of them are recent.
    programs = PulseProgram.objects.filter(program_id__in=by_id, is_test=False)
    recent = {
        r["program_id"]: r["n"]
        for r in PulseEvent.objects.exclude(program_id=None).values("program_id").annotate(n=Count("id"))
    }
    menu = [
        {
            "id": p.program_id,
            "name": p.name or f"Programme {p.program_id}",
            "delivery_type": p.delivery_type,
            "service_label": service_label(p.delivery_type),
            "opportunities": by_id[p.program_id]["opps"],
            "visits": by_id[p.program_id]["visits"],
            # Lets the menu say which programmes are currently delivering
            # rather than letting someone discover it by selecting one.
            "recent_events": recent.get(p.program_id, 0),
        }
        for p in programs
    ]
    # Currently-delivering programmes first, each group by lifetime volume.
    menu.sort(key=lambda m: (-(1 if m["recent_events"] else 0), -m["visits"]))
    return menu


class SummaryView(View):
    """Scalars, rollups, scope and ingest health — everything a card needs at load."""

    def get(self, request):
        window_hours = min(int(request.GET.get("hours", 72)), 24 * 30)
        since = timezone.now() - timedelta(hours=window_hours)

        sc = _program_scope(request)
        program = sc["program"]
        off_map = PulseScalar.objects.filter(key="off_map_points").first()

        agg = sc["events"].aggregate(
            n=Count("id"),
            flagged=Count("id", filter=Q(flagged=True)),
            usd=Sum("usd_to_worker"),
        )

        by_status = {
            row["status"]: row["n"] for row in sc["events"].values("status").annotate(n=Count("id")).order_by("-n")
        }
        by_flag = {
            row["flag_type"]: row["n"]
            for row in sc["events"].exclude(flag_type="").values("flag_type").annotate(n=Count("id")).order_by("-n")
        }
        by_country = {
            row["country"]: row["n"]
            for row in sc["events"].exclude(country="").values("country").annotate(n=Count("id")).order_by("-n")
        }

        hourly = list(
            sc["rollups"]
            .filter(bucket_hour__gte=since)
            .values("bucket_hour")
            .annotate(n=Sum("n"), usd=Sum("usd"))
            .order_by("bucket_hour")
        )

        opps = [
            {
                "id": o.opportunity_id,
                "name": o.name,
                "org": o.org_slug,
                "program_id": o.program_id,
                "service": o.service_slug,
                "active": o.is_active,
                "lifetime_visits": o.lifetime_visit_count,
                "usd_per_service": float(o.usd_per_service) if o.usd_per_service else None,
                "currency": o.currency,
            }
            for o in sc["opps"].order_by("-lifetime_visit_count")[:600]
        ]

        # Money comes from the works spine, which carries full history at ~53
        # B/row — not from visits, which are only a rolling window.
        money = sc["works"].aggregate(
            to_workers=Sum("usd_to_worker"),
            to_orgs=Sum("usd_to_org"),
            works=Count("id"),
        )
        approved_works = sc["works"].filter(status="approved").count()
        by_work_status = {
            row["status"]: row["n"] for row in sc["works"].values("status").annotate(n=Count("id")).order_by("-n")
        }
        money_by_country = [
            {
                "country": row["country"],
                "name": COUNTRY_NAMES.get(row["country"], row["country"]),
                "works": row["n"],
                "usd": float(row["usd"] or 0),
            }
            for row in sc["works"]
            .exclude(country="")
            .values("country")
            .annotate(n=Count("id"), usd=Sum("usd_to_worker"))
            .order_by("-usd")
        ]
        # Country comes from the opportunity, and Connect leaves it blank on
        # most of them -- `pulse_backfill --countries` resolves it by sampling a
        # visit's GPS, but any opportunity with no ingested visit stays blank.
        # The excluded rows used to just vanish, so the panel rendered
        # full-width bars over 6% of the money and read as the whole picture.
        # Report the remainder instead: a consumer can then say what the
        # breakdown covers rather than implying it covers everything.
        country_usd = sum(r["usd"] for r in money_by_country)
        country_works = sum(r["works"] for r in money_by_country)
        money_country_unattributed = {
            "works": (money["works"] or 0) - country_works,
            "usd": float(money["to_workers"] or 0) - country_usd,
            "usd_share": (country_usd / float(money["to_workers"]) if money["to_workers"] else 0),
        }
        # Rate per service is VOLUME-WEIGHTED, computed from money actually
        # accrued over approved work. Averaging each opportunity's own rate
        # instead lets a two-row test opportunity count as much as a
        # 106,719-work programme -- which put "Malaria rapid test" at $17.03
        # when the real programme pays $1.08.
        money_by_service = [
            {
                "service": row["service_slug"],
                "name": service_label(row["service_slug"]),
                "works": row["n"],
                "approved": row["approved"],
                "usd": float(row["usd"] or 0),
                "rate": (float(row["usd"] or 0) / row["approved"]) if row["approved"] else None,
            }
            for row in sc["works"]
            .exclude(service_slug="")
            .values("service_slug")
            .annotate(
                n=Count("id"),
                approved=Count("id", filter=Q(status="approved")),
                usd=Sum("usd_to_worker"),
            )
            .order_by("-usd")[:12]
        ]

        return JsonResponse(
            {
                "generated_at": timezone.now().isoformat(),
                "ingest": _ingest_state(),
                "scope": _scope_for(sc),
                "program": (
                    {
                        "id": program.program_id,
                        "name": program.name,
                        "delivery_type": program.delivery_type,
                        "service_label": service_label(program.delivery_type),
                    }
                    if program
                    else None
                ),
                "programs": _program_menu(),
                "money": {
                    "to_workers": float(money["to_workers"] or 0),
                    "to_orgs": float(money["to_orgs"] or 0),
                    "works": money["works"] or 0,
                    "approved_works": approved_works,
                    "usd_per_approved_work": (
                        float(money["to_workers"] or 0) / approved_works if approved_works else 0
                    ),
                    "by_work_status": by_work_status,
                    "by_country": money_by_country,
                    "by_country_unattributed": money_country_unattributed,
                    "by_service": money_by_service,
                },
                "stored": {
                    "events": agg["n"] or 0,
                    "flagged": agg["flagged"] or 0,
                    "usd_to_workers": float(agg["usd"] or 0),
                    "off_map_points": (off_map.value.get("count", 0) if off_map else 0),
                },
                "by_status": by_status,
                "by_flag": by_flag,
                "by_country": by_country,
                "hourly": [
                    {"t": int(r["bucket_hour"].timestamp()), "n": r["n"], "usd": float(r["usd"] or 0)} for r in hourly
                ],
                "opportunities": opps,
                "labels": {
                    "countries": COUNTRY_NAMES,
                    "flags": FLAG_LABELS,
                    "services": SERVICE_LABELS,
                },
            }
        )


class GridView(View):
    """The historical map layer: anonymous ~1 km cells.

    This is what the map is *made of*. Live events light up on top of it, but
    the accumulated shape of where Connect works comes from here — cells that
    outlive the visit rows that produced them, and so keep getting denser while
    labs holds no deep beneficiary-level archive.
    """

    def get(self, request):
        limit = min(int(request.GET.get("limit", 20000)), 60000)
        sc = _program_scope(request)
        cells = PulseGridCell.objects.all()
        if sc["grid_service"]:
            cells = cells.filter(service_slug=sc["grid_service"])
        cells = cells.order_by("-n")[:limit]

        rows = [
            [c.lat_q, c.lon_q, c.n, c.approved_n, c.flagged_n, c.country or None, c.service_slug or None]
            for c in cells
        ]
        return JsonResponse(
            {
                "fields": ["lat_q", "lon_q", "n", "approved_n", "flagged_n", "country", "service"],
                # Cells are quantised to 1/100 degree; divide to get coordinates.
                "quantum": 100,
                "cells": rows,
                "total_points": sum(r[2] for r in rows),
                "truncated": len(rows) >= limit,
                # Cells carry a delivery type, not a programme id: they are
                # built by folding visit rows that are then deleted, so a cell
                # predating this cannot be resolved to the programme that made
                # it. Filtering narrows to the right KIND of work; the caller
                # is told so rather than being left to assume exactness.
                "filtered_by": sc["grid_service"],
            }
        )


class EventsView(View):
    """Live tail. ``since`` is a visit id, matching Connect's own cursor semantics."""

    def get(self, request):
        since = request.GET.get("since")
        limit = min(int(request.GET.get("limit", 500)), MAX_EVENTS)

        qs = _program_scope(request)["events"].order_by("connect_visit_id")
        if since:
            qs = qs.filter(connect_visit_id__gt=int(since))
        else:
            qs = qs.order_by("-connect_visit_id")[:limit]
            rows = sorted(qs, key=lambda e: e.connect_visit_id)
            return JsonResponse(_events_payload(rows))

        return JsonResponse(_events_payload(list(qs[:limit])))


def _events_payload(rows) -> dict:
    return {
        "fields": EVENT_FIELDS,
        "events": [_event_row(e) for e in rows],
        "cursor": rows[-1].connect_visit_id if rows else None,
        "ingest": _ingest_state(),
    }


class ReplayView(View):
    """A bounded window for replay.

    Windowed on **field_ts**, not sync_ts. Replay is paced on field time, so
    selecting on arrival time would make a window span far wider than its label
    claims — the prototype's "last 48h" actually covered nine days because of
    the offline-sync tail.
    """

    def get(self, request):
        hours = min(int(request.GET.get("hours", DEFAULT_REPLAY_HOURS)), 24 * 14)
        limit = min(int(request.GET.get("limit", MAX_EVENTS)), MAX_EVENTS)
        end = timezone.now()
        start = end - timedelta(hours=hours)

        sc = _program_scope(request)
        qs = sc["events"].filter(field_ts__gte=start, field_ts__lte=end)

        # Sample ACROSS the window rather than taking its head. Slicing an
        # ordered queryset returns the chronologically first `limit` rows, which
        # makes a longer window show *less*: asking for 336h returned 2000 rows
        # spanning 12.8h -- 3.8% of what was requested -- and 94% of them
        # Nigeria, because whichever programme submitted first monopolises the
        # head. Four of the eight countries with delivery never appeared at all,
        # so the map read as one country however wide the window.
        #
        # Strided on rank in field-time order, so the sample is uniform over the
        # window and fills the point budget exactly. Striding on `visit_id % n`
        # instead is a one-liner but under-delivers whenever ids are sparse
        # relative to the stride -- 12 events at limit 3 yields 2 -- which is the
        # small-window case a country- or day-filtered view actually hits.
        # ROW_NUMBER is exact at every size. Deterministic, so consecutive polls
        # agree and points do not appear and vanish between frames.
        total = qs.count()
        stride = max(1, math.ceil(total / limit)) if total > limit else 1
        if stride > 1:
            # Rank inside the SAME filtered set, or the stride would skip over
            # rows excluded by the programme filter and return far fewer than
            # the budget.
            table = PulseEvent._meta.db_table
            where = "field_ts >= %s AND field_ts <= %s"
            params = [start, end]
            if sc["program"] is not None:
                where += " AND program_id = %s"
                params.append(sc["program"].program_id)
            rows = list(
                PulseEvent.objects.raw(
                    f"""SELECT * FROM (
                            SELECT *, ROW_NUMBER() OVER (ORDER BY field_ts, id) AS rn
                            FROM {table} WHERE {where}
                        ) ranked
                        WHERE (rn - 1) %% %s = 0
                        ORDER BY field_ts, id
                        LIMIT %s""",
                    params + [stride, limit],
                )
            )
        else:
            rows = list(qs.order_by("field_ts")[:limit])

        return JsonResponse(
            {
                "fields": EVENT_FIELDS,
                "events": [_event_row(e) for e in rows],
                "window": {"from": start.isoformat(), "to": end.isoformat(), "hours": hours, "basis": "field_ts"},
                # A sample is not a truncation, and conflating them would let the
                # display claim completeness it does not have. Both are declared.
                "truncated": len(rows) >= limit and stride == 1,
                "sampled": stride > 1,
                "sample_stride": stride,
                "matched": total,
                "ingest": _ingest_state(),
            }
        )
