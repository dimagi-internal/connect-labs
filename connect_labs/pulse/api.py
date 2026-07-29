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
    PulseRollup,
    PulseScalar,
    PulseWork,
)
from connect_labs.pulse.normalize import COUNTRY_NAMES, FLAG_LABELS, SERVICE_LABELS

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


class SummaryView(View):
    """Scalars, rollups, scope and ingest health — everything a card needs at load."""

    def get(self, request):
        window_hours = min(int(request.GET.get("hours", 72)), 24 * 30)
        since = timezone.now() - timedelta(hours=window_hours)

        scope = PulseScalar.objects.filter(key="scope").first()
        off_map = PulseScalar.objects.filter(key="off_map_points").first()

        agg = PulseEvent.objects.aggregate(
            n=Count("id"),
            flagged=Count("id", filter=Q(flagged=True)),
            usd=Sum("usd_to_worker"),
        )

        by_status = {
            row["status"]: row["n"]
            for row in PulseEvent.objects.values("status").annotate(n=Count("id")).order_by("-n")
        }
        by_flag = {
            row["flag_type"]: row["n"]
            for row in PulseEvent.objects.exclude(flag_type="")
            .values("flag_type")
            .annotate(n=Count("id"))
            .order_by("-n")
        }
        by_country = {
            row["country"]: row["n"]
            for row in PulseEvent.objects.exclude(country="").values("country").annotate(n=Count("id")).order_by("-n")
        }

        hourly = list(
            PulseRollup.objects.filter(bucket_hour__gte=since)
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
            for o in PulseOpportunity.objects.all().order_by("-lifetime_visit_count")[:600]
        ]

        # Money comes from the works spine, which carries full history at ~53
        # B/row — not from visits, which are only a rolling window.
        money = PulseWork.objects.aggregate(
            to_workers=Sum("usd_to_worker"),
            to_orgs=Sum("usd_to_org"),
            works=Count("id"),
        )
        approved_works = PulseWork.objects.filter(status="approved").count()
        by_work_status = {
            row["status"]: row["n"]
            for row in PulseWork.objects.values("status").annotate(n=Count("id")).order_by("-n")
        }
        money_by_country = [
            {
                "country": row["country"],
                "name": COUNTRY_NAMES.get(row["country"], row["country"]),
                "works": row["n"],
                "usd": float(row["usd"] or 0),
            }
            for row in PulseWork.objects.exclude(country="")
            .values("country")
            .annotate(n=Count("id"), usd=Sum("usd_to_worker"))
            .order_by("-usd")
        ]
        # Rate per service is VOLUME-WEIGHTED, computed from money actually
        # accrued over approved work. Averaging each opportunity's own rate
        # instead lets a two-row test opportunity count as much as a
        # 106,719-work programme -- which put "Malaria rapid test" at $17.03
        # when the real programme pays $1.08.
        money_by_service = [
            {
                "service": row["service_slug"],
                "name": SERVICE_LABELS.get(row["service_slug"], row["service_slug"] or "Service delivery"),
                "works": row["n"],
                "approved": row["approved"],
                "usd": float(row["usd"] or 0),
                "rate": (float(row["usd"] or 0) / row["approved"]) if row["approved"] else None,
            }
            for row in PulseWork.objects.exclude(service_slug="")
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
                "scope": scope.value if scope else {},
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
        cells = PulseGridCell.objects.all().order_by("-n")[:limit]

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
            }
        )


class EventsView(View):
    """Live tail. ``since`` is a visit id, matching Connect's own cursor semantics."""

    def get(self, request):
        since = request.GET.get("since")
        limit = min(int(request.GET.get("limit", 500)), MAX_EVENTS)

        qs = PulseEvent.objects.all().order_by("connect_visit_id")
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

        rows = list(PulseEvent.objects.filter(field_ts__gte=start, field_ts__lte=end).order_by("field_ts")[:limit])

        return JsonResponse(
            {
                "fields": EVENT_FIELDS,
                "events": [_event_row(e) for e in rows],
                "window": {"from": start.isoformat(), "to": end.isoformat(), "hours": hours, "basis": "field_ts"},
                "truncated": len(rows) >= limit,
                "ingest": _ingest_state(),
            }
        )
