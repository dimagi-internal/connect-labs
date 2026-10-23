"""Labs usage analytics: who uses labs, for which organisation / programme /
opportunity, doing what, over time.

Everything here is a read of the audit trail (``labs_audit_event``), which
already records every authenticated page view and every data touch. This module
only rolls it up. Three things it has to get right:

**Only people.** Celery, scripts and the system write most audit rows (status
churn on workflow runs); they are not usage. Only ``web`` and ``mcp`` sources
with a user attached count, the same rule ``audit_trail.service`` applies to
product analytics.

**Scope comes from three places.** A data touch carries the opportunity /
programme the API client was scoped to. A page view carries none in rows written
before the middleware stamped ``labs_context`` onto it, so for those the scope is
read back out of the recorded query string -- the context middleware puts
``opportunity_id`` / ``program_id`` / ``organization_id`` on every scoped URL.
Organisation is a slug in labs URLs (not the integer column), so it is read from
the query string or ``metadata.organization_slug``.

**Organisation is derived, not recorded.** Most events name an opportunity or a
programme, not an organisation. Pulse's mirror (and the synthetic registry for
labs-only opps) says which programme an opportunity is in and which organisation
runs each, so the rollup walks opp -> programme -> organisation in Python, after
one grouped SQL query.

The grouped query returns one row per (day, user, opp, programme, org slug,
activity kind, feature area): small enough to roll up in Python for any window
an admin would ask for, and every table and chart on the page derives from it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Case, CharField, Count, F, Func, IntegerField, Max, Q, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, NullIf, TruncDate
from django.utils import timezone

from connect_labs.audit_trail.models import Action, AuditEvent, Source

WINDOW_CHOICES = (7, 30, 90, 180, 365)
DEFAULT_WINDOW = 30

HUMAN_SOURCES = (Source.WEB, Source.MCP)
ACTIVITY_ACTIONS = (
    Action.PAGE_VIEW,
    Action.LIST,
    Action.READ,
    Action.EXPORT,
    Action.CREATE,
    Action.UPDATE,
    Action.DELETE,
)

# Activity kinds, in display order.
VIEW, READ, CHANGE, EXPORT, AGENT = "view", "read", "change", "export", "agent"
KIND_LABELS = {
    VIEW: "Page views",
    CHANGE: "Changes",
    EXPORT: "Exports",
    AGENT: "Agent calls",
    READ: "Data reads",
}

# Feature area by path prefix; first match wins, so longer prefixes go first.
AREAS = (
    ("/labs/workflow/", "Workflows"),
    ("/labs/p/", "Pages"),
    ("/labs/pipelines/", "Pipelines"),
    ("/labs/data/", "Data explorer"),
    ("/labs/explorer/", "Data explorer"),
    ("/labs/targeting/", "Targeting"),
    ("/labs/marketplace/", "Marketplace"),
    ("/labs/pulse/", "Pulse"),
    ("/labs/settings/", "Settings"),
    ("/labs/admin/", "Labs admin"),
    ("/labs/audit-trail/", "Labs admin"),
    ("/labs/overview/", "Overview"),
    ("/audit/", "Audits"),
    ("/tasks/", "Tasks"),
    ("/solicitations/", "Solicitations"),
    ("/supply/", "Supply"),
    ("/microplans/", "Microplans"),
    ("/wa-revisit/", "Microplans"),
    ("/mopup/", "Microplans"),
    ("/funder/", "Funder dashboard"),
    ("/coverage/", "Coverage"),
    ("/custom_analysis/", "Custom analysis"),
    ("/campaign/", "Campaign"),
    ("/ai/", "AI"),
    ("/embed/", "Embedded"),
)
AGENT_AREA = "Agent (MCP)"
OTHER_AREA = "Other"

STAFF_EMAIL_SUFFIXES = ("@dimagi.com", "@dimagi-ai.com")

NO_ORG = ""  # key for activity that named no organisation


class _QueryParam(Func):
    """Postgres ``substring(text FROM pattern)``: the first capture group, or NULL."""

    output_field = CharField()

    def __init__(self, expression, param: str, value_pattern: str):
        self.pattern = rf"(?:^|&){param}=({value_pattern})(?:&|$)"
        super().__init__(expression)

    def as_sql(self, compiler, connection, **extra):
        sql, params = compiler.compile(self.source_expressions[0])
        return f"substring({sql} FROM %s)", [*params, self.pattern]


def _query_int(param: str):
    # Nine digits keeps a hand-typed id inside int4; anything longer is not an id.
    return Cast(_QueryParam(F("query_string"), param, "[0-9]{1,9}"), IntegerField())


def _staff_q() -> Q:
    q = Q()
    for suffix in STAFF_EMAIL_SUFFIXES:
        q |= Q(user_email__iendswith=suffix)
    return q


def _area_expression():
    whens = [When(source=Source.MCP, then=Value(AGENT_AREA))]
    whens += [When(path__startswith=prefix, then=Value(label)) for prefix, label in AREAS]
    return Case(*whens, default=Value(OTHER_AREA), output_field=CharField())


def _kind_expression():
    return Case(
        When(source=Source.MCP, then=Value(AGENT)),
        When(action=Action.PAGE_VIEW, then=Value(VIEW)),
        When(action__in=(Action.CREATE, Action.UPDATE, Action.DELETE), then=Value(CHANGE)),
        When(action=Action.EXPORT, then=Value(EXPORT)),
        default=Value(READ),
        output_field=CharField(),
    )


def activity_events(since: datetime, until: datetime | None = None, *, hide_staff: bool = False):
    """People's labs activity in a window, annotated with its effective scope."""
    qs = AuditEvent.objects.filter(
        occurred_at__gte=since,
        source__in=HUMAN_SOURCES,
        action__in=ACTIVITY_ACTIONS,
        user__isnull=False,
    )
    if until is not None:
        qs = qs.filter(occurred_at__lt=until)
    if hide_staff:
        qs = qs.exclude(_staff_q())
    return qs.annotate(
        eff_opp=Coalesce(F("opportunity_id"), _query_int("opportunity_id")),
        eff_prog=Coalesce(F("program_id"), _query_int("program_id")),
        eff_org=Coalesce(
            NullIf(KeyTextTransform("organization_slug", "metadata"), Value("")),
            _QueryParam(F("query_string"), "organization_id", "[A-Za-z0-9_.-]{1,120}"),
            Value(NO_ORG),
            output_field=CharField(),
        ),
        kind=_kind_expression(),
        area=_area_expression(),
    )


