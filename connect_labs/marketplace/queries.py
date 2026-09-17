"""What the marketplace screens ask for.

Kept out of the views because these are the questions, and the views are only
their arrangement: a marketplace has two sides, and every query here answers
something about one of them — the organisations that deliver, or the rounds
they answer.

Nothing is stored that can be derived. Delivery comes off the pulse spine at
read time through the same resolver the rest of labs uses, so a partner that
started delivering this morning is delivering here this morning, and the
directory can never disagree with the wall display about who is live.
"""

from __future__ import annotations

import time

from django.db.models import Count, Max, Q

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgProfile
from connect_labs.solicitations.local_models import ACCESS_OK, Solicitation, SolicitationResponse

# The segments people actually work, in the order they are offered. Each one is
# a real job — "who can I still activate", "who can I not even write to" — not a
# permutation of the filter controls.
SEGMENTS = [
    ("all", "All", "Every organisation labs knows of, including those Connect has no row for."),
    ("bench", "On the bench", "Answered a round, never activated. The largest untapped part of the network."),
    ("delivering", "Delivering", "Currently running at least one Connect opportunity."),
    ("nocontact", "No contact on file", "Nobody to write to — the ceiling on any outreach until it is fixed."),
    ("repeat", "Applied more than once", "Came back for another round. Interest is already demonstrated."),
]


# Delivery and workspace resolution both read the pulse spine -- PulseEvent is
# millions of rows -- and both are asked for repeatedly while rendering one
# page: the list, each facet dimension's counts, and the globe all need them.
# So they are cached for a minute, exactly as `partner_names` caches the
# registry and for the same reason: the underlying answer changes when the
# poller ingests, not between two queries in one request.
_CACHE_TTL_SECONDS = 60
_cache: dict = {"delivering": None, "workspaces": None, "loaded_at": 0.0}


def invalidate() -> None:
    """Drop the cache. Called after an import, and by tests that seed delivery."""
    _cache["loaded_at"] = 0.0
    _cache["delivering"] = None
    _cache["workspaces"] = None


def _fresh() -> bool:
    return bool(_cache["loaded_at"]) and (time.monotonic() - _cache["loaded_at"]) < _CACHE_TTL_SECONDS


def delivering_names() -> set[str]:
    """Organisation names with at least one verified service on Connect.

    Cached: this aggregates over the whole pulse spine, and one page render
    asks for it four times over.
    """
    if _fresh() and _cache["delivering"] is not None:
        return _cache["delivering"]

    from connect_labs.pulse.network_api import first_service_by_partner

    names = set(first_service_by_partner())
    _cache["delivering"] = names
    _cache["loaded_at"] = time.monotonic()
    return names


def workspace_slugs_by_org_name() -> dict[str, set[str]]:
    """Organisation name -> every Connect workspace slug that resolves to it.

    One pass over the distinct slugs rather than a resolve per organisation:
    the directory asks this for 240 organisations at once, and `resolve()` is
    cached per process for a minute, not per call.
    """
    from connect_labs.pulse.models import PulseOpportunity
    from connect_labs.pulse.partner_names import resolve as resolve_partner

    if _fresh() and _cache["workspaces"] is not None:
        return _cache["workspaces"]

    out: dict[str, set[str]] = {}
    slugs = PulseOpportunity.objects.exclude(org_slug="").values_list("org_slug", flat=True).distinct()
    for slug in slugs:
        parent = resolve_partner(slug)["parent"]
        if parent:
            out.setdefault(parent, set()).add(slug)
    _cache["workspaces"] = out
    _cache["loaded_at"] = _cache["loaded_at"] or time.monotonic()
    return out


def org_rows():
    """Every organisation, annotated with what the directory shows."""
    return (
        LabsOrg.objects.select_related("marketplace_profile")
        .annotate(
            contact_count=Count("contacts", distinct=True),
            application_count=Count("solicitation_responses", distinct=True),
            last_applied=Max("solicitation_responses__submission_date"),
        )
        .order_by("name")
    )


