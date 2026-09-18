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

import datetime
import decimal
import time

from django.db.models import Count, Max, Prefetch, Q

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import programmes
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
# Everything cached for the TTL, keyed by the function that fills it.
_CACHED = ("delivering", "workspaces", "delivered", "first_service", "committed", "fx")
_cache: dict = dict.fromkeys(_CACHED) | {"loaded_at": 0.0}


def invalidate() -> None:
    """Drop the cache. Called after an import, and by tests that seed delivery.

    Clears by iterating `_CACHED` rather than naming each entry: an earlier
    version listed them by hand, and adding a fourth left it uncleared, which
    surfaced as a test that passed alone and failed in the suite — the cache
    from the previous test answering this one.
    """
    _cache.update(dict.fromkeys(_CACHED))
    _cache["loaded_at"] = 0.0


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


def first_service_by_org_name() -> dict:
    """Organisation name -> the date it first delivered a verified service.

    The date, not just the fact, because "is delivering" and "started
    delivering because of this round" are different claims and only the second
    is interesting on a round page.
    """
    if _fresh() and _cache["first_service"] is not None:
        return _cache["first_service"]

    from connect_labs.pulse.network_api import first_service_by_partner

    out = dict(first_service_by_partner())
    _cache["first_service"] = out
    _cache["loaded_at"] = _cache["loaded_at"] or time.monotonic()
    return out


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


def delivered_programmes_by_org_name() -> dict[str, set[str]]:
    """Organisation name -> the Connect delivery types it has actually run.

    Read off the pulse spine, which carries Connect's own `delivery_type` on
    every opportunity. This is the honest half of the programme filter: not
    what an organisation says it does, but what it has been paid to do.
    """
    from connect_labs.pulse.models import PulseOpportunity

    if _fresh() and _cache["delivered"] is not None:
        return _cache["delivered"]

    by_slug: dict[str, set[str]] = {}
    for slug, service in (
        PulseOpportunity.objects.exclude(org_slug="").exclude(service_slug="").values_list("org_slug", "service_slug")
    ):
        # `other` is Connect's unclassified bucket, not a programme. Offering
        # it in the filter would put 264 opportunities behind a label that
        # means "we do not know what this is".
        if programmes.is_programme(service):
            by_slug.setdefault(slug, set()).add(service)

    out: dict[str, set[str]] = {}
    for name, slugs in workspace_slugs_by_org_name().items():
        services = set().union(*(by_slug.get(s, set()) for s in slugs)) if slugs else set()
        if services:
            out[name] = services
    _cache["delivered"] = out
    _cache["loaded_at"] = _cache["loaded_at"] or time.monotonic()
    return out


def fx_rates() -> dict[str, decimal.Decimal]:
    """USD per unit of each currency, from Connect's own conversions.

    Every completed work records what it accrued locally and what Connect
    converted that to, so each opportunity carries a rate Connect actually
    applied. Pooling them per currency covers the opportunities whose own works
    have not been sampled yet.

    The MEDIAN, not the mean: a single opportunity with a malformed accrual
    produced a rate of zero in the first sample of this data, and a mean would
    have carried that into every figure the currency touches.
    """
    from statistics import median

    from connect_labs.pulse.models import PulseOpportunity

    if _fresh() and _cache["fx"] is not None:
        return _cache["fx"]

    seen: dict[str, list] = {}
    for currency, rate in (
        PulseOpportunity.objects.exclude(currency="")
        .exclude(usd_rate=None)
        .filter(usd_rate__gt=0)
        .values_list("currency", "usd_rate")
    ):
        seen.setdefault(currency, []).append(rate)

    out = {currency: median(rates) for currency, rates in seen.items()}
    # A currency is its own unit. Asserting it rather than deriving it means a
    # broken derivation shows up as USD figures that are wrong by a factor,
    # which is visible, instead of quietly rescaling the one currency whose
    # answer everybody knows.
    out["USD"] = decimal.Decimal(1)
    _cache["fx"] = out
    _cache["loaded_at"] = _cache["loaded_at"] or time.monotonic()
    return out