# ---------------------------------------------------------------------------
# Names: opp -> programme -> organisation
# ---------------------------------------------------------------------------


@dataclass
class Directory:
    """What labs knows about the opportunities, programmes and organisations
    that appear in the activity. Unknown ids keep a ``#id`` label rather than
    being dropped: activity on an opp Pulse has not mirrored is still activity."""

    opps: dict[int, dict] = field(default_factory=dict)
    programs: dict[int, dict] = field(default_factory=dict)
    orgs: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, opp_ids, program_ids, org_slugs) -> Directory:
        from connect_labs.labs.models import LabsOrg
        from connect_labs.labs.synthetic.models import SyntheticOpportunity
        from connect_labs.pulse.models import PulseOpportunity, PulseOrganization, PulseProgram

        d = cls()
        opp_ids = {i for i in opp_ids if i is not None}
        for o in PulseOpportunity.objects.filter(opportunity_id__in=opp_ids).values(
            "opportunity_id", "name", "org_slug", "program_id", "is_test"
        ):
            d.opps[o["opportunity_id"]] = {
                "name": o["name"],
                "org": o["org_slug"] or NO_ORG,
                "program": o["program_id"],
                "synthetic": False,
                "test": o["is_test"],
            }
        for s in SyntheticOpportunity.objects.filter(opportunity_id__in=opp_ids).select_related("labs_org"):
            entry = d.opps.setdefault(s.opportunity_id, {"name": "", "org": NO_ORG, "program": None, "test": False})
            entry["synthetic"] = bool(s.labs_only)
            entry["name"] = entry["name"] or s.label
            if s.labs_only:
                entry["program"] = s.program_id or s.opportunity_id
                entry["org"] = s.labs_org.slug if s.labs_org_id else NO_ORG
                d.programs.setdefault(entry["program"], {"name": s.program_name, "org": entry["org"]})
                if s.labs_org_id:
                    d.orgs.setdefault(s.labs_org.slug, {"name": s.labs_org.name})

        program_ids = {i for i in program_ids if i is not None} | {
            o["program"] for o in d.opps.values() if o["program"] is not None
        }
        for p in PulseProgram.objects.filter(program_id__in=program_ids).values("program_id", "name", "org_slug"):
            d.programs[p["program_id"]] = {"name": p["name"], "org": p["org_slug"] or NO_ORG}

        slugs = (
            {s for s in org_slugs if s}
            | {o["org"] for o in d.opps.values() if o["org"]}
            | {p["org"] for p in d.programs.values() if p["org"]}
        ) - set(d.orgs)
        for o in PulseOrganization.objects.filter(slug__in=slugs).values("slug", "name"):
            d.orgs[o["slug"]] = {"name": o["name"]}
        # The organisation registry names more partners than Connect publishes to
        # Pulse's polling account, so it wins where it knows the slug.
        for lo in LabsOrg.objects.filter(Q(slug__in=slugs) | Q(connect_organization_slug__in=slugs)):
            key = lo.connect_organization_slug if lo.connect_organization_slug in slugs else lo.slug
            d.orgs[key] = {"name": lo.name}
        return d

    def opp_label(self, opp_id: int) -> str:
        return (self.opps.get(opp_id) or {}).get("name") or f"Opportunity #{opp_id}"

    def program_label(self, program_id: int) -> str:
        return (self.programs.get(program_id) or {}).get("name") or f"Programme #{program_id}"

    def org_label(self, slug: str) -> str:
        if not slug:
            return "No organisation in view"
        return (self.orgs.get(slug) or {}).get("name") or slug

    def resolve(self, opp, prog, org) -> tuple[str, int | None, int | None]:
        """(org slug, programme id, opp id) for one event's recorded scope."""
        opp_info = self.opps.get(opp) if opp is not None else None
        if prog is None and opp_info:
            prog = opp_info["program"]
        prog_info = self.programs.get(prog) if prog is not None else None
        org = (opp_info or {}).get("org") or (prog_info or {}).get("org") or org or NO_ORG
        return org, prog, opp

    def opps_in(self, *, program_id=None, org=None) -> set[int]:
        return {
            i
            for i, o in self.opps.items()
            if (program_id is not None and o["program"] == program_id)
            or (org is not None and self.resolve(i, None, None)[0] == org)
        }

    def programs_in(self, org: str) -> set[int]:
        return {i for i, p in self.programs.items() if p["org"] == org}


