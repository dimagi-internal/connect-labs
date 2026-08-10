"""Cross-workspace opportunity dashboard, built on Pulse's local mirror.

Reads ``connect_labs.pulse.models`` directly rather than ``LabsRecordAPIClient``.
Pulse's poller account already ingests cross-org data with no per-request
opportunity/org scoping on read, so this sidesteps the membership-scoped
``data_export`` API entirely -- see docs/plans for the full rationale. This
module never writes to Pulse's tables; it is a read-only consumer.

Visit-count metrics (claimed/approved/pending/7-day, and the Visit Stats /
Visits-by-Country charts) are deliberately sourced from ``PulseRollup``, not
``PulseWork``: ``PulseWork.completed_count`` is a *payment-unit* count, which
undercounts real visits for programmes like KMC (works-per-visit ratio ~0.23
per Pulse's own design doc). Rollups are visit-derived and never pruned, so
they carry full history the way raw ``PulseEvent`` does not.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Min, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulseOrganization, PulseRollup, PulseWork
from connect_labs.pulse.normalize import service_label

# Ported verbatim from the Superset SQL's CASE statement that drives the real
# "Funder" column. The substring matches are intentionally broad (e.g. "gw",
# "fp", "c5") because that's what the source SQL does -- narrowing them would
# silently diverge from the numbers this is meant to match. Checked in order;
# first match wins, same as a SQL CASE.
_FUNDER_RULES = [
    (("givewell", "gw", "ppfn | sam", "chc vaccine coach", "mother baby wellness"), "GiveWell"),
    (("founders pledge", "fp", "sujukwa - child health campaign", "c5", "c6", "c7", "c17", "c18"), "Founders Pledge"),
    (("kangaroo mother care", "kmc"), "ECF"),
]
FUNDER_CHOICES = ["GiveWell", "Founders Pledge", "ECF", "Other Funder"]


def funder_for(opportunity_name: str) -> str:
    """Business-rule funder bucket for an opportunity, by name -- not an org attribute."""
    name = (opportunity_name or "").lower()
    for needles, funder in _FUNDER_RULES:
        if any(needle in name for needle in needles):
            return funder
    return "Other Funder"


def status_for(opp: PulseOpportunity) -> str:
    """Active means genuinely still running, not just Connect's raw flag.

    Mirrors the real SQL's ``active=true AND end_date >= today`` rather than
    passing through ``PulseOpportunity.is_active`` alone, which can stay True
    past an opportunity's own end date.
    """
    today = timezone.now().date()
    return "Active" if (opp.is_active and opp.end_date and opp.end_date >= today) else "Inactive"


def opportunity_filter_choices() -> dict:
    """Menus built from what's actually in the mirror, not a static list --
    same philosophy as ``_program_scope``'s menus in connect_labs/pulse/api.py,
    so a filter can never resolve to a guaranteed-empty screen."""
    delivery_types = sorted(
        PulseOpportunity.objects.exclude(service_slug="").values_list("service_slug", flat=True).distinct()
    )
    countries = sorted(PulseOpportunity.objects.exclude(country="").values_list("country", flat=True).distinct())
    return {
        "delivery_types": [{"slug": s, "label": service_label(s)} for s in delivery_types],
        "countries": countries,
        "funders": FUNDER_CHOICES,
    }


def _filtered_opportunities(*, delivery_type=None, country=None, funder=None):
    qs = PulseOpportunity.objects.all()
    if delivery_type:
        qs = qs.filter(service_slug=delivery_type)
    if country:
        qs = qs.filter(country=country)
    opps = list(qs)
    if funder:
        opps = [o for o in opps if funder_for(o.name) == funder]
    return opps


def _filtered_opp_ids(**filters) -> list[int]:
    return [o.opportunity_id for o in _filtered_opportunities(**filters)]


def opportunity_detail_rows(*, status=None, delivery_type=None, country=None, funder=None) -> list[dict]:
    """One row per opportunity -- the "Opportunity Info Detailed" table."""
    opps = _filtered_opportunities(delivery_type=delivery_type, country=country, funder=funder)
    opp_ids = [o.opportunity_id for o in opps]

    org_names = dict(PulseOrganization.objects.values_list("slug", "name"))

    flw_counts = {
        row["opportunity_id"]: row["n"]
        for row in PulseWork.objects.filter(opportunity_id__in=opp_ids)
        .exclude(worker_hash="")
        .values("opportunity_id")
        .annotate(n=Count("worker_hash", distinct=True))
    }

    rollup_totals = (
        PulseRollup.objects.filter(opportunity_id__in=opp_ids)
        .values("opportunity_id", "status")
        .annotate(n=Sum("n"))
    )
    claimed, approved, pending = {}, {}, {}
    for row in rollup_totals:
        oid, n = row["opportunity_id"], row["n"] or 0
        claimed[oid] = claimed.get(oid, 0) + n
        if row["status"] == "approved":
            approved[oid] = approved.get(oid, 0) + n
        elif row["status"] == "pending":
            pending[oid] = pending.get(oid, 0) + n

    since_7d = timezone.now() - timedelta(days=7)
    approved_7d = {
        row["opportunity_id"]: row["n"] or 0
        for row in PulseRollup.objects.filter(opportunity_id__in=opp_ids, status="approved", bucket_hour__gte=since_7d)
        .values("opportunity_id")
        .annotate(n=Sum("n"))
    }

    # "Paid" here is accrued-to-worker (PulseWork.usd_to_worker with a payment
    # date recorded), not a real opportunity_payment disbursement sum -- see
    # the Known-gaps note in the plan. Flagged in the template, not hidden.
    paid = {
        row["opportunity_id"]: row["usd"]
        for row in PulseWork.objects.filter(opportunity_id__in=opp_ids, payment_date__isnull=False)
        .values("opportunity_id")
        .annotate(usd=Sum("usd_to_worker"))
    }

    rows = []
    for opp in opps:
        row_status = status_for(opp)
        if status and status != "all" and row_status != status:
            continue
        rows.append(
            {
                "opportunity_id": opp.opportunity_id,
                "country": opp.country,
                "funder": funder_for(opp.name),
                "delivery_type": service_label(opp.service_slug),
                "llo": org_names.get(opp.org_slug) or opp.org_slug,
                "name": opp.name,
                "status": row_status,
                "start_date": None,  # not ingested -- see plan's "Known gaps"
                "end_date": opp.end_date,
                "flws": flw_counts.get(opp.opportunity_id, 0),
                "visits_claimed": claimed.get(opp.opportunity_id, 0),
                "visits_approved": approved.get(opp.opportunity_id, 0),
                "approved_7d": approved_7d.get(opp.opportunity_id, 0),
                "visits_pending": pending.get(opp.opportunity_id, 0),
                "amount_paid": float(paid.get(opp.opportunity_id) or 0),
            }
        )
    rows.sort(key=lambda r: -r["approved_7d"])
    return rows


def cohort_pivot(*, funder=None) -> dict:
    """Delivery Type × Country pivot: Visits / Visits Last 7 Days / Orgs bands."""
    opps = [o for o in PulseOpportunity.objects.exclude(country="").exclude(service_slug="")]
    if funder:
        opps = [o for o in opps if funder_for(o.name) == funder]
    opp_ids = [o.opportunity_id for o in opps]
    country_of = {o.opportunity_id: o.country for o in opps}
    service_of = {o.opportunity_id: o.service_slug for o in opps}
    org_of = {o.opportunity_id: o.org_slug for o in opps}

    since_7d = timezone.now() - timedelta(days=7)
    visits_rows = (
        PulseRollup.objects.filter(status="approved", opportunity_id__in=opp_ids)
        .values("opportunity_id")
        .annotate(n=Sum("n"))
    )
    visits_7d_rows = (
        PulseRollup.objects.filter(status="approved", opportunity_id__in=opp_ids, bucket_hour__gte=since_7d)
        .values("opportunity_id")
        .annotate(n=Sum("n"))
    )

    cells: dict[tuple[str, str], dict] = {}

    def cell(oid):
        key = (country_of[oid], service_of[oid])
        return cells.setdefault(key, {"visits": 0, "visits_7d": 0, "orgs": set()})

    for row in visits_rows:
        oid = row["opportunity_id"]
        if oid not in country_of:
            continue
        c = cell(oid)
        c["visits"] += row["n"] or 0
        if org_of.get(oid):
            c["orgs"].add(org_of[oid])
    for row in visits_7d_rows:
        oid = row["opportunity_id"]
        if oid not in country_of:
            continue
        cell(oid)["visits_7d"] += row["n"] or 0

    countries = sorted({k[0] for k in cells})
    services = sorted({k[1] for k in cells})
    empty = lambda: {"visits": 0, "visits_7d": 0, "orgs": set()}  # noqa: E731
    grid = {c: {s: cells.get((c, s), empty()) for s in services} for c in countries}

    def orgs_union(cell_list):
        u: set = set()
        for cc in cell_list:
            u |= cc["orgs"]
        return len(u)

    row_subtotals = {
        c: {
            "visits": sum(grid[c][s]["visits"] for s in services),
            "visits_7d": sum(grid[c][s]["visits_7d"] for s in services),
            "orgs": orgs_union(grid[c].values()),
        }
        for c in countries
    }
    col_totals = {
        s: {
            "visits": sum(grid[c][s]["visits"] for c in countries),
            "visits_7d": sum(grid[c][s]["visits_7d"] for c in countries),
            "orgs": orgs_union(grid[c][s] for c in countries),
        }
        for s in services
    }
    grand_total = {
        "visits": sum(col_totals[s]["visits"] for s in services),
        "visits_7d": sum(col_totals[s]["visits_7d"] for s in services),
        "orgs": orgs_union(grid[c][s] for c in countries for s in services),
    }

    # Flattened into parallel lists (one row per country, cells in the same
    # order as `services`) rather than a dict keyed by variable -- Django
    # templates can't do `dict[var]` lookups, and this needs no custom filter.
    rows = [
        {
            "country": c,
            "cells": [
                {
                    "visits": grid[c][s]["visits"],
                    "visits_7d": grid[c][s]["visits_7d"],
                    "orgs_count": len(grid[c][s]["orgs"]),
                }
                for s in services
            ],
            "subtotal": row_subtotals[c],
        }
        for c in countries
    ]
    col_totals_list = [col_totals[s] for s in services]

    return {
        "services": [{"slug": s, "label": service_label(s)} for s in services],
        "rows": rows,
        "col_totals": col_totals_list,
        "grand_total": grand_total,
    }


def running_visit_total(**filters) -> list[dict]:
    """Cumulative approved visits over all time -- exact, PulseRollup never prunes."""
    opp_ids = _filtered_opp_ids(**filters)
    daily = (
        PulseRollup.objects.filter(status="approved", opportunity_id__in=opp_ids)
        .annotate(day=TruncDate("bucket_hour"))
        .values("day")
        .annotate(n=Sum("n"))
        .order_by("day")
    )
    points, running = [], 0
    for row in daily:
        running += row["n"] or 0
        points.append({"t": row["day"].isoformat(), "value": running})
    return points


def running_user_total(**filters) -> list[dict]:
    """Cumulative distinct-FLW roster growth, by first-seen date in PulseWork.

    Exact and full-history, unlike daily active users below (which needs
    visit-level data that's pruned after PULSE_EVENT_RETENTION_DAYS).
    """
    opp_ids = _filtered_opp_ids(**filters)
    first_seen = (
        PulseWork.objects.filter(opportunity_id__in=opp_ids)
        .exclude(worker_hash="")
        .values("worker_hash")
        .annotate(first=Min("created_ts"))
    )
    by_day: dict[str, int] = {}
    for row in first_seen:
        d = row["first"].date().isoformat()
        by_day[d] = by_day.get(d, 0) + 1
    points, running = [], 0
    for d in sorted(by_day):
        running += by_day[d]
        points.append({"t": d, "value": running})
    return points


def daily_visits_and_users(**filters) -> list[dict]:
    """Daily approved visits (full history) + daily unique FLWs (last ~30d only).

    The users line can't extend further back without visit-level PulseEvent
    rows, which are pruned -- callers must render missing days as absent, not
    zero, per Pulse's own "no data != zero" rule for money.
    """
    opp_ids = _filtered_opp_ids(**filters)
    daily_visits = {
        row["day"].isoformat(): row["n"] or 0
        for row in PulseRollup.objects.filter(status="approved", opportunity_id__in=opp_ids)
        .annotate(day=TruncDate("bucket_hour"))
        .values("day")
        .annotate(n=Sum("n"))
    }
    retention_days = getattr(settings, "PULSE_EVENT_RETENTION_DAYS", 30)
    cutoff = timezone.now() - timedelta(days=retention_days)
    daily_users = {
        row["day"].isoformat(): row["n"]
        for row in PulseEvent.objects.filter(opportunity_id__in=opp_ids, field_ts__gte=cutoff)
        .exclude(worker_hash="")
        .annotate(day=TruncDate("field_ts"))
        .values("day")
        .annotate(n=Count("worker_hash", distinct=True))
    }
    return [
        {"t": d, "approved_visits": daily_visits.get(d, 0), "unique_users": daily_users.get(d)}
        for d in sorted(daily_visits)
    ]


def monthly_visits_by_country(*, top_n=6, **filters) -> dict:
    """Monthly stacked totals; the long tail folds into "Other" rather than
    each country competing for a low-contrast hue."""
    opp_ids = _filtered_opp_ids(**filters)
    country_of = dict(PulseOpportunity.objects.filter(opportunity_id__in=opp_ids).values_list("opportunity_id", "country"))

    rows = (
        PulseRollup.objects.filter(status="approved", opportunity_id__in=opp_ids)
        .annotate(month=TruncMonth("bucket_hour"))
        .values("month", "opportunity_id")
        .annotate(n=Sum("n"))
    )

    totals_by_country: dict[str, int] = {}
    monthly: dict[str, dict[str, int]] = {}
    for row in rows:
        country = country_of.get(row["opportunity_id"]) or ""
        if not country:
            continue
        n = row["n"] or 0
        totals_by_country[country] = totals_by_country.get(country, 0) + n
        m = row["month"].date().isoformat()
        monthly.setdefault(m, {})
        monthly[m][country] = monthly[m].get(country, 0) + n

    top_countries = [c for c, _ in sorted(totals_by_country.items(), key=lambda kv: -kv[1])[:top_n]]
    series = []
    for m in sorted(monthly):
        values = {c: monthly[m].get(c, 0) for c in top_countries}
        values["Other"] = sum(v for c, v in monthly[m].items() if c not in top_countries)
        series.append({"month": m, "values": values})
    return {"countries": top_countries + ["Other"], "series": series}
