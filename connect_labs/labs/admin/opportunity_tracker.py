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

Callers filter once via ``filtered_opportunities()`` and pass the resulting
opportunities (or their ids) into the functions below, rather than each
function re-deriving its own filtered set. Two tabs on the page (Opportunities
and Visit Stats) share the same delivery_type/country/funder filter set, so
computing it once avoids repeating the same query and the same funder_for()
name-scan per section -- and it's what makes the Cohort Pivot honor the same
filters as the detail table beside it, instead of drifting out of sync.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.db.models import Count, Min, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulseOrganization, PulseRollup, PulseWork
from connect_labs.pulse.normalize import COUNTRY_NAMES, service_label


def country_label(code: str) -> str:
    """Display name for a country code, matching connect_labs/pulse/api.py's
    own country menus/labels -- falls back to the code itself for one Pulse
    hasn't named (never invents a label the way an unconfirmed service slug
    would)."""
    return COUNTRY_NAMES.get(code, code) if code else ""


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

    NOTE: the same rule is reimplemented separately in
    ``AppDownloaderDataAccess.get_active_opportunities`` (app_data_access.py)
    against the raw API dict shape rather than this model. Known duplication,
    not unified here to avoid touching an unrelated data path under this
    change -- if "active" ever grows a grace period or similar, update both.
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
        "countries": [{"code": c, "label": country_label(c)} for c in countries],
        "funders": FUNDER_CHOICES,
    }


def filtered_opportunities(*, delivery_type=None, country=None, funder=None) -> list[PulseOpportunity]:
    """The one place delivery_type/country/funder filtering happens.

    Call once per distinct filter combination and pass the result into the
    functions below -- they take opportunities/ids directly rather than
    filter kwargs, so nothing re-queries or re-scans funder_for() a second time
    for the same combination.
    """
    qs = PulseOpportunity.objects.all()
    if delivery_type:
        qs = qs.filter(service_slug=delivery_type)
    if country:
        qs = qs.filter(country=country)
    opps = list(qs)
    if funder:
        opps = [o for o in opps if funder_for(o.name) == funder]
    return opps


def opportunity_detail_rows(opportunities: list[PulseOpportunity]) -> list[dict]:
    """One row per opportunity -- the "Opportunity Info Detailed" table."""
    opp_ids = [o.opportunity_id for o in opportunities]

    org_slugs = {o.org_slug for o in opportunities if o.org_slug}
    org_names = dict(PulseOrganization.objects.filter(slug__in=org_slugs).values_list("slug", "name"))

    # One grouped scan of PulseWork with conditional aggregates, instead of
    # two separate ones for FLW count and paid amount -- both need the same
    # opportunity_id grouping, just different filter conditions per column.
    work_totals = (
        PulseWork.objects.filter(opportunity_id__in=opp_ids)
        .values("opportunity_id")
        .annotate(
            flws=Count("worker_hash", distinct=True, filter=~Q(worker_hash="")),
            # "Paid" here is accrued-to-worker (usd_to_worker with a payment
            # date recorded), not a real opportunity_payment disbursement sum
            # -- see the Known-gaps note in the plan. Flagged in the
            # template, not hidden.
            #
            # Known narrow edge case, not handled: usd_to_worker and
            # payment_date are independently nullable, so a row could have a
            # payment_date but no dollar figure recorded. Sum() over an
            # all-such group returns None, which reads as a real $0 below
            # rather than "amount unknown" -- in practice every payment-dated
            # row also carries an amount, so this is deliberately not
            # special-cased further.
            usd=Sum("usd_to_worker", filter=Q(payment_date__isnull=False)),
            has_payment=Count("id", filter=Q(payment_date__isnull=False)),
        )
    )
    flw_counts, paid, has_payment_data = {}, {}, set()
    for row in work_totals:
        oid = row["opportunity_id"]
        flw_counts[oid] = row["flws"]
        paid[oid] = row["usd"]
        if row["has_payment"]:
            has_payment_data.add(oid)

    # Likewise one grouped scan of PulseRollup with conditional Sums for
    # claimed/approved/pending/approved-7d, instead of a by-status query plus
    # a separate 7-day-windowed one.
    since_7d = timezone.now() - timedelta(days=7)
    rollup_totals = (
        PulseRollup.objects.filter(opportunity_id__in=opp_ids)
        .values("opportunity_id")
        .annotate(
            claimed=Sum("n"),
            approved=Sum("n", filter=Q(status="approved")),
            pending=Sum("n", filter=Q(status="pending")),
            approved_7d=Sum("n", filter=Q(status="approved", bucket_hour__gte=since_7d)),
        )
    )
    claimed, approved, pending, approved_7d = {}, {}, {}, {}
    for row in rollup_totals:
        oid = row["opportunity_id"]
        claimed[oid] = row["claimed"] or 0
        approved[oid] = row["approved"] or 0
        pending[oid] = row["pending"] or 0
        approved_7d[oid] = row["approved_7d"] or 0

    rows = [
        {
            "opportunity_id": opp.opportunity_id,
            "country": country_label(opp.country),
            "funder": funder_for(opp.name),
            "delivery_type": service_label(opp.service_slug),
            "llo": org_names.get(opp.org_slug) or opp.org_slug,
            "name": opp.name,
            "status": status_for(opp),
            "start_date": None,  # not ingested -- see plan's "Known gaps"
            "end_date": opp.end_date,
            "flws": flw_counts.get(opp.opportunity_id, 0),
            "visits_claimed": claimed.get(opp.opportunity_id, 0),
            "visits_approved": approved.get(opp.opportunity_id, 0),
            "approved_7d": approved_7d.get(opp.opportunity_id, 0),
            "visits_pending": pending.get(opp.opportunity_id, 0),
            "amount_paid": float(paid.get(opp.opportunity_id) or 0),
            # Distinct from amount_paid==0: whether any PulseWork row for this
            # opp has a payment_date at all. Without this, a real "$0 paid so
            # far" (has_payment_data=True) renders identically to "no payment
            # data exists yet" -- the template must tell those apart.
            "has_payment_data": opp.opportunity_id in has_payment_data,
        }
        for opp in opportunities
    ]
    rows.sort(key=lambda r: -r["approved_7d"])
    return rows