def in_segment(row, segment: str, delivering: set[str]) -> bool:
    """Whether one annotated organisation belongs to a segment."""
    if segment == "delivering":
        return row.name in delivering
    if segment == "bench":
        return row.name not in delivering
    if segment == "nocontact":
        return row.contact_count == 0
    if segment == "repeat":
        return row.application_count > 1
    return True


def segment_counts(rows, delivering: set[str]) -> dict[str, int]:
    """How many organisations each segment holds, over the rows given.

    Counted over the SAME rows the list is showing, so a count can never
    promise a number the list then fails to produce.
    """
    rows = list(rows)
    return {key: sum(1 for r in rows if in_segment(r, key, delivering)) for key, _, _ in SEGMENTS}


def facet_counts(rows, delivering: set[str]) -> dict[str, list[dict]]:
    """Countries, sectors and rounds with the number of organisations in each.

    The point of a facet count is that you see the size of a filter before you
    spend a click on it, so these are computed over the rows currently in
    scope rather than over the whole registry.
    """
    rows = list(rows)
    countries: dict[str, int] = {}
    sectors: dict[str, int] = {}
    for row in rows:
        profile = getattr(row, "marketplace_profile", None)
        if profile is None:
            continue
        for country in profile.countries or []:
            countries[country] = countries.get(country, 0) + 1
        for sector in profile.sectors or []:
            sectors[sector] = sectors.get(sector, 0) + 1

    ids = [r.pk for r in rows]
    rounds = (
        Solicitation.objects.filter(responses__llo_entity_id__in=ids)
        .annotate(orgs=Count("responses__llo_entity_id", distinct=True))
        .order_by("-orgs")
        .values("slug", "title", "orgs")
    )

    def ranked(counts: dict[str, int]) -> list[dict]:
        return [
            {"value": value, "count": count} for value, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    return {
        "countries": ranked(countries),
        "sectors": ranked(sectors),
        "rounds": [{"value": r["slug"], "label": r["title"], "count": r["orgs"]} for r in rounds],
    }


def network_totals() -> dict:
    """The headline figures for the marketplace hero.

    `services` comes from pulse's own opportunity mirror rather than being
    recounted here — it is the same number the wall display shows, and two
    figures for one fact is how a dashboard loses trust.
    """
    from connect_labs.pulse.models import PulseOpportunity

    profiles = OrgProfile.objects.exclude(country_iso3="")
    services = sum(PulseOpportunity.objects.values_list("lifetime_visit_count", flat=True))
    return {
        "organisations": LabsOrg.objects.count(),
        "countries": len({p.country_iso3 for p in profiles}),
        "services": services,
        "rounds": Solicitation.objects.count(),
        "applications": SolicitationResponse.objects.count(),
        "delivering": len(delivering_names()),
    }


def rounds_with_counts():
    """Every round, with how many applications and organisations it drew."""
    return Solicitation.objects.annotate(
        applications=Count("responses", distinct=True),
        organisations=Count("responses__llo_entity_id", distinct=True),
        unresolved=Count(
            "responses",
            filter=Q(responses__match_state=SolicitationResponse.MATCH_UNMATCHED),
            distinct=True,
        ),
    ).order_by("-published_on", "title")


def open_rounds():
    """Rounds still taking applications."""
    return rounds_with_counts().filter(status="active")


def closed_rounds():
    """Rounds that have been decided, busiest first — the track record."""
    return rounds_with_counts().exclude(status="active").order_by("-applications")


def round_applicants(round_: Solicitation, delivering: set[str]):
    """Who applied to a round, and what became of them.

    The outcome column is the join this whole project exists to make: an EOI
    answered in 2025 and a first delivered service in 2026 live in two systems
    that have never been able to see each other.
    """
    responses = round_.responses.select_related("llo_entity", "llo_entity__marketplace_profile").order_by(
        "-submission_date", "source_row"
    )
    out = []
    for response in responses:
        org = response.llo_entity
        profile = getattr(org, "marketplace_profile", None) if org else None
        if response.match_state == SolicitationResponse.MATCH_UNMATCHED:
            outcome = "unresolved"
        elif org is not None and org.name in delivering:
            outcome = "delivering"
        else:
            outcome = "never"
        out.append(
            {
                "response": response,
                "org": org,
                "name": (org.name if org else response.org_name) or "(organisation name not given)",
                "country": (profile.countries[0] if profile and profile.countries else response.country_as_submitted),
                "flws": profile.flws_managed if profile else None,
                "outcome": outcome,
            }
        )
    return out


def unreadable_rounds():
    """Rounds labs could not open. Not the same as rounds nobody applied to."""
    return Solicitation.objects.exclude(sa_access_state=ACCESS_OK).order_by("title")


def map_points(rows, delivering: set[str]) -> list[dict]:
    """The organisations as points on the globe.

    Precision travels with every point, because the three sources behind it are
    not equivalent: a town matched in an address is a pin, a country is a whole
    country. The globe draws them differently and the legend says so — a map
    that hides the difference draws a rooftop from the word "Nigeria".
    """
    points = []
    for row in rows:
        profile = getattr(row, "marketplace_profile", None)
        if profile is None or profile.lat is None or profile.lon is None:
            continue
        points.append(
            {
                "name": row.name,
                "short": row.short_name or row.name,
                "slug": row.slug,
                "lat": profile.lat,
                "lon": profile.lon,
                "precision": profile.location_precision or "country",
                "place": profile.location_label or "",
                "iso3": profile.country_iso3 or "",
                "delivering": row.name in delivering,
                "contacts": row.contact_count,
                "applications": row.application_count,
            }
        )
    return points


def facet_rail(facets: dict, selected: dict, querydict) -> list[dict]:
    """The facet rail: every value, its size, and the URL that toggles it.

    The toggle URL is built here rather than in the template because it has to
    PRESERVE the rest of the query — a facet that silently dropped the search
    box or the segment when clicked would be worse than no facet at all — and
    because a checked value's link must REMOVE it, which is not something a
    template can express.
    """
    sections = [
        ("country", "Country", facets["countries"], selected["countries"], None),
        ("sector", "Sector", facets["sectors"], selected["sectors"], None),
        ("applied", "Applied to", facets["rounds"], selected["applied"], "label"),
    ]

    out = []
    for param, title, values, chosen, label_key in sections:
        rows = []
        for entry in values:
            value = entry["value"]
            on = value in chosen
            params = querydict.copy()
            current = [v for v in params.getlist(param) if v]
            params.setlist(param, [v for v in current if v != value] if on else current + [value])
            rows.append(
                {
                    "value": value,
                    "label": entry.get(label_key) if label_key else value,
                    "count": entry["count"],
                    "selected": on,
                    "url": "?" + params.urlencode(),
                }
            )
        out.append({"param": param, "title": title, "rows": rows, "chosen": len(chosen)})
    return out


def rounds_by_org(orgs) -> dict[int, list]:
    """org id -> up to three rounds it answered, in ONE query.

    This was a query per row. At 240 organisations that is 240 round-trips to
    render one page, which is the difference between a page that feels instant
    and one people notice.
    """
    ids = [o.pk for o in orgs]
    if not ids:
        return {}

    pairs = (
        SolicitationResponse.objects.filter(llo_entity_id__in=ids)
        .select_related("solicitation")
        .order_by("llo_entity_id", "-solicitation__published_on")
        .values_list("llo_entity_id", "solicitation__slug", "solicitation__title")
    )

    out: dict[int, list] = {}
    for org_id, slug, title in pairs:
        seen = out.setdefault(org_id, [])
        if len(seen) >= 3 or any(r["slug"] == slug for r in seen):
            continue
        seen.append({"slug": slug, "title": title})
    return out
