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
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db.models import Count, Max, Q, Sum
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
    PulseOrganization,
    PulseProgram,
    PulsePublicToken,
    PulseRollup,
    PulseScalar,
    PulseWork,
)
from connect_labs.pulse.normalize import COUNTRY_NAMES, FLAG_LABELS, SERVICE_LABELS, service_label
from connect_labs.pulse.partner_names import resolve as resolve_partner

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


def _event_row(e: PulseEvent, partners: bool = False) -> list:
    """Compact positional encoding — these ship thousands at a time.

    ``partners`` carries the delivering org so the feed can name who delivered
    each service and open their record. Withheld for a caller not entitled to
    partner identity, for the same reason the partner menu is: a per-event org
    slug would hand an anonymised link the partner breakdown one row at a time.
    """
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
        (e.org_slug or None) if partners else None,
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
    "org_slug",
]


def _program_scope(request):
    """Resolve ``?program=<id>`` / ``?org=<slug>`` into the querysets every card is drawn from.

    Filtering has to happen HERE rather than in the page: the headline figures
    are server-side aggregates over the whole estate, so a client that hid rows
    would leave "1.6M services" sitting above a filtered map. Every count on
    screen has to be recomputed for the selected programme or none of them can
    be trusted.

    ``PulseEvent``, ``PulseWork`` and ``PulseOpportunity`` all carry an indexed
    ``program_id``. ``PulseRollup`` keys on opportunity, so it filters through
    one. ``PulseGridCell`` carries neither -- see ``grid_service`` below.

    Org and programme compose rather than override: an org filter narrows to
    that partner, and a programme filter on top narrows to their work on that
    programme. Making one silently clear the other would let the two controls
    disagree about what the screen is showing.
    """
    raw = (request.GET.get("program") or "").strip()
    program = None
    if raw:
        try:
            program = PulseProgram.objects.filter(program_id=int(raw)).first()
        except ValueError:
            program = None

    # The partner filter is only honoured for a caller entitled to partner
    # identity. Scoping to `?org=connect-nigeria` would hand an anonymised
    # caller that partner's volumes and rates keyed to a name they supplied --
    # withholding the *name* from a response whose shape is "this named
    # partner's commercial performance" protects nothing.
    org_raw = (request.GET.get("org") or "").strip()
    org = _resolve_org(org_raw) if org_raw and _partner_names_allowed(request) else None

    # Delivery type -- Connect's own service taxonomy (chc, ecd, kmc, ...), which
    # is a different axis from `program`: a programme is one funder's engagement,
    # a delivery type is the kind of work. "All the Kangaroo Mother Care on the
    # platform" spans many programmes and many partners, and was previously only
    # answerable by reading a breakdown rather than by narrowing to it.
    #
    # Not gated: a delivery type is a category, not a partner identity, so an
    # anonymised link can use it.
    service_raw = (request.GET.get("service") or "").strip().lower()
    service = service_raw if service_raw in SERVICE_LABELS else ""

    # A single opportunity. Composes like the rest, and is what a partner
    # window uses when one of its engagements is selected -- a partner with 91
    # of them is not one thing, and the roster underneath has to follow.
    opp_raw = (request.GET.get("opportunity") or "").strip()
    opportunity = None
    if opp_raw.isdigit():
        opportunity = PulseOpportunity.objects.filter(opportunity_id=int(opp_raw)).first()

    events = PulseEvent.objects.all()
    works = PulseWork.objects.all()
    opps = PulseOpportunity.objects.all()
    rollups = PulseRollup.objects.all()
    grid_service = None

    if org is not None:
        # org_slug is denormalised onto all three spines at ingest, so the
        # partner filter needs no join. Rollups key on opportunity, same as the
        # programme path.
        events = events.filter(org_slug=org.slug)
        works = works.filter(org_slug=org.slug)
        opps = opps.filter(org_slug=org.slug)
        rollups = rollups.filter(
            opportunity_id__in=PulseOpportunity.objects.filter(org_slug=org.slug).values("opportunity_id")
        )

    if opportunity is not None:
        oid = opportunity.opportunity_id
        events = events.filter(opportunity_id=oid)
        works = works.filter(opportunity_id=oid)
        opps = opps.filter(opportunity_id=oid)
        rollups = rollups.filter(opportunity_id=oid)

    if service:
        events = events.filter(service_slug=service)
        works = works.filter(service_slug=service)
        opps = opps.filter(service_slug=service)
        rollups = rollups.filter(
            opportunity_id__in=PulseOpportunity.objects.filter(service_slug=service).values("opportunity_id")
        )
        grid_service = service

    if program is not None:
        pid = program.program_id
        events = events.filter(program_id=pid)
        works = works.filter(program_id=pid)
        opps = opps.filter(program_id=pid)
        rollups = rollups.filter(
            opportunity_id__in=PulseOpportunity.objects.filter(program_id=pid).values("opportunity_id")
        )
        # Cells now carry program_id, so density narrows exactly like the
        # points do. Cells folded before that are null and fall back to
        # delivery type -- see _grid_for.
        grid_service = program.delivery_type or None

    return {
        "program": program,
        "org": org,
        "service": service,
        "opportunity": opportunity,
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
    if sc["program"] is None and sc["org"] is None and not sc["service"] and sc["opportunity"] is None:
        row = PulseScalar.objects.filter(key="scope").first()
        return row.value if row else {}

    opps = sc["opps"]
    agg = opps.aggregate(n=Count("id"), visits=Sum("lifetime_visit_count"))
    return {
        "opportunities": agg["n"] or 0,
        "active_opportunities": opps.filter(is_active=True).count(),
        "lifetime_visits": agg["visits"] or 0,
        # A programme is one programme; a partner may run several. Counting the
        # distinct programmes in scope keeps the header honest under either
        # filter and under both at once.
        "programs": 1
        if sc["program"] is not None
        else opps.exclude(program_id=None).values("program_id").distinct().count(),
        "orgs": opps.exclude(org_slug="").values("org_slug").distinct().count(),
    }


class _UnnamedOrg:
    """A delivery partner Connect will not name to the polling account.

    ``opp_org_program_list`` scopes its ``organizations`` list to the orgs the
    poller is a **member** of, while returning every opportunity under a
    programme those orgs *manage* -- which is delivered by other partners
    entirely. Measured on labs prod: 74 distinct partners deliver the work, 10
    of them are named, and the other 64 carry **92.2% of all services**.

    So most partners are visible only as a slug, and there is no endpoint that
    will give up their names -- ``OpportunitySerializer`` also emits
    ``organization`` as a slug.

    The slug is shown verbatim and flagged ``named: False``. It is deliberately
    **not** de-slugified: mechanical title-casing reads plausibly and is wrong
    exactly where it matters, turning the real "C-WINS DGw" into "C Wins Dgw"
    and "EHA Clinics REACH" into "Eha Clinics Reach". A visible identifier
    cannot be mistaken for a considered name; a mangled name can.

    Hiding them instead was the worse option: a Partner menu of ten small
    partners, omitting the ones doing 92% of the delivery, would look complete.
    """

    named = False
    funder_slug = ""

    def __init__(self, slug: str):
        self.slug = slug

    @property
    def display_name(self) -> str:
        return self.slug


def _delivery_partner_slugs() -> set:
    """Every org slug that actually appears on an opportunity.

    The authoritative set of delivery partners, which is a superset of the orgs
    Connect names for us -- see ``_UnnamedOrg``.
    """
    return set(PulseOpportunity.objects.exclude(org_slug="").values_list("org_slug", flat=True).distinct())


def _resolve_org(slug: str):
    """A partner by slug, named if Connect told us the name.

    Resolving only against ``PulseOrganization`` would silently ignore the
    filter for 64 of 74 partners -- selecting one would leave the whole
    portfolio on screen under that partner's name, which is the worst available
    outcome: a filter that appears to work and does not.
    """
    row = PulseOrganization.objects.filter(slug=slug).first()
    if row is not None:
        return row
    return _UnnamedOrg(slug) if slug in _delivery_partner_slugs() else None


def _partner_names_allowed(request) -> bool:
    """Whether this response may name delivery partners. **Fails closed.**

    A public link can be minted with ``show_partner_names=False``, and
    ``PulsePublicToken`` has always documented what that means: the page renders
    partners "as descriptors ('a partner in northern Nigeria') instead of
    names". That promise was never implemented -- the flag reached
    ``window.PULSE_CONFIG`` and no card read it -- which was harmless only while
    nothing on screen carried partner identity.

    Enforced HERE rather than in the page, because **this read API is
    unauthenticated**: a clean ``curl`` of ``/labs/pulse/api/summary/`` returns
    200 with the full payload, which it has to, since a public token page has no
    session and its JS still needs to call it. So a client that merely *hid* the
    names would still have been sent them, and an anonymised link's holder could
    read them out of the network tab -- or simply drop the token and ask again.

    Hence the default is **deny**, not allow. Partner names require positive
    authorisation: a labs session, or a token minted to permit them. An
    anonymous caller gets exactly what it gets today, which is a payload with no
    partner identity in it -- so nothing that works now breaks, and naming a
    partner becomes something someone has to be entitled to.
    """
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        return True
    raw = (request.GET.get("token") or "").strip()
    if not raw:
        return False
    token = PulsePublicToken.objects.filter(token=raw).first()
    return bool(token and token.is_usable and token.show_partner_names)


def _org_label(org, *, country: str = "", allowed: bool = True, index: int = 0) -> str:
    """A partner's name, or the anonymous stand-in used in its place.

    Anonymised partners still need to be told apart -- a filter menu of twenty
    identical "a partner in Nigeria" entries is unusable -- so the stand-in
    carries a stable index over the menu's deterministic order. The index is an
    arbitrary label, not a derived identifier: it says "these are different
    partners" without saying who either of them is.
    """
    if allowed:
        return org.display_name if hasattr(org, "display_name") else (org.name or org.slug)
    where = COUNTRY_NAMES.get(country, "")
    return f"Partner {index} · {where}" if where else f"Partner {index}"


def _org_menu(request):
    """Partners offered in the filter, largest delivery first.

    Empty for a caller not entitled to partner identity. A menu is a list of
    named partners; there is no version of it that both offers the drill-down
    and withholds who the partners are, because "Partner 3 · Nigeria, CHC, 3
    opportunities, $640k" is re-identifiable from a couple of public facts.
    Anonymised links therefore lose this control rather than get a leaky
    version of it, and ``_program_scope`` ignores ``?org=`` for them too.

    Mirrors ``_program_menu``'s hygiene, because the same two traps apply: an
    org with no ingested delivery resolves to a blank screen, and an org whose
    only work is internal scaffolding should not be offered to a funder. The
    latter is judged by the org's programmes rather than by its own name --
    Connect marks test *programmes*, not test orgs.
    """
    if not _partner_names_allowed(request):
        return []

    # Scaffolding is excluded by dropping test *opportunities* at the source,
    # rather than by asking whether the org owns a non-test programme.
    #
    # Those are not the same question, and the difference matters: under
    # Connect's managed model a programme belongs to the *managing* org while the
    # opportunities under it belong to the *delivering* partners. So
    # `PulseProgram.org_slug` is the manager, `PulseOpportunity.org_slug` is who
    # actually did the work, and judging a partner by the programmes it owns
    # would have hidden almost every real delivery partner -- they own none.
    #
    # Filtering the opportunities also fixes the volume: a partner's menu entry
    # now counts real delivery only, so a test programme's 9,035 visits cannot
    # inflate it, and an org whose only work is a test falls out for free by
    # summing to zero.
    test_pids = set(PulseProgram.objects.filter(is_test=True).values_list("program_id", flat=True))
    real_opps = PulseOpportunity.objects.exclude(org_slug="").exclude(program_id__in=test_pids)

    rows = (
        real_opps.values("org_slug")
        .annotate(visits=Sum("lifetime_visit_count"), opps=Count("id"))
        .filter(visits__gt=0)
    )
    by_slug = {r["org_slug"]: r for r in rows}
    if not by_slug:
        return []

    recent = {
        r["org_slug"]: r["n"]
        for r in PulseEvent.objects.exclude(org_slug="").values("org_slug").annotate(n=Count("id"))
    }
    # Modal country per partner, so the menu can say where a partner works.
    country_of = {r["org_slug"]: r["country"] for r in real_opps.exclude(country="").values("org_slug", "country")}

    # Money and delivery per partner, carried on the menu row rather than
    # fetched when one is pointed at. The card this feeds appears on hover, and a
    # request per hover would put a query behind a mouse movement -- so each row
    # arrives complete enough to draw the whole card. Two grouped queries against
    # the now-indexed org_slug, both bounded by the number of partners.
    money_of = {
        r["org_slug"]: r
        for r in PulseWork.objects.exclude(org_slug="")
        .values("org_slug")
        .annotate(
            works=Count("id"),
            approved=Count("id", filter=Q(status="approved")),
            usd=Sum("usd_to_worker"),
            usd_org=Sum("usd_to_org"),
        )
    }
    spark_of = _weekly_spark_by()

    # Built from who actually DELIVERS, with names joined on where Connect gave
    # us one. Iterating PulseOrganization instead would list only the 10
    # partners the poller is a member of and omit the 64 doing 92% of the work
    # -- see _UnnamedOrg.
    named_of = {o.slug: o for o in PulseOrganization.objects.filter(slug__in=by_slug)}

    menu = []
    for slug in by_slug:
        org = named_of.get(slug) or _UnnamedOrg(slug)
        # Connect names 10 of these; the master Organizations list supplies the
        # rest and, more importantly, says which Connect orgs are the SAME real
        # partner -- Solina Health runs both `solina` and `connect-nigeria`.
        partner = resolve_partner(org.slug, org.display_name if org.named else "")
        country = country_of.get(org.slug, "")
        m = money_of.get(org.slug) or {}
        worker = float(m.get("usd") or 0)
        org_share = float(m.get("usd_org") or 0)
        approved = m.get("approved") or 0
        works = m.get("works") or 0
        menu.append(
            {
                "slug": org.slug,
                "name": org.display_name,
                # Whether that name is Connect's or just the slug standing in
                # for one. The display marks the difference rather than letting
                # an identifier pass as a considered name.
                "named": bool(getattr(org, "named", False)) or bool(partner["parent"]),
                # The real partner behind this workspace, when we can say so at
                # high confidence. Several Connect orgs can share one.
                "partner": partner["parent"],
                "partner_short": partner["short"],
                "partner_evidence": partner["why"],
                "funder": org.funder_slug,
                "country": country,
                "opportunities": by_slug[org.slug]["opps"],
                "visits": by_slug[org.slug]["visits"],
                "recent_events": recent.get(org.slug, 0),
                "works": works,
                "approved": approved,
                # Share of this partner's work that survived the checks. The
                # denominator is every unit they submitted, not just the paid
                # ones, or the figure would be 100% for everybody.
                "approval_rate": (approved / works) if works else None,
                "usd": worker,
                "usd_org": org_share,
                "usd_total": worker + org_share,
                "rate": ((worker + org_share) / approved) if approved else None,
                "spark": spark_of.get(org.slug, []),
            }
        )
    menu.sort(key=lambda m: (-(1 if m["recent_events"] else 0), -m["visits"]))
    return menu


def _weekly_spark_by(field: str = "org_slug", qs=None) -> dict:
    """A fixed-length weekly delivery series per group, oldest bucket first.

    Padded to ``WEEKLY_WEEKS`` slots with zeroes for the weeks a partner did
    nothing, so every partner's sparkline shares one x-axis. Without the
    padding a partner active for three weeks and one active for six months
    would draw the same width of chart, which makes a new partner look
    established and an ending one look continuous.
    """
    from django.db.models.functions import TruncWeek

    now = timezone.now()
    start = (now - timedelta(weeks=WEEKLY_WEEKS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start - timedelta(days=start.weekday())

    base = PulseWork.objects.all() if qs is None else qs
    rows = (
        base.filter(created_ts__gte=start)
        .annotate(bucket=TruncWeek("created_ts"))
        .values(field, "bucket")
        .annotate(n=Count("id"))
    )
    out: dict = {}
    for r in rows:
        if r["bucket"] is None or r[field] in (None, ""):
            continue
        idx = int((r["bucket"] - start).days // 7)
        if not (0 <= idx < WEEKLY_WEEKS):
            continue
        out.setdefault(r[field], [0] * WEEKLY_WEEKS)[idx] = r["n"]
    return out


def _service_menu():
    """Delivery types offered in the filter, largest first.

    Built from what has actually been delivered rather than from
    ``SERVICE_LABELS``, so the menu can never offer a type that resolves to an
    empty screen -- the table carries 17 labels and only 12 have any work
    against them.

    Counts come from opportunities (lifetime, free) and events (recent), the
    same pair the programme menu uses, so "no recent delivery" means the same
    thing in both menus.
    """
    rows = (
        PulseOpportunity.objects.exclude(service_slug="")
        .values("service_slug")
        .annotate(visits=Sum("lifetime_visit_count"), opps=Count("id"))
        .filter(visits__gt=0)
    )
    recent = {
        r["service_slug"]: r["n"]
        for r in PulseEvent.objects.exclude(service_slug="").values("service_slug").annotate(n=Count("id"))
    }
    menu = [
        {
            "slug": r["service_slug"],
            "name": service_label(r["service_slug"]),
            "opportunities": r["opps"],
            "visits": r["visits"],
            "recent_events": recent.get(r["service_slug"], 0),
        }
        for r in rows
    ]
    menu.sort(key=lambda m: (-(1 if m["recent_events"] else 0), -m["visits"]))
    return menu


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


WEEKLY_WEEKS = 26


def _activity_strip(sc, hours: int = 24 * 30) -> list:
    """Hourly delivery across the retention window, for the range picker.

    Drawn from the rollups, which exist precisely so no card scans raw events.
    Its purpose is discovery: a partner with two visits all week should LOOK
    like that before anyone drags a range over it, rather than after.
    """
    since = timezone.now() - timedelta(hours=hours)
    return [
        {"t": int(r["bucket_hour"].timestamp()), "n": r["n"]}
        for r in sc["rollups"]
        .filter(bucket_hour__gte=since)
        .values("bucket_hour")
        .annotate(n=Sum("n"))
        .order_by("bucket_hour")
        if r["bucket_hour"] is not None
    ]


def _weekly_series(sc):
    """Weekly delivery and money for the current scope, from the works spine.

    **Not from the rollups.** Rollups aggregate ``PulseEvent``, which is capped
    at ``PULSE_EVENT_RETENTION_DAYS`` (30) -- so anything drawn from them is a
    one-month window whatever axis label sits under it, and "performance over
    time" over 30 days is a month, not a trend.

    ``PulseWork`` is the spine that carries full history (~53 B/row, no form
    JSON, which is why all 1.65M visits' worth fits in ~87 MB), so a partner's
    trajectory over half a year costs one grouped query against an indexed
    ``created_ts``.

    Returns whole weeks only, oldest first. The current partial week is included
    and flagged, because dropping it makes a live trend look like it stopped and
    keeping it unmarked makes every trend look like it just fell off a cliff.
    """
    from django.db.models.functions import TruncWeek

    since = timezone.now() - timedelta(weeks=WEEKLY_WEEKS)
    rows = (
        sc["works"]
        .filter(created_ts__gte=since)
        .annotate(bucket=TruncWeek("created_ts"))
        .values("bucket")
        .annotate(
            works=Count("id"),
            approved=Count("id", filter=Q(status="approved")),
            usd=Sum("usd_to_worker"),
            usd_org=Sum("usd_to_org"),
        )
        .order_by("bucket")
    )
    out = []
    for r in rows:
        if r["bucket"] is None:
            continue
        worker = float(r["usd"] or 0)
        org = float(r["usd_org"] or 0)
        out.append(
            {
                "t": int(r["bucket"].timestamp()),
                "works": r["works"],
                "approved": r["approved"],
                "usd": worker,
                "usd_org": org,
                "usd_total": worker + org,
            }
        )
    # Flag the trailing partial week rather than letting it read as a collapse.
    if out:
        week_start = timezone.now() - timedelta(days=timezone.now().weekday())
        cutoff = int(week_start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        for row in out:
            row["partial"] = row["t"] >= cutoff
    return out


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
        # The headline figure is everything verified delivery has moved: the
        # worker's payout AND the delivery organisation's share. Showing only
        # the worker side understated the platform by 47% on real data -- the
        # org share is $422,889 against the workers' $480,144, not a rounding
        # error.
        #
        # These two are ADDITIVE, which is the one thing worth being sure of
        # before summing them -- an inclusive figure would double-count. Two
        # independent confirmations from Connect's own source
        # (opportunity/utils/completed_work.py):
        #
        #   1. They are computed from separate payment-unit fields --
        #      `approved_count * payment_unit.amount` for the worker,
        #      `approved_count * payment_unit.org_amount` for the org.
        #   2. Connect's invoice generator defines the billed total as exactly
        #      this sum: `"total_amount_usd": flw_usd + org_usd`.
        #
        # So this is not our interpretation of the money; it is the arithmetic
        # Connect invoices on. Note org_amount is only set for *managed*
        # opportunities, so `to_orgs` covers a subset of the portfolio -- which
        # is why the split stays on screen beside the total rather than being
        # folded away into one number.
        total_paid = float(money["to_workers"] or 0) + float(money["to_orgs"] or 0)
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
                "usd_org": float(row["usd_org"] or 0),
                "usd_total": float(row["usd"] or 0) + float(row["usd_org"] or 0),
            }
            for row in sc["works"]
            .exclude(country="")
            .values("country")
            .annotate(n=Count("id"), usd=Sum("usd_to_worker"), usd_org=Sum("usd_to_org"))
        ]
        money_by_country.sort(key=lambda r: -r["usd_total"])
        # Country comes from the opportunity, and Connect leaves it blank on
        # most of them -- `pulse_backfill --countries` resolves it by sampling a
        # visit's GPS, but any opportunity with no ingested visit stays blank.
        # The excluded rows used to just vanish, so the panel rendered
        # full-width bars over 6% of the money and read as the whole picture.
        # Report the remainder instead: a consumer can then say what the
        # breakdown covers rather than implying it covers everything.
        country_usd = sum(r["usd"] for r in money_by_country)
        country_total = sum(r["usd_total"] for r in money_by_country)
        country_works = sum(r["works"] for r in money_by_country)
        money_country_unattributed = {
            "works": (money["works"] or 0) - country_works,
            "usd": float(money["to_workers"] or 0) - country_usd,
            "usd_share": (country_usd / float(money["to_workers"]) if money["to_workers"] else 0),
            "usd_total": total_paid - country_total,
            "usd_total_share": (country_total / total_paid if total_paid else 0),
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
                "usd_org": float(row["usd_org"] or 0),
                "usd_total": float(row["usd"] or 0) + float(row["usd_org"] or 0),
                # Worker payout per approved unit -- what the unit-economics
                # card ranks. The all-in figure per unit is total_rate.
                "rate": (float(row["usd"] or 0) / row["approved"]) if row["approved"] else None,
                "total_rate": (
                    (float(row["usd"] or 0) + float(row["usd_org"] or 0)) / row["approved"]
                    if row["approved"]
                    else None
                ),
            }
            for row in sc["works"]
            .exclude(service_slug="")
            .values("service_slug")
            .annotate(
                n=Count("id"),
                approved=Count("id", filter=Q(status="approved")),
                usd=Sum("usd_to_worker"),
                usd_org=Sum("usd_to_org"),
            )
        ]
        money_by_service.sort(key=lambda r: -r["usd_total"])
        money_by_service = money_by_service[:12]

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
                "org": (
                    {
                        "slug": sc["org"].slug,
                        "name": sc["org"].display_name,
                        "named": bool(getattr(sc["org"], "named", False)),
                        "funder": sc["org"].funder_slug,
                    }
                    if sc["org"] is not None
                    else None
                ),
                "service": ({"slug": sc["service"], "name": service_label(sc["service"])} if sc["service"] else None),
                "programs": _program_menu(),
                "services": _service_menu(),
                "orgs": _org_menu(request),
                "weekly": _weekly_series(sc),
                "activity": _activity_strip(sc),
                "retention_days": getattr(settings, "PULSE_EVENT_RETENTION_DAYS", 30),
                "money": {
                    "to_workers": float(money["to_workers"] or 0),
                    "to_orgs": float(money["to_orgs"] or 0),
                    "total_paid": total_paid,
                    "works": money["works"] or 0,
                    "approved_works": approved_works,
                    "usd_per_approved_work": (
                        float(money["to_workers"] or 0) / approved_works if approved_works else 0
                    ),
                    "total_per_approved_work": (total_paid / approved_works if approved_works else 0),
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


def _grid_for(sc):
    """Density cells for the current scope, and whether the match is exact.

    Cells key on programme now, so a filtered map narrows its accumulated
    geography the same way its points do. Cells folded before that carry a null
    programme and can only be matched on delivery type -- which is why a
    Nigeria-only programme could light up Cameroon and DR Congo beside a header
    reading "COUNTRIES 1".

    Those legacy cells are re-derivable rather than lost: the events they came
    from always carried program_id, the fold just never selected it, and visits
    can be re-fetched from Connect. Until that runs they stand in, and the
    response says which of the two it gave you.
    """
    cells = PulseGridCell.objects.all()

    # Unlike the partner filter, this one is exact: cells key on service_slug
    # directly, so the accumulated geography narrows the same way the live
    # points do with nothing inferred.
    if sc["service"]:
        cells = cells.filter(service_slug=sc["service"])

    # A partner has no column on the cell, but it owns programmes and cells key
    # on programme -- so the accumulated geography narrows through that. It is
    # only as complete as the org's programme coverage: an opportunity with no
    # programme folded a null-programme cell, which cannot be attributed back to
    # a partner. Declared as inexact rather than quietly under-drawn, the same
    # way the programme path declares its legacy cells.
    if sc["org"] is not None:
        pids = list(sc["opps"].exclude(program_id=None).values_list("program_id", flat=True).distinct())
        cells = cells.filter(program_id__in=pids) if pids else cells.none()
        unattributed = sc["opps"].filter(program_id=None).exists()
        if sc["program"] is None:
            return cells, not unattributed

    if sc["program"] is None:
        return cells, True

    pid = sc["program"].program_id
    exact = cells.filter(program_id=pid)
    if exact.exists():
        return exact, True
    if sc["grid_service"]:
        return cells.filter(service_slug=sc["grid_service"], program_id=None), False
    return cells.none(), False


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
        cells, exact = _grid_for(sc)
        cells = cells.order_by("-n")[:limit]

        rows = [
            [c.lat_q, c.lon_q, c.n, c.approved_n, c.flagged_n, c.country or None, c.service_slug or None, c.program_id]
            for c in cells
        ]
        return JsonResponse(
            {
                "fields": ["lat_q", "lon_q", "n", "approved_n", "flagged_n", "country", "service", "program_id"],
                # Cells are quantised to 1/100 degree; divide to get coordinates.
                "quantum": 100,
                "cells": rows,
                "total_points": sum(r[2] for r in rows),
                "truncated": len(rows) >= limit,
                # Whether the density shown is this programme's own history or
                # a delivery-type approximation standing in for cells folded
                # before programme attribution existed.
                "filtered_by": (sc["program"].program_id if exact and sc["program"] else sc["grid_service"]),
                "exact": exact,
            }
        )


class EventsView(View):
    """Live tail. ``since`` is a visit id, matching Connect's own cursor semantics."""

    def get(self, request):
        since = request.GET.get("since")
        limit = min(int(request.GET.get("limit", 500)), MAX_EVENTS)
        partners = _partner_names_allowed(request)

        qs = _program_scope(request)["events"].order_by("connect_visit_id")
        if since:
            qs = qs.filter(connect_visit_id__gt=int(since))
        else:
            qs = qs.order_by("-connect_visit_id")[:limit]
            rows = sorted(qs, key=lambda e: e.connect_visit_id)
            return JsonResponse(_events_payload(rows, partners))

        return JsonResponse(_events_payload(list(qs[:limit]), partners))


def _events_payload(rows, partners: bool = False) -> dict:
    return {
        "fields": EVENT_FIELDS,
        "events": [_event_row(e, partners) for e in rows],
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
        limit = min(int(request.GET.get("limit", MAX_EVENTS)), MAX_EVENTS)

        # An explicit range wins over the rolling one. The rolling window only
        # bounds the QUERY -- the loop a viewer actually sees is the span
        # between the first and last event that came back, so a partner with two
        # visits ten minutes apart loops over ten minutes however many hours
        # were asked for. Being able to name the range is the only way to say
        # "show me the whole of Tuesday" when Tuesday is mostly empty.
        raw_from = (request.GET.get("from") or "").strip()
        raw_to = (request.GET.get("to") or "").strip()
        start = end = None
        if raw_from and raw_to:
            try:
                start = datetime.fromtimestamp(int(raw_from), tz=dt_timezone.utc)
                end = datetime.fromtimestamp(int(raw_to), tz=dt_timezone.utc)
            except (TypeError, ValueError, OSError, OverflowError):
                start = end = None
            if start and end and end <= start:
                start = end = None
        if start is None:
            hours = min(int(request.GET.get("hours", DEFAULT_REPLAY_HOURS)), 24 * 14)
            end = timezone.now()
            start = end - timedelta(hours=hours)
        hours = max((end - start).total_seconds() / 3600.0, 0.0)

        sc = _program_scope(request)
        partners = _partner_names_allowed(request)
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
                "events": [_event_row(e, partners) for e in rows],
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


# ---------------------------------------------------------------------------
# Drill-down: a partner, and the workers inside it
# ---------------------------------------------------------------------------
#
# These are on-demand rather than folded into `summary`, because summary is
# polled continuously by every open display and these answer a question nobody
# has asked until they click.
#
# **Workers are opaque.** Connect's export publishes `username` already hashed
# (`985770f1bf2079f58119`), so a worker here is an anonymous identifier with a
# delivery record attached -- there is no name or phone anywhere in Pulse to
# show, by construction. That is what makes a per-worker view safe to put on a
# funder screen at all.

WORKER_ROSTER_LIMIT = 250


def _worker_roster(sc) -> list:
    """Every worker who has delivered inside the current scope.

    Money and volume come from the works spine, which carries full history;
    recency, flags and geography come from events, which are the rolling
    retention window. Neither alone answers "who delivers for this partner and
    how are they doing".
    """
    money = {
        r["worker_hash"]: r
        for r in sc["works"]
        .exclude(worker_hash="")
        .values("worker_hash")
        .annotate(
            works=Count("id"),
            approved=Count("id", filter=Q(status="approved")),
            usd=Sum("usd_to_worker"),
        )
    }
    recent = {
        r["worker_hash"]: r
        for r in sc["events"]
        .exclude(worker_hash="")
        .values("worker_hash")
        .annotate(
            events=Count("id"),
            flagged=Count("id", filter=Q(flagged=True)),
            last_ts=Max("field_ts"),
        )
    }
    country_of = {
        r["worker_hash"]: r["country"]
        for r in sc["events"].exclude(worker_hash="").exclude(country="").values("worker_hash", "country")
    }

    rows = []
    for h in set(money) | set(recent):
        m = money.get(h) or {}
        e = recent.get(h) or {}
        works = m.get("works") or 0
        approved = m.get("approved") or 0
        events = e.get("events") or 0
        flagged = e.get("flagged") or 0
        rows.append(
            {
                # Truncated the same way the ticker truncates it: enough to tell
                # two workers apart on screen, and never the whole identifier.
                "worker": h[:6],
                "worker_full": h,
                "works": works,
                "approved": approved,
                "approval_rate": (approved / works) if works else None,
                "events": events,
                "flagged": flagged,
                "flag_rate": (flagged / events) if events else None,
                "usd": float(m.get("usd") or 0),
                "country": country_of.get(h, ""),
                "last_ts": int(e["last_ts"].timestamp()) if e.get("last_ts") else None,
            }
        )
    rows.sort(key=lambda r: (-(r["last_ts"] or 0), -r["works"]))
    return rows[:WORKER_ROSTER_LIMIT]


def _opportunity_roster(sc) -> list:
    """The partner's engagements, one row each.

    A partner is not one thing. On real data they run anywhere from one
    opportunity to ninety-one, and a window that renders both identically
    answers "how is this partner doing" without ever saying "at what".

    Volume and money come from the works spine; recency and worker count from
    events. `lifetime_visits` comes off the opportunity itself, which Connect
    keeps current for free, so it survives the event retention window that the
    other two figures are bounded by.
    """
    money = {
        r["opportunity_id"]: r
        for r in sc["works"]
        .values("opportunity_id")
        .annotate(
            works=Count("id"),
            approved=Count("id", filter=Q(status="approved")),
            usd=Sum("usd_to_worker"),
            usd_org=Sum("usd_to_org"),
        )
    }
    live = {
        r["opportunity_id"]: r
        for r in sc["events"]
        .values("opportunity_id")
        .annotate(
            events=Count("id"),
            flagged=Count("id", filter=Q(flagged=True)),
            workers=Count("worker_hash", distinct=True),
            last_ts=Max("field_ts"),
        )
    }
    spark_of = _weekly_spark_by("opportunity_id", qs=sc["works"])

    rows = []
    for opp in sc["opps"]:
        m = money.get(opp.opportunity_id) or {}
        e = live.get(opp.opportunity_id) or {}
        works = m.get("works") or 0
        approved = m.get("approved") or 0
        events = e.get("events") or 0
        worker = float(m.get("usd") or 0)
        org_share = float(m.get("usd_org") or 0)
        rows.append(
            {
                "id": opp.opportunity_id,
                # Opportunity names are operational ("KMC - UG - PIPN - P1 -
                # Apr 26") and shown verbatim: it is what the partner and
                # Connect both call it, and shortening it in labs would invent
                # a name for something that already has one.
                "name": opp.name or f"Opportunity {opp.opportunity_id}",
                "service": opp.service_slug,
                "service_name": service_label(opp.service_slug),
                "country": opp.country,
                "active": bool(opp.is_active),
                "end_date": opp.end_date.isoformat() if opp.end_date else None,
                "visits": opp.lifetime_visit_count or 0,
                "works": works,
                "approved": approved,
                "approval_rate": (approved / works) if works else None,
                "flagged": e.get("flagged") or 0,
                "flag_rate": ((e.get("flagged") or 0) / events) if events else None,
                "workers": e.get("workers") or 0,
                "usd": worker,
                "usd_total": worker + org_share,
                "rate": ((worker + org_share) / approved) if approved else None,
                "last_ts": int(e["last_ts"].timestamp()) if e.get("last_ts") else None,
                "spark": spark_of.get(opp.opportunity_id, []),
            }
        )
    # Delivering now first, then by lifetime volume -- the same ordering rule
    # the partner and programme menus use, so "recent" means one thing.
    rows.sort(key=lambda r: (-(1 if r["last_ts"] else 0), -r["visits"]))
    return rows


class PartnerView(View):
    """Everything a partner window shows, for one partner.

    Entitlement is the same as the partner menu's: this is a named partner's
    commercial and quality record, so an anonymised link cannot have it.
    """

    def get(self, request):
        if not _partner_names_allowed(request):
            return JsonResponse({"error": "not_authorised"}, status=403)

        sc = _program_scope(request)
        org = sc["org"]
        if org is None:
            return JsonResponse({"error": "unknown_partner"}, status=404)

        partner = resolve_partner(org.slug, org.display_name if org.named else "")
        works = sc["works"]
        agg = works.aggregate(
            works=Count("id"),
            approved=Count("id", filter=Q(status="approved")),
            usd=Sum("usd_to_worker"),
            usd_org=Sum("usd_to_org"),
        )
        worker = float(agg["usd"] or 0)
        org_share = float(agg["usd_org"] or 0)
        approved = agg["approved"] or 0

        roster = _worker_roster(sc)
        return JsonResponse(
            {
                "generated_at": timezone.now().isoformat(),
                "ingest": _ingest_state(),
                "partner": {
                    "slug": org.slug,
                    "workspace": org.display_name,
                    "name": partner["parent"] or org.display_name,
                    "named": bool(partner["parent"]) or bool(getattr(org, "named", False)),
                    "funder": getattr(org, "funder_slug", ""),
                    "evidence": partner["why"],
                },
                "scope": _scope_for(sc),
                "money": {
                    "to_workers": worker,
                    "to_orgs": org_share,
                    "total_paid": worker + org_share,
                    "works": agg["works"] or 0,
                    "approved_works": approved,
                    "rate": ((worker + org_share) / approved) if approved else None,
                },
                "by_status": {
                    r["status"]: r["n"] for r in works.values("status").annotate(n=Count("id")).order_by("-n")
                },
                "by_service": [
                    {
                        "service": r["service_slug"],
                        "name": service_label(r["service_slug"]),
                        "works": r["n"],
                        "usd": float(r["usd"] or 0),
                    }
                    for r in works.exclude(service_slug="")
                    .values("service_slug")
                    .annotate(n=Count("id"), usd=Sum("usd_to_worker"))
                    .order_by("-n")[:8]
                ],
                "weekly": _weekly_series(sc),
                "opportunities": _opportunity_roster(sc),
                "workers": roster,
                "worker_count": len(roster),
                "workers_truncated": len(roster) >= WORKER_ROSTER_LIMIT,
            }
        )


class WorkerView(View):
    """One worker's delivery record.

    The identifier is Connect's own hash. Callers pass the truncated form the
    rest of the UI shows, so the lookup is a prefix match -- and a prefix that
    matches more than one worker is refused rather than silently answered with
    whichever came first, which would blend two people's records.
    """

    def get(self, request):
        if not _partner_names_allowed(request):
            return JsonResponse({"error": "not_authorised"}, status=403)

        raw = (request.GET.get("w") or "").strip()
        if not raw:
            return JsonResponse({"error": "no_worker"}, status=400)

        sc = _program_scope(request)
        matches = list(
            sc["events"].filter(worker_hash__startswith=raw).values_list("worker_hash", flat=True).distinct()[:3]
        ) or list(sc["works"].filter(worker_hash__startswith=raw).values_list("worker_hash", flat=True).distinct()[:3])
        if not matches:
            return JsonResponse({"error": "unknown_worker"}, status=404)
        if len(matches) > 1:
            return JsonResponse({"error": "ambiguous_worker", "candidates": len(matches)}, status=409)

        full = matches[0]
        events = sc["events"].filter(worker_hash=full)
        works = sc["works"].filter(worker_hash=full)

        agg = works.aggregate(
            works=Count("id"),
            approved=Count("id", filter=Q(status="approved")),
            usd=Sum("usd_to_worker"),
        )
        ev = events.aggregate(n=Count("id"), flagged=Count("id", filter=Q(flagged=True)), last_ts=Max("field_ts"))

        from django.db.models.functions import TruncWeek

        weekly = [
            {
                "t": int(r["bucket"].timestamp()),
                "works": r["n"],
                "usd": float(r["usd"] or 0),
            }
            for r in works.filter(created_ts__gte=timezone.now() - timedelta(weeks=WEEKLY_WEEKS))
            .annotate(bucket=TruncWeek("created_ts"))
            .values("bucket")
            .annotate(n=Count("id"), usd=Sum("usd_to_worker"))
            .order_by("bucket")
            if r["bucket"] is not None
        ]

        return JsonResponse(
            {
                "generated_at": timezone.now().isoformat(),
                "worker": raw[:6],
                "org": sc["org"].slug if sc["org"] is not None else "",
                "totals": {
                    "works": agg["works"] or 0,
                    "approved": agg["approved"] or 0,
                    "approval_rate": ((agg["approved"] or 0) / agg["works"]) if agg["works"] else None,
                    "usd": float(agg["usd"] or 0),
                    "events": ev["n"] or 0,
                    "flagged": ev["flagged"] or 0,
                    "flag_rate": ((ev["flagged"] or 0) / ev["n"]) if ev["n"] else None,
                    "last_ts": int(ev["last_ts"].timestamp()) if ev.get("last_ts") else None,
                },
                "by_status": {
                    r["status"]: r["n"] for r in works.values("status").annotate(n=Count("id")).order_by("-n")
                },
                "by_flag": {
                    r["flag_type"]: r["n"]
                    for r in events.exclude(flag_type="").values("flag_type").annotate(n=Count("id")).order_by("-n")
                },
                "by_service": [
                    {"service": r["service_slug"], "name": service_label(r["service_slug"]), "works": r["n"]}
                    for r in works.exclude(service_slug="")
                    .values("service_slug")
                    .annotate(n=Count("id"))
                    .order_by("-n")[:6]
                ],
                "weekly": weekly,
                # Same shape and same town-scale treatment the ticker already
                # applies; adds no exposure beyond what /api/events/ returns.
                "recent": [
                    {
                        "t": int(e.field_ts.timestamp()),
                        "lat": round(e.lat, 4) if e.lat is not None else None,
                        "lon": round(e.lon, 4) if e.lon is not None else None,
                        "status": e.status,
                        "flag": e.flag_type or None,
                        "service": e.service_slug or None,
                    }
                    for e in events.order_by("-field_ts")[:60]
                ],
            }
        )