def cohort_pivot(opportunities: list[PulseOpportunity]) -> dict:
    """Delivery Type × Country pivot: Visits / Visits Last 7 Days / Orgs bands.

    Takes the SAME already-filtered opportunity list as opportunity_detail_rows
    for whatever's on screen next to it -- otherwise the two panels can show
    different scopes (e.g. the pivot including Inactive opportunities the
    detail table has already filtered out). That includes opportunities with
    no country or no delivery type set: the detail table already shows those
    (as "—" / "Unclassified"), so excluding them here instead of giving them
    a same-named row/column would silently undercount this pivot's grand
    total relative to the detail table's own sum for the identical scope.
    """
    opps = list(opportunities)
    opp_ids = [o.opportunity_id for o in opps]
    country_of = {o.opportunity_id: country_label(o.country) or "Unknown" for o in opps}
    service_of = {o.opportunity_id: o.service_slug for o in opps}
    org_of = {o.opportunity_id: o.org_slug for o in opps}

    # One grouped scan with a conditional Sum for the 7-day figure, instead
    # of two separate queries differing only by that date cutoff.
    since_7d = timezone.now() - timedelta(days=7)
    visits_rows = (
        PulseRollup.objects.filter(status="approved", opportunity_id__in=opp_ids)
        .values("opportunity_id")
        .annotate(n=Sum("n"), n_7d=Sum("n", filter=Q(bucket_hour__gte=since_7d)))
    )

    cells: dict[tuple[str, str], dict] = {}

    def cell(oid):
        key = (country_of[oid], service_of[oid])
        return cells.setdefault(key, {"visits": 0, "visits_7d": 0, "orgs": set()})

    # Seed a cell for every in-scope opportunity FIRST, regardless of whether
    # it has any approved-visit rollup yet -- otherwise a newly-onboarded org
    # whose opportunity hasn't had a visit approved yet is correctly visible
    # in the detail table beside this pivot, but silently missing from the
    # Orgs column here.
    for oid in opp_ids:
        c = cell(oid)
        if org_of.get(oid):
            c["orgs"].add(org_of[oid])

    for row in visits_rows:
        oid = row["opportunity_id"]
        if oid not in country_of:
            continue
        c = cell(oid)
        c["visits"] += row["n"] or 0
        c["visits_7d"] += row["n_7d"] or 0

    countries = sorted({k[0] for k in cells})
    services = sorted({k[1] for k in cells})

    def empty_cell():
        return {"visits": 0, "visits_7d": 0, "orgs": set()}

    grid = {c: {s: cells.get((c, s), empty_cell()) for s in services} for c in countries}

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


def running_visit_total(opp_ids: list[int]) -> list[dict]:
    """Cumulative approved visits over all time -- exact, PulseRollup never prunes."""
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


def running_user_total(opp_ids: list[int]) -> list[dict]:
    """Cumulative distinct-FLW roster growth, by first-seen date in PulseWork.

    Exact and full-history, unlike daily active users below (which needs
    visit-level data that's pruned after PULSE_EVENT_RETENTION_DAYS).
    """
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


def daily_visits_and_users(opp_ids: list[int]) -> list[dict]:
    """Daily approved visits (full history) + daily unique FLWs (last ~30d only).

    The users line can't extend further back without visit-level PulseEvent
    rows, which are pruned -- callers must render missing days as absent, not
    zero, per Pulse's own "no data != zero" rule for money.
    """
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
    # Union of both key sets: a day with FLW activity but no *approved* visit
    # yet (still pending review) must still appear, with approved_visits=0,
    # rather than being dropped from the series entirely.
    all_days = sorted(set(daily_visits) | set(daily_users))
    return [{"t": d, "approved_visits": daily_visits.get(d, 0), "unique_users": daily_users.get(d)} for d in all_days]


def monthly_visits_by_country(opportunities: list[PulseOpportunity], *, top_n=6) -> dict:
    """Monthly stacked totals; the long tail folds into "Other" rather than
    each country competing for a low-contrast hue."""
    country_of = {o.opportunity_id: country_label(o.country) for o in opportunities}
    opp_ids = list(country_of)

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
    # Every month from first to last observed, not just months that happen to
    # have an approved visit -- the chart positions bars by index, not by
    # date, so a skipped month would silently sit its neighbours right next
    # to each other and read as continuous activity across a gap.
    for m in _month_range(monthly):
        month_values = monthly.get(m, {})
        values = {c: month_values.get(c, 0) for c in top_countries}
        values["Other"] = sum(v for c, v in month_values.items() if c not in top_countries)
        series.append({"month": m, "values": values})
    return {"countries": [*top_countries, "Other"], "series": series}


def _month_range(monthly: dict[str, dict[str, int]]) -> list[str]:
    """Every month from the first to the last observed, including empty ones."""
    if not monthly:
        return []
    keys = sorted(monthly)
    start = date.fromisoformat(keys[0])
    end = date.fromisoformat(keys[-1])
    months, cur = [], start
    while cur <= end:
        months.append(cur.isoformat())
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return months