# ---------------------------------------------------------------------------
# Rollups
# ---------------------------------------------------------------------------


@dataclass
class Tally:
    users: set = field(default_factory=set)
    counts: dict = field(default_factory=lambda: defaultdict(int))
    days: set = field(default_factory=set)
    last: datetime | None = None
    series: dict = field(default_factory=lambda: defaultdict(int))  # bucket -> activity count
    areas: dict = field(default_factory=lambda: defaultdict(int))

    def add(self, row, bucket):
        self.users.add(row["user_id"])
        n = row["calls"] if row["kind"] == AGENT else row["n"]
        self.counts[row["kind"]] += n
        self.days.add(row["day"])
        if self.last is None or row["last"] > self.last:
            self.last = row["last"]
        if row["kind"] != READ:  # data reads ride on page views; counting both doubles a visit
            self.series[bucket] += n
            self.areas[row["area"]] += n

    @property
    def activity(self) -> int:
        return sum(v for k, v in self.counts.items() if k != READ)


def _bucket_for(day: date, weekly: bool) -> date:
    return day - timedelta(days=day.weekday()) if weekly else day


def _buckets(since: date, until: date, weekly: bool) -> list[date]:
    out, cur = [], _bucket_for(since, weekly)
    step = timedelta(days=7 if weekly else 1)
    while cur <= until:
        out.append(cur)
        cur += step
    return out


def _sparkline(series: dict, buckets: list[date], width=120, height=24) -> str:
    values = [series.get(b, 0) for b in buckets]
    peak = max(values) or 1
    if len(values) < 2:
        return ""
    step = width / (len(values) - 1)
    return " ".join(f"{i * step:.1f},{height - (v / peak) * (height - 2) - 1:.1f}" for i, v in enumerate(values))


@dataclass
class Scope:
    """What a report is about: everything, or one org / programme / opp / user."""

    kind: str = "all"  # all | org | program | opp | user
    key: str = ""

    def filter(self, qs, directory: Directory):
        if self.kind == "opp":
            return qs.filter(eff_opp=int(self.key))
        if self.kind == "program":
            pid = int(self.key)
            return qs.filter(Q(eff_prog=pid) | Q(eff_opp__in=directory.opps_in(program_id=pid)))
        if self.kind == "org":
            return qs.filter(
                Q(eff_org=self.key)
                | Q(eff_opp__in=directory.opps_in(org=self.key))
                | Q(eff_prog__in=directory.programs_in(self.key))
            )
        if self.kind == "user":
            return qs.filter(user_id=int(self.key))
        return qs