def committed_by_programme() -> dict[str, dict]:
    """Per programme: what has been deployed, and what live work is still funded.

    Two numbers, deliberately built from different things:

    * DEPLOYED is money Connect actually paid — the USD accrued on every
      completed work, to workers and to organisations. It is not estimated
      from visits: a payment unit can cover several visits, so multiplying
      visits by a per-visit budget overstated delivery wherever it did.

    * REMAINING is budget still available on opportunities that are LIVE —
      active and not past their end date — net of what each has already paid.
      An ended opportunity contributes nothing, however much of its budget went
      unspent: that money expired, it is not waiting to be spent.

      It is an UPPER BOUND, and should be presented as "up to". Real budgets
      are not uniformly spent: across 193 finished real opportunities the
      median paid 40% of its budget and a quarter paid 1% or less. Most of the
      gap is one programme — KMC Uganda Roll-out's five opportunities held
      $2.5M between them and paid about $95k — and that programme's live
      opportunity is the majority of today's KMC figure. Forecasting a spend
      rate onto it would be inventing a number; saying "up to" is not.

    The second rule is most of the correction. `total_budget` on Connect is a
    ceiling set generously, not a commitment — one Kangaroo Mother Care
    opportunity carried $1.48M of budget and paid $4,722 before it closed — so
    counting ended opportunities put $5M of lapsed allowance into a figure
    that should have been about $1M, and made "remaining" read as twice what
    had ever been spent.

    Figures are USD at the rate Connect applied (see `fx_rates`). Opportunities
    whose budget has not been read, or whose currency has no rate, are left out
    of REMAINING and counted in `unconvertible`, never guessed at.
    """
    from django.db.models import Sum
    from django.utils import timezone

    from connect_labs.pulse.models import PulseOpportunity, PulseProgram, PulseWork

    if _fresh() and _cache["committed"] is not None:
        return _cache["committed"]

    today = timezone.now().date()
    rates = fx_rates()
    # Test programmes are excluded from both figures. Connect has a real
    # `Opportunity.is_test`, but no export carries it, so this is labs' own
    # programme-name heuristic (`PulseProgram.is_test`) — it catches "CHC Test
    # Opportunity" under "Founders Pledge Test Program", and would miss a test
    # programme named like a real one. It matters here more than anywhere:
    # that one test opportunity alone carried $312,500 of budget.
    test_programmes = set(PulseProgram.objects.filter(is_test=True).values_list("program_id", flat=True))

    paid = {
        row["opportunity_id"]: (row["worker"] or 0) + (row["org"] or 0)
        for row in PulseWork.objects.values("opportunity_id").annotate(
            worker=Sum("usd_to_worker"), org=Sum("usd_to_org")
        )
    }

    out: dict[str, dict] = {}
    for opp in PulseOpportunity.objects.exclude(service_slug="").only(
        "opportunity_id", "program_id", "service_slug", "total_budget", "currency", "usd_rate", "is_active", "end_date"
    ):
        if not programmes.is_programme(opp.service_slug) or opp.program_id in test_programmes:
            continue
        entry = out.setdefault(
            opp.service_slug,
            {"deployed": 0, "remaining": 0, "live_opportunities": 0, "unconvertible": 0, "currencies": set()},
        )
        spent = decimal.Decimal(paid.get(opp.opportunity_id, 0) or 0)
        entry["deployed"] += int(spent)

        live = opp.is_active and (opp.end_date is None or opp.end_date >= today)
        if not live:
            continue
        entry["live_opportunities"] += 1

        rate = opp.usd_rate or rates.get(opp.currency)
        if opp.total_budget is None or not rate or rate <= 0:
            entry["unconvertible"] += 1
            continue
        budget_usd = decimal.Decimal(opp.total_budget) * rate
        # Never below zero: an opportunity can pay out beyond its budget, and
        # "minus $4,000 still available" is not something anyone can act on.
        entry["remaining"] += int(max(budget_usd - spent, 0))
        if opp.currency:
            entry["currencies"].add(opp.currency)

    for entry in out.values():
        entry["currencies"] = sorted(entry["currencies"])

    _cache["committed"] = out
    _cache["loaded_at"] = _cache["loaded_at"] or time.monotonic()
    return out


def in_segment(row, segment: str, delivering: set[str]) -> bool:
    """Whether one annotated organisation belongs to a segment."""
    if segment == "delivering":
        return row.name in delivering
    if segment == "bench":
        return row.name not in delivering
    if segment == "nocontact":
        return row.contact_count == 0
    if segment == "repeat":
        return row.rounds_applied > 1
    return True


def segment_counts(rows, delivering: set[str]) -> dict[str, int]:
    """How many organisations each segment holds, over the rows given.

    Counted over the SAME rows the list is showing, so a count can never
    promise a number the list then fails to produce.
    """
    rows = list(rows)
    return {key: sum(1 for r in rows if in_segment(r, key, delivering)) for key, _, _ in SEGMENTS}


