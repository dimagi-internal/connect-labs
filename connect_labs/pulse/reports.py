"""Deriving a donor report's figures from the works spine.

Every number here is verified, reimbursed delivery. The spine is ``PulseWork``
filtered to ``status="approved"`` -- not ``PulseEvent`` -- and the reasons are
worth keeping next to the code, because "count the visits" is the obvious wrong
answer and it fails in two unrelated ways at once:

* **It counts work nobody paid for.** Recorded visits include pending, rejected
  and over-limit ones. A funder's total should not contain delivery Connect
  declined to reimburse.
* **It reports zero for anything older than a month.** Events carry
  ``form_json`` at ~1,346 B/row and are retained ~30 days; works are 53 B/row
  and kept in full. A report on last quarter keyed to events would render a
  page of zeroes without erroring.

**Verification rate comes from the same spine, and is therefore permanent.**
Works carry ``status`` for every submitted item, not only approved ones --
measured on prod: 942,531 approved of 1,012,911 works, i.e. 93.0%, alongside
32,134 ``over_limit``, 22,479 ``rejected``, 15,357 ``pending`` and 410
``incomplete``. So "what share of submitted work passed verification" needs no
event rows and works for any window.

The one thing the works spine cannot answer is *why* a failure failed:
``flagged`` / ``flag_type`` / ``review_status`` exist only on events, because
Connect's ``completed_works`` export does not carry them. So the flag-reason
breakdown is computed separately and is present only when the window falls
inside retention -- ``quality.available`` says which, and the template omits
that panel rather than printing zeroes that would read as "nothing was
flagged". The headline verification rate is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone

from connect_labs.pulse.models import PulseGridCell, PulseOrganization, PulseProgram, PulseReport
from connect_labs.pulse.normalize import service_label


@dataclass
class Deliverable:
    """One "what we delivered" line, resolved to a number."""

    label: str
    description: str = ""
    value: float | None = None
    basis: str = PulseReport.BASIS_SERVICES
    multiplier: float = 1.0
    # True when a human typed the figure rather than Pulse deriving it. Carried
    # through to the editor so the author always knows which is which.
    is_manual: bool = False
    emphasis: bool = False

    @property
    def basis_label(self) -> str:
        return dict(PulseReport.BASIS_CHOICES).get(self.basis, self.basis)


# Connect's completed-work statuses, in the order a donor report should read
# them, with what each one actually means to a funder. `over_limit` is the one
# worth not lumping into "failed": the work was delivered and verified, it just
# fell beyond the funded ceiling, which is a statement about the budget rather
# than about the delivery.
WORK_STATUS_MEANINGS = [
    ("approved", "Verified and reimbursed", "verified"),
    ("over_limit", "Delivered beyond the funded limit", "over"),
    ("rejected", "Did not pass verification", "failed"),
    ("pending", "Still in review", "open"),
    ("incomplete", "Incomplete submission", "open"),
]


@dataclass
class Verification:
    """Did the work pass? Two levels, because they answer different questions.

    **Visit level** (from ``PulseRollup``) is the one a donor means by "verified
    service deliveries": one row per service contact. Rollups are hourly counts
    keyed on ``(opportunity, status)``, hold nothing at beneficiary level, and
    are never pruned -- so they outlive the events they were built from and can
    be windowed for any period Pulse has ever rolled up.

    **Work level** (from ``PulseWork``) counts payment units. For a simple
    programme the two coincide; for a longitudinal one they diverge sharply --
    measured on prod, KMC runs 8,844 works against 34,676 visits, so quoting
    works to a KMC funder understates delivery about fourfold.

    Both are reported. Neither is a substitute for the other.
    """

    # Visit level.
    visits_available: bool = False
    visits_submitted: int = 0
    visits_approved: int = 0
    visit_rate: float | None = None

    # Work / payment-unit level.
    submitted: int = 0
    approved: int = 0
    rate: float | None = None
    rows: list = field(default_factory=list)

    @property
    def rate_pct(self) -> float | None:
        return self.rate * 100 if self.rate is not None else None

    @property
    def visit_rate_pct(self) -> float | None:
        return self.visit_rate * 100 if self.visit_rate is not None else None

    @property
    def headline_rate_pct(self) -> float | None:
        """The verification rate to print. Visit level when we have it."""
        return self.visit_rate_pct if self.visits_available else self.rate_pct


@dataclass
class Metrics:
    """The derived figures. All of these are approved-and-reimbursed."""

    # Verified service contacts — the headline. Visit-level where available.
    services: int = 0
    services_are_visits: bool = False
    # Payment units approved — "care episodes" on a longitudinal programme.
    episodes: int = 0
    works: int = 0
    workers: int = 0
    verification: Verification = field(default_factory=Verification)
    usd_to_workers: float = 0.0
    usd_to_orgs: float = 0.0
    total_paid: float = 0.0
    cost_per_service: float | None = None
    org_cost_per_service: float | None = None
    first_delivery = None
    last_delivery = None
    by_service: list = field(default_factory=list)
    # Money that has actually left Connect, as distinct from accrued. Only
    # meaningful where payment_date is populated, so coverage rides along.
    paid_out_usd: float = 0.0
    paid_out_coverage: float = 0.0


@dataclass
class Quality:
    """Verification signals — event-only, hence window-dependent."""

    available: bool = False
    recorded: int = 0
    flagged: int = 0
    flag_rate: float | None = None
    by_flag: list = field(default_factory=list)
    median_sync_lag_minutes: float | None = None


def compute(report: PulseReport, sc) -> dict:
    """Everything the report template needs, for one report and its scope."""
    metrics = _metrics(sc)
    quality = _quality(sc, report)
    deliverables = resolve_deliverables(report.deliverables or [], metrics)

    from connect_labs.pulse import geo as geo_module

    grid = _grid_for_report(sc)
    geography = geo_module.resolve(sc, grid, retention_days=getattr(settings, "PULSE_EVENT_RETENTION_DAYS", 30))

    return {
        "report": report,
        "metrics": metrics,
        "quality": quality,
        "deliverables": deliverables,
        "geography": geography,
        "partner": _partner(report, sc),
        "program": _program(sc),
        "opportunities": _engagements(sc),
        "generated_at": timezone.now(),
    }


def _approved(sc):
    return sc["works"].filter(status="approved")


def _metrics(sc) -> Metrics:
    approved = _approved(sc)
    agg = approved.aggregate(
        works=Count("id"),
        services=Sum("approved_count"),
        usd=Sum("usd_to_worker"),
        usd_org=Sum("usd_to_org"),
        workers=Count("worker_hash", distinct=True),
        first=Min("created_ts"),
        last=Max("created_ts"),
    )

    m = Metrics()
    m.verification = _verification(sc)
    m.works = agg["works"] or 0

    # Payment units approved -- "care episodes completed" in a longitudinal
    # programme. `approved_count` is Connect's own figure and is the minimum
    # across required deliver units (see completed_work.py), i.e. complete sets
    # of required forms, NOT visits. It is not universally populated, so fall
    # back to counting the approved records themselves, which is the same thing
    # at one unit each and the conservative read.
    m.episodes = int(agg["services"] or 0) or m.works

    # The headline. A donor asking "how many services did you deliver" means
    # service contacts, so prefer the visit-level count and fall back to
    # payment units only where the window was never rolled up. `services_are_visits`
    # carries which, so the report labels the tile truthfully instead of calling
    # payment units "visits".
    if m.verification.visits_available:
        m.services = m.verification.visits_approved
        m.services_are_visits = True
    else:
        m.services = m.episodes
        m.services_are_visits = False

    m.workers = agg["workers"] or 0
    m.usd_to_workers = float(agg["usd"] or 0)
    m.usd_to_orgs = float(agg["usd_org"] or 0)
    # Additive, not overlapping: Connect computes them from separate
    # payment-unit fields and invoices on exactly this sum. See api.py:790.
    m.total_paid = m.usd_to_workers + m.usd_to_orgs
    m.first_delivery = agg["first"]
    m.last_delivery = agg["last"]

    if m.services:
        m.cost_per_service = m.total_paid / m.services
        m.org_cost_per_service = m.usd_to_orgs / m.services

    paid = approved.exclude(payment_date=None).aggregate(usd=Sum("usd_to_worker"), n=Count("id"))
    m.paid_out_usd = float(paid["usd"] or 0)
    # Disbursement coverage is reported rather than assumed: payment_date is
    # sparsely populated, and presenting a partial figure as "paid out" would
    # understate what a partner has actually received.
    m.paid_out_coverage = ((paid["n"] or 0) / m.works) if m.works else 0.0

    m.by_service = [
        {
            "slug": row["service_slug"],
            "name": service_label(row["service_slug"]),
            "services": int(row["services"] or 0) or row["n"],
            "usd_total": float(row["usd"] or 0) + float(row["usd_org"] or 0),
        }
        for row in approved.exclude(service_slug="")
        .values("service_slug")
        .annotate(n=Count("id"), services=Sum("approved_count"), usd=Sum("usd_to_worker"), usd_org=Sum("usd_to_org"))
    ]
    m.by_service.sort(key=lambda r: -r["services"])
    return m


def _verification(sc) -> Verification:
    """Share of submitted work that passed verification and was reimbursed.

    Counts *works*, not the units inside them, because the status is a decision
    taken per work record -- weighting by ``approved_count`` would mean summing
    an approved count over rows that were rejected, which is zero by definition
    and would silently drive the denominator toward the numerator.
    """
    counts = {row["status"]: row["n"] for row in sc["works"].values("status").annotate(n=Count("id")).order_by("-n")}
    v = Verification()
    v.submitted = sum(counts.values())
    v.approved = counts.get("approved", 0)
    if v.submitted:
        v.rate = v.approved / v.submitted

    # Visit level, from the rollups. Absent for windows Pulse never rolled up
    # (history predating the first visit backfill), in which case the report
    # falls back to the work-level rate and says which it is showing.
    visits = {row["status"]: row["n"] for row in sc["rollups"].values("status").annotate(n=Sum("n"))}
    v.visits_submitted = sum(n or 0 for n in visits.values())
    v.visits_approved = visits.get("approved") or 0
    if v.visits_submitted:
        v.visits_available = True
        v.visit_rate = v.visits_approved / v.visits_submitted

    known = {slug for slug, _, _ in WORK_STATUS_MEANINGS}
    v.rows = [
        {
            "status": slug,
            "meaning": meaning,
            "kind": kind,
            "n": counts.get(slug, 0),
            "share": (counts.get(slug, 0) / v.submitted) if v.submitted else 0,
        }
        for slug, meaning, kind in WORK_STATUS_MEANINGS
        if counts.get(slug)
    ]
    # Anything Connect starts emitting that this table has not learned about yet
    # is still counted, under its own name, rather than vanishing from a
    # breakdown whose shares are supposed to total 100%.
    for slug, n in counts.items():
        if slug not in known and n:
            v.rows.append(
                {
                    "status": slug,
                    "meaning": slug.replace("_", " ").capitalize(),
                    "kind": "open",
                    "n": n,
                    "share": n / v.submitted if v.submitted else 0,
                }
            )
    return v


def _quality(sc, report: PulseReport) -> Quality:
    """Flag detail, but only where the window is inside event retention.

    Returning ``available=False`` rather than zeroes is the whole point: a donor
    report claiming "0 flagged" over a window whose events were deleted months
    ago is a false statement, not a missing panel.
    """
    q = Quality()
    retention = getattr(settings, "PULSE_EVENT_RETENTION_DAYS", 30)
    cutoff = timezone.now() - timedelta(days=retention)

    window_from = sc.get("window_from")
    if window_from is None or window_from < cutoff:
        return q

    agg = sc["events"].aggregate(
        recorded=Count("id"),
        flagged=Count("id", filter=Q(flagged=True)),
    )
    q.recorded = agg["recorded"] or 0
    if not q.recorded:
        return q

    q.available = True
    q.flagged = agg["flagged"] or 0
    q.flag_rate = q.flagged / q.recorded
    q.by_flag = [
        {"type": row["flag_type"], "n": row["n"]}
        for row in sc["events"].exclude(flag_type="").values("flag_type").annotate(n=Count("id")).order_by("-n")[:6]
    ]
    q.median_sync_lag_minutes = _median_sync_lag(sc["events"])
    return q


def _median_sync_lag(events) -> float | None:
    """Median field-to-Connect lag in minutes — the verification-speed stat.

    Read as a bounded sample rather than a database-side percentile: the exact
    median of a capped, ordered sample is close enough for a headline stated to
    one decimal, and it costs one indexed scan instead of a window function.
    """
    rows = list(events.values_list("field_ts", "sync_ts")[:20000])
    deltas = sorted((s - f).total_seconds() / 60.0 for f, s in rows if f and s and s >= f)
    if not deltas:
        return None
    mid = len(deltas) // 2
    return deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2


def resolve_deliverables(rows: list, metrics: Metrics) -> list[Deliverable]:
    """Turn declared line-items into numbers.

    A line names a quantity a funder cares about ("ORS co-packs distributed")
    and ties it to a verified basis and a ratio. ``ORS co-packs, services x 2``
    is a claim the platform can stand behind *given* the programme's protocol;
    the protocol itself is the author's knowledge, not Connect's, which is why
    the multiplier is declared rather than inferred.
    """
    basis_values = {
        PulseReport.BASIS_SERVICES: metrics.services,
        # Payment units, not raw work records: a line reading "kits issued, one
        # per completed episode" must not silently count rejected works too.
        PulseReport.BASIS_WORKS: metrics.episodes,
        PulseReport.BASIS_WORKERS: metrics.workers,
    }

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = (row.get("label") or "").strip()
        # Enforced here and not only in the editor's POST handler: a stored
        # report can acquire a blank label by other routes (an API write, a
        # fixture, a hand-edited JSON column), and an unlabelled tile carrying
        # a real number reads to a donor as a figure nobody bothered to name.
        if not label:
            continue
        basis = row.get("basis") or PulseReport.BASIS_SERVICES
        override = row.get("override")
        try:
            multiplier = float(row.get("multiplier", 1) or 1)
        except (TypeError, ValueError):
            multiplier = 1.0

        if override not in (None, ""):
            try:
                value = float(override)
            except (TypeError, ValueError):
                value = None
            is_manual = True
        elif basis == PulseReport.BASIS_MANUAL:
            value, is_manual = None, True
        else:
            value = basis_values.get(basis, 0) * multiplier
            is_manual = False

        out.append(
            Deliverable(
                label=label,
                description=(row.get("description") or "").strip(),
                value=value,
                basis=basis,
                multiplier=multiplier,
                is_manual=is_manual,
                emphasis=bool(row.get("emphasis")),
            )
        )
    return out


def default_deliverables() -> list[dict]:
    """The starting line-items for a new report.

    One-to-one with verified delivery, which is the only ratio Pulse can assert
    without knowing the programme's protocol. The author edits from here.
    """
    return [
        {
            "label": "Verified service deliveries",
            "description": "Services delivered, checked by Connect and reimbursed.",
            "basis": PulseReport.BASIS_SERVICES,
            "multiplier": 1,
            "emphasis": True,
        },
        {
            "label": "Frontline workers deployed",
            "description": "Workers who delivered at least one reimbursed service in the window.",
            "basis": PulseReport.BASIS_WORKERS,
            "multiplier": 1,
        },
    ]


def _grid_for_report(sc):
    """Accumulated geography matching the report's scope.

    Cells key on ``(service, program)`` only -- they carry no organisation or
    opportunity -- so an org-scoped report narrows its cells as far as the cell
    key allows and no further. ``geo.Geography.is_all_time`` is what tells the
    reader that the density panel may be wider than the window.
    """
    cells = PulseGridCell.objects.all()
    if sc["program"] is not None:
        cells = cells.filter(Q(program_id=sc["program"].program_id) | Q(program_id=None))
        if sc["program"].delivery_type:
            cells = cells.filter(service_slug=sc["program"].delivery_type)
    elif sc["service"]:
        cells = cells.filter(service_slug=sc["service"])

    countries = set(sc["works"].exclude(country="").values_list("country", flat=True).distinct())
    if countries:
        cells = cells.filter(country__in=countries)
    return cells


def _partner(report: PulseReport, sc) -> dict | None:
    org = sc["org"]
    if org is None and report.org_slug:
        org = PulseOrganization.objects.filter(slug=report.org_slug).first()
    if org is None:
        return None
    return {
        "name": org.display_name if report.show_partner_names else "a delivery partner",
        "slug": org.slug,
        "funder": getattr(org, "funder_slug", ""),
        "named": bool(getattr(org, "named", False)) and report.show_partner_names,
    }


def _program(sc) -> dict | None:
    program = sc["program"]
    if program is None:
        return None
    return {
        "id": program.program_id,
        "name": program.name,
        "delivery_type": program.delivery_type,
        "service_label": service_label(program.delivery_type),
    }


def _engagements(sc) -> list[dict]:
    """The opportunities the reported work actually came from."""
    approved = _approved(sc)
    by_opp = {
        row["opportunity_id"]: row
        for row in approved.values("opportunity_id").annotate(
            services=Sum("approved_count"), n=Count("id"), usd=Sum("usd_to_worker"), usd_org=Sum("usd_to_org")
        )
    }
    rows = []
    for opp in sc["opps"]:
        hit = by_opp.get(opp.opportunity_id)
        if not hit:
            continue
        rows.append(
            {
                "id": opp.opportunity_id,
                "name": opp.name or f"Opportunity {opp.opportunity_id}",
                "service_name": service_label(opp.service_slug),
                "country": opp.country,
                "services": int(hit["services"] or 0) or hit["n"],
                "usd_total": float(hit["usd"] or 0) + float(hit["usd_org"] or 0),
            }
        )
    rows.sort(key=lambda r: -r["services"])
    return rows


def programs_for_picker() -> list[dict]:
    """Programmes a report can be scoped to, test programmes excluded."""
    return [
        {"id": p.program_id, "name": p.name or f"Programme {p.program_id}", "delivery_type": p.delivery_type}
        for p in PulseProgram.objects.filter(is_test=False).order_by("name")
    ]