def _scope_directory(scope: Scope) -> Directory:
    """The directory a scope filter needs before the activity query runs: which
    opps sit under a programme or an organisation."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.pulse.models import PulseOpportunity, PulseProgram

    if scope.kind == "program":
        opp_ids = set(
            PulseOpportunity.objects.filter(program_id=int(scope.key)).values_list("opportunity_id", flat=True)
        )
        opp_ids |= set(
            SyntheticOpportunity.objects.filter(Q(program_id=int(scope.key)) | Q(opportunity_id=int(scope.key)))
            .filter(labs_only=True)
            .values_list("opportunity_id", flat=True)
        )
        return Directory.load(opp_ids, [int(scope.key)], [])
    if scope.kind == "org":
        opp_ids = set(PulseOpportunity.objects.filter(org_slug=scope.key).values_list("opportunity_id", flat=True))
        opp_ids |= set(
            SyntheticOpportunity.objects.filter(labs_only=True, labs_org__slug=scope.key).values_list(
                "opportunity_id", flat=True
            )
        )
        program_ids = PulseProgram.objects.filter(org_slug=scope.key).values_list("program_id", flat=True)
        return Directory.load(opp_ids, program_ids, [scope.key])
    return Directory()


def build_report(scope: Scope, *, days: int = DEFAULT_WINDOW, hide_staff: bool = False, now=None) -> dict:
    now = now or timezone.now()
    since = now - timedelta(days=days)
    weekly = days > 31

    events = scope.filter(activity_events(since, hide_staff=hide_staff), _scope_directory(scope))
    rows = list(
        events.annotate(day=TruncDate("occurred_at"))
        .values("day", "user_id", "eff_opp", "eff_prog", "eff_org", "kind", "area")
        .annotate(n=Count("id"), calls=Count("request_id", distinct=True), last=Max("occurred_at"))
    )

    # The scope's own ids are loaded too, so a page about a quiet opportunity
    # still has its name and its place in the org tree.
    directory = Directory.load(
        {r["eff_opp"] for r in rows} | ({int(scope.key)} if scope.kind == "opp" else set()),
        {r["eff_prog"] for r in rows} | ({int(scope.key)} if scope.kind == "program" else set()),
        {r["eff_org"] for r in rows} | ({scope.key} if scope.kind == "org" else set()),
    )
    buckets = _buckets(since.date(), now.date(), weekly)

    total = Tally()
    by_org: dict[str, Tally] = defaultdict(Tally)
    by_program: dict[int, Tally] = defaultdict(Tally)
    by_opp: dict[int, Tally] = defaultdict(Tally)
    by_user: dict[int, Tally] = defaultdict(Tally)
    user_orgs: dict[int, set] = defaultdict(set)
    series_users: dict[date, set] = defaultdict(set)
    series_orgs: dict[date, set] = defaultdict(set)
    series_kind: dict[str, dict] = defaultdict(lambda: defaultdict(int))

    for row in rows:
        bucket = _bucket_for(row["day"], weekly)
        org, prog, opp = directory.resolve(row["eff_opp"], row["eff_prog"], row["eff_org"])
        total.add(row, bucket)
        by_org[org].add(row, bucket)
        if prog is not None:
            by_program[prog].add(row, bucket)
        if opp is not None:
            by_opp[opp].add(row, bucket)
        by_user[row["user_id"]].add(row, bucket)
        if org:
            user_orgs[row["user_id"]].add(org)
            series_orgs[bucket].add(org)
        series_users[bucket].add(row["user_id"])
        if row["kind"] != READ:
            series_kind[row["kind"]][bucket] += row["calls"] if row["kind"] == AGENT else row["n"]

    users = {u.pk: u for u in get_user_model().objects.filter(pk__in=list(by_user)).only("pk", "username", "email")}

    def table(tallies: dict, label, link_kind: str | None):
        out = []
        for key, t in tallies.items():
            out.append(
                {
                    "key": key,
                    "label": label(key),
                    "link_kind": link_kind if key not in (None, NO_ORG) else None,
                    "users": len(t.users),
                    "views": t.counts[VIEW],
                    "changes": t.counts[CHANGE],
                    "exports": t.counts[EXPORT],
                    "agent": t.counts[AGENT],
                    "activity": t.activity,
                    "active_days": len(t.days),
                    "last": t.last,
                    "top_area": max(t.areas.items(), key=lambda kv: kv[1])[0] if t.areas else "",
                    "spark": _sparkline(t.series, buckets),
                }
            )
        out.sort(key=lambda r: (-r["users"], -r["activity"]))
        return out

    def opp_label(i):
        info = directory.opps.get(i) or {}
        return directory.opp_label(i) + (" (synthetic)" if info.get("synthetic") else "")

    user_rows = table(by_user, lambda pk: getattr(users.get(pk), "username", f"user #{pk}"), "user")
    for r in user_rows:
        u = users.get(r["key"])
        r["email"] = getattr(u, "email", "")
        r["orgs"] = sorted(directory.org_label(o) for o in user_orgs[r["key"]])
        r["staff"] = r["email"].lower().endswith(STAFF_EMAIL_SUFFIXES)
    user_rows.sort(key=lambda r: (-r["active_days"], -r["activity"]))

    area_rows = sorted(
        ({"area": a, "n": n} for a, n in total.areas.items()),
        key=lambda r: -r["n"],
    )
    area_users: dict[str, set] = defaultdict(set)
    for row in rows:
        if row["kind"] != READ:
            area_users[row["area"]].add(row["user_id"])
    for r in area_rows:
        r["users"] = len(area_users[r["area"]])

    return {
        "days": days,
        "weekly": weekly,
        "hide_staff": hide_staff,
        "since": since,
        "directory": directory,
        "totals": {
            "users": len(total.users),
            "views": total.counts[VIEW],
            "changes": total.counts[CHANGE],
            "exports": total.counts[EXPORT],
            "agent": total.counts[AGENT],
            "orgs": len([k for k in by_org if k]),
            "programs": len(by_program),
            "opps": len(by_opp),
        },
        "series": {
            "labels": [b.isoformat() for b in buckets],
            "users": [len(series_users[b]) for b in buckets],
            "orgs": [len(series_orgs[b]) for b in buckets],
            "views": [series_kind[VIEW][b] for b in buckets],
            "changes": [series_kind[CHANGE][b] for b in buckets],
            "exports": [series_kind[EXPORT][b] for b in buckets],
            "agent": [series_kind[AGENT][b] for b in buckets],
        },
        "orgs": table(by_org, directory.org_label, "org"),
        "programs": table(by_program, directory.program_label, "program"),
        "opps": table(by_opp, opp_label, "opp"),
        "users": user_rows,
        "areas": area_rows,
        "events": events,
    }


def top_pages(events, limit: int = 15) -> list[dict]:
    return list(
        events.filter(action=Action.PAGE_VIEW)
        .values("path")
        .annotate(n=Count("id"), users=Count("user_id", distinct=True))
        .order_by("-n")[:limit]
    )


def agent_tools(scope: Scope, since: datetime, *, hide_staff: bool = False, limit: int = 15) -> list[dict]:
    """MCP tool calls by tool. Read from ``MCPAuditLog`` rather than the audit
    trail: a tool that touches no LabsRecord (targeting, docs) writes no audit
    event, but every call writes one MCP log row. Only user scope can filter it;
    a read call's log row records no arguments, so it names no opportunity."""
    from connect_labs.mcp.models import MCPAuditLog

    qs = MCPAuditLog.objects.filter(created_at__gte=since, user__isnull=False)
    if scope.kind == "user":
        qs = qs.filter(user_id=int(scope.key))
    elif scope.kind != "all":
        return []
    if hide_staff:
        q = Q()
        for suffix in STAFF_EMAIL_SUFFIXES:
            q |= Q(user__email__iendswith=suffix)
        qs = qs.exclude(q)
    return list(
        qs.values("tool_name")
        .annotate(n=Count("id"), users=Count("user_id", distinct=True), writes=Count("id", filter=Q(is_write=True)))
        .order_by("-n")[:limit]
    )


def recent_activity(events, limit: int = 60) -> list[dict]:
    return list(
        events.exclude(kind=READ)
        .order_by("-occurred_at")
        .values(
            "occurred_at",
            "user_id",
            "username",
            "action",
            "source",
            "resource_type",
            "path",
            "query_string",
            "request_id",
            "eff_opp",
            "eff_prog",
            "area",
        )[:limit]
    )