def facet_counts(rows, delivering: set[str]) -> dict[str, list[dict]]:
    """Countries, programmes and rounds with the number of organisations in each.

    The point of a facet count is that you see the size of a filter before you
    spend a click on it, so these are computed over the rows currently in
    scope rather than over the whole registry.
    """
    rows = list(rows)
    delivered_by_name = delivered_programmes_by_org_name()
    countries: dict[str, int] = {}
    delivered: dict[str, int] = {}
    applied: dict[str, int] = {}
    for row in rows:
        profile = getattr(row, "marketplace_profile", None)
        for country in (profile.countries if profile else None) or []:
            countries[country] = countries.get(country, 0) + 1
        for slug in delivered_by_name.get(row.name, ()):  # noqa: SIM118
            delivered[slug] = delivered.get(slug, 0) + 1
        for slug in applied_programmes_of(row):
            applied[slug] = applied.get(slug, 0) + 1

    # From the prefetch rather than a fresh aggregate: the caller has already
    # fetched every organisation with its responses, and asking the database to
    # recount what is in memory is three more round-trips per page.
    per_round: dict[str, dict] = {}
    for row in rows:
        for response in row.solicitation_responses.all():
            round_ = response.solicitation
            entry = per_round.setdefault(round_.slug, {"slug": round_.slug, "title": round_.title, "orgs": set()})
            entry["orgs"].add(row.pk)
    rounds = sorted(
        ({"slug": e["slug"], "title": e["title"], "orgs": len(e["orgs"])} for e in per_round.values()),
        key=lambda e: (-e["orgs"], e["title"]),
    )

    def ranked(counts: dict[str, int]) -> list[dict]:
        return [
            {"value": value, "count": count} for value, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def ranked_programmes(counts: dict[str, int]) -> list[dict]:
        # Labelled here rather than in the template: the slug is what the URL
        # carries and the label is what a person reads, and only this module
        # knows that `programmes.label` is where the second comes from.
        return [
            {"value": slug, "label": programmes.label(slug), "count": count}
            for slug, count in sorted(counts.items(), key=lambda kv: (-kv[1], programmes.label(kv[0])))
        ]

    return {
        "countries": ranked(countries),
        "delivered": ranked_programmes(delivered),
        "applied": ranked_programmes(applied),
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
        # Identified organisations PLUS the submissions nobody could attribute.
        # `COUNT(DISTINCT llo_entity_id)` ignores NULLs, so on its own it drops
        # every applicant awaiting a verdict — the French Readers round showed
        # "4 organisations" over a list of five, one of which was simply not
        # matched yet. An unattributed submission is still an applicant; what
        # is unknown is WHICH one, and the verdict count next to it says so.
        # Each is counted separately because assuming two of them are the same
        # organisation is precisely the judgement being deferred.
        organisations=Count("responses__llo_entity_id", distinct=True)
        + Count("responses", filter=Q(responses__llo_entity__isnull=True), distinct=True),
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


def round_since(round_: Solicitation):
    """The date after which delivery could plausibly have come FROM this round.

    The decision date if there is one, else the deadline, else publication.
    """
    return round_.decision_on or round_.application_deadline or round_.published_on


def round_applicants(round_: Solicitation, first_service: dict):
    """Who applied to a round, and what became of them.

    The outcome is the join this whole project exists to make: an EOI answered
    in 2025 and a first delivered service in 2026 live in two systems that have
    never been able to see each other.

    It reports the ORDER of those two events and nothing stronger. An
    organisation already delivering when the round opened did not start because
    of it, and saying only "delivering" of both invites exactly that reading —
    which was the state of this page until someone asked what it meant. One
    that first delivered afterwards is consistent with having won, and that is
    as much as two dates can tell you: this round is not the only thing that
    happened to these organisations.
    """
    since = round_since(round_)
    responses = round_.responses.select_related("llo_entity", "llo_entity__marketplace_profile").order_by(
        "-submission_date", "source_row"
    )

    # One row per ORGANISATION, not per submission. Four organisations sent the
    # 2025 CHC form twice — the same body, days or months apart, under slightly
    # different spellings of its own name — and listing each submission made
    # them look like separate applicants. Unmatched submissions are never
    # collapsed: two of them being the same organisation is exactly the
    # question nobody has answered yet.
    seen: dict[int, dict] = {}
    out = []
    for response in responses:
        org = response.llo_entity
        if org is not None and org.pk in seen:
            seen[org.pk]["submissions"] += 1
            continue
        profile = getattr(org, "marketplace_profile", None) if org else None
        started = first_service.get(org.name) if org is not None else None
        if response.match_state == SolicitationResponse.MATCH_UNMATCHED:
            outcome = "unresolved"
        elif started is None:
            outcome = "never"
        elif since is None or _as_date(started) >= since:
            outcome = "after"
        else:
            outcome = "before"
        row = {
            "response": response,
            "org": org,
            "name": (org.name if org else response.org_name) or "(organisation name not given)",
            "country": (profile.countries[0] if profile and profile.countries else response.country_as_submitted),
            "outcome": outcome,
            "started": started,
            "submissions": 1,
        }
        if org is not None:
            seen[org.pk] = row
        out.append(row)
    return out


def _as_date(value):
    return value.date() if hasattr(value, "date") else value


def submission_trend(round_: Solicitation, buckets: int = 26) -> list[dict]:
    """When this round's submissions actually arrived.

    A round reads as one event and is not: the 2025 CHC round took submissions
    from February to September. The shape of that — a burst on announcement, a
    tail, a second burst when somebody re-shared it — is the thing a total
    cannot show.

    Bucketed by week, or by day when the whole round ran inside three weeks,
    because thirty bars of one submission each says less than seven of four.
    """
    dates = sorted(
        _as_date(r.submission_date) for r in round_.responses.all() if r.submission_date is not None  # noqa: SIM118
    )
    if len(dates) < 2:
        return []

    first, last = dates[0], dates[-1]
    span = (last - first).days
    step = 1 if span <= 21 else 7
    edges = []
    cursor = first
    while cursor <= last and len(edges) < 400:
        edges.append(cursor)
        cursor += datetime.timedelta(days=step)

    counts = [0] * len(edges)
    for value in dates:
        index = min((value - first).days // step, len(edges) - 1)
        counts[index] += 1

    peak = max(counts) or 1
    return [
        {
            "start": edge,
            "count": count,
            # Percentage rather than pixels: the bar is drawn by the template
            # and has to survive whatever width the column ends up being.
            "pct": round(count * 100 / peak, 1),
            "days": step,
        }
        for edge, count in zip(edges, counts)
    ]


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
    # Two programme dimensions rather than one, because they answer different
    # questions and the gap between them is the interesting one: an
    # organisation that has APPLIED to a programme it has never DELIVERED is
    # exactly who a round is looking for.
    sections = [
        ("country", "Country", facets["countries"], selected["countries"], None),
        ("delivered", "Has delivered", facets["delivered"], selected["delivered"], "label"),
        ("applied", "Has applied for", facets["applied"], selected["applied"], "label"),
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


def all_rows_with_rounds():
    """Every organisation, its profile and its rounds, in ONE trip.

    The network page filters the same population five ways — the list, each of
    the three facet dimensions, and the globe. Re-running the query for each is
    five round-trips for one page, and at a few hundred organisations the whole
    population is smaller than a single page of most tables. So it is fetched
    once and sliced in Python, which turns four queries and three aggregates
    into one query and some list comprehensions.

    If this registry ever reaches the tens of thousands, this is the decision to
    revisit: the fix then is to push filtering back into SQL and paginate, not
    to fetch more rows into memory.
    """
    responses = SolicitationResponse.objects.select_related("solicitation").order_by("-solicitation__published_on")
    return list(
        LabsOrg.objects.select_related("marketplace_profile")
        .prefetch_related(Prefetch("solicitation_responses", queryset=responses))
        .annotate(
            contact_count=Count("contacts", distinct=True),
            application_count=Count("solicitation_responses", distinct=True),
            # Distinct ROUNDS, not submissions. An organisation that sent the
            # same round's form twice has not "come back for another round",
            # which is what the segment below claims of it.
            rounds_applied=Count("solicitation_responses__solicitation", distinct=True),
            last_applied=Max("solicitation_responses__submission_date"),
        )
        .order_by("name")
    )


def applied_programmes_of(org) -> set[str]:
    """The delivery types this organisation has applied to work on.

    Reads the prefetch. An untagged round contributes nothing rather than an
    empty-string facet — "we have not decided what programme this round is" is
    not a programme anybody can filter on.
    """
    return {
        r.solicitation.delivery_type
        for r in org.solicitation_responses.all()
        if programmes.is_programme(r.solicitation.delivery_type)
    }


def matches(org, *, query="", countries=(), delivered=(), applied=(), delivered_by_name=None) -> bool:
    """Whether one organisation survives the facet filters.

    Values WITHIN a dimension are an OR and dimensions are an AND, because that
    is what "Uganda or Malawi, and delivers KMC" means to the person ticking
    them.

    Country stays a substring match because the sheet's country cell is free
    text ("Congo, the Democratic Republic of the"). Programmes are matched
    exactly: they are Connect's own slugs, not prose, and a substring rule over
    a controlled vocabulary only invents false positives.
    """
    profile = getattr(org, "marketplace_profile", None)

    if query:
        needle = query.lower()
        if needle not in org.name.lower() and needle not in (org.short_name or "").lower():
            return False
    if countries:
        have = [c.lower() for c in ((profile.countries if profile else None) or [])]
        if not any(any(v.lower() in c for c in have) for v in countries):
            return False
    if delivered:
        by_name = delivered_programmes_by_org_name() if delivered_by_name is None else delivered_by_name
        if not (by_name.get(org.name, set()) & set(delivered)):
            return False
    if applied and not (applied_programmes_of(org) & set(applied)):
        return False
    return True
