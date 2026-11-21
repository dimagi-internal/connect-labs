"""The interface labs reads Connect Workers through, and the shapes it returns.

`ConnectWorkerSource`'s methods mirror the endpoints labs has asked Connect for
(`docs/connect-asks.md`). Today one implementation exists, `ExportFacade`, which
reaches each answer through Connect's per-opportunity export API and works around
what that API lacks. When Connect ships an endpoint, a `NativeConnectSource` serves
that method and declares it in `capabilities`; `RoutedSource` then sends that method
to it and every other method to the facade. Nothing outside this package changes.

Every answer carries a `Coverage`: whether it is complete, whether it is exact or
derived, how it was obtained, and what it cannot include. A profile built on these
answers states its coverage instead of implying completeness.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime

# The entity kinds `entities()` serves. Each is one Connect export per opportunity today.
USER_DATA = "user_data"
COMPLETED_MODULE = "completed_module"
ASSESSMENT = "assessment"
COMPLETED_WORKS = "completed_works"
PAYMENT = "payment"
ASSIGNED_TASKS = "assigned_tasks"
TASK_TYPES = "task_types"
AUDIT_REPORT_ENTRIES = "audit_report_entries"
WORK_AREAS = "work_areas"
VISITS_THIN = "visits_thin"

ENTITY_KINDS = (
    USER_DATA,
    COMPLETED_MODULE,
    ASSESSMENT,
    COMPLETED_WORKS,
    PAYMENT,
    ASSIGNED_TASKS,
    TASK_TYPES,
    AUDIT_REPORT_ENTRIES,
    WORK_AREAS,
    VISITS_THIN,
)

# Method keys, as `capabilities` names them. An entity kind is its own key
# (`entities:<kind>`), because Connect will ship them one at a time.
ACCESSIBLE_OPPORTUNITIES = "accessible_opportunities"
OPPORTUNITY_WORKERS = "opportunity_workers"
WORKER_OPPORTUNITIES = "worker_opportunities"
EVENTS = "events"
NOTIFICATIONS = "notifications"
LEARN_MODULES = "learn_modules"


def entities_key(kind: str) -> str:
    return f"entities:{kind}"


METHOD_KEYS = (
    ACCESSIBLE_OPPORTUNITIES,
    OPPORTUNITY_WORKERS,
    WORKER_OPPORTUNITIES,
    EVENTS,
    NOTIFICATIONS,
    LEARN_MODULES,
    *(entities_key(k) for k in ENTITY_KINDS),
)


@dataclass(frozen=True)
class Capabilities:
    """What a source serves, and how well.

    ``native``: method keys the source answers from an endpoint built for the purpose.
    A routed source sends a method to the first source that declares it native.
    ``username_filter`` / ``since`` / ``stable_ids``: entity kinds the source can
    filter by worker, filter by change time, or key by Connect's own id, on the server.
    The facade declares none of these; it emulates every one (and says so in Coverage).
    """

    native: frozenset[str] = frozenset()
    username_filter: frozenset[str] = frozenset()
    since: frozenset[str] = frozenset()
    stable_ids: frozenset[str] = frozenset()

    def serves_natively(self, method_key: str) -> bool:
        return method_key in self.native


@dataclass(frozen=True)
class Coverage:
    """How far an answer can be trusted.

    ``complete``: every row that exists for this question is present.
    ``exact``: rows came from Connect as recorded, not derived by labs (diffs, caches).
    ``method``: one line on how the answer was obtained, for the profile's coverage panel.
    ``notes``: what this answer cannot contain and why (each a sentence for a person).
    """

    complete: bool
    exact: bool
    method: str
    source: str = "connect-export"
    notes: tuple[str, ...] = ()
    as_of: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "complete": self.complete,
            "exact": self.exact,
            "method": self.method,
            "source": self.source,
            "notes": list(self.notes),
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }


@dataclass
class Result:
    """Rows plus the coverage they come with. Rows are plain dicts, each with a stable ``key``."""

    rows: list[dict]
    coverage: Coverage

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)


@dataclass(frozen=True)
class OpportunityRef:
    """An opportunity the sync identity can read.

    ``via`` is how labs learned it may read it: ``listed`` (Connect's
    opp_org_program_list) or ``probed`` (opportunity_access answered yes for an
    opportunity the list leaves out -- a supervising-org or funder opportunity).
    """

    id: int
    name: str = ""
    program_id: int | None = None
    organization_slug: str = ""
    is_active: bool = False
    end_date: str | None = None
    is_test: bool = False
    visit_count: int | None = None
    via: str = "listed"


@dataclass(frozen=True)
class AccessibleOpportunities:
    opportunities: list[OpportunityRef]
    coverage: Coverage
    programs: dict[int, dict] = field(default_factory=dict)
    organizations: dict[str, dict] = field(default_factory=dict)


class ConnectWorkerSource(abc.ABC):
    """Everything labs reads about Connect Workers from Connect.

    Each method is the endpoint labs asked Connect for (docs/connect-asks.md). All
    rows are plain dicts with a stable ``key``; usernames are PersonalID usernames.
    """

    name = "abstract"
    capabilities = Capabilities()

    def reset(self) -> None:
        """Forget anything memoised, so the next call reads Connect again."""

    def close(self) -> None:
        """Release connections."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @abc.abstractmethod
    def accessible_opportunities(self, *, probe_ids=None) -> AccessibleOpportunities:
        """Every opportunity this source's identity may read.

        ``probe_ids``: further opportunity ids to ask about that a listing may omit
        (supervising-org and funder opportunities)."""

    @abc.abstractmethod
    def opportunity_workers(self, opp_id: int) -> Result:
        """One ``user_data`` row per Connect Worker on the opportunity."""

    @abc.abstractmethod
    def worker_opportunities(self, username: str, *, opportunity_ids=None) -> Result:
        """``user_data`` rows for one worker across opportunities (each row has ``opportunity_id``)."""

    @abc.abstractmethod
    def entities(
        self, opp_id: int, kind: str, *, username: str | None = None, since: datetime | None = None
    ) -> Result:
        """Rows of one kind on one opportunity, optionally for one worker / changed since."""

    @abc.abstractmethod
    def events(self, opp_id: int, *, since: datetime | None, username: str | None = None) -> Result:
        """Connect's own history of changes (suspensions, status and review changes, payments)."""

    @abc.abstractmethod
    def notifications(self, username: str, *, since: datetime | None) -> Result:
        """Messages Connect sent the worker (invite SMS, pushes, payment notices...)."""

    @abc.abstractmethod
    def learn_modules(self, opp_id: int) -> Result:
        """The opportunity's learn modules, with names."""


class RoutedSource(ConnectWorkerSource):
    """Sends each method to the first source that serves it natively, else to the fallback.

    This is how a ``NativeConnectSource`` switches on per method as Connect ships
    endpoints: list it first; the ``ExportFacade`` stays as the fallback for the rest.
    """

    name = "routed"

    def __init__(self, sources: list[ConnectWorkerSource], fallback: ConnectWorkerSource):
        self.sources = list(sources)
        self.fallback = fallback
        native = frozenset().union(*(s.capabilities.native for s in self.sources)) if self.sources else frozenset()
        self.capabilities = Capabilities(native=native)

    def reset(self) -> None:
        for source in (*self.sources, self.fallback):
            source.reset()

    def close(self) -> None:
        for source in (*self.sources, self.fallback):
            source.close()

    def source_for(self, method_key: str) -> ConnectWorkerSource:
        for source in self.sources:
            if source.capabilities.serves_natively(method_key):
                return source
        return self.fallback

    def accessible_opportunities(self, *, probe_ids=None):
        return self.source_for(ACCESSIBLE_OPPORTUNITIES).accessible_opportunities(probe_ids=probe_ids)

    def opportunity_workers(self, opp_id):
        return self.source_for(OPPORTUNITY_WORKERS).opportunity_workers(opp_id)

    def worker_opportunities(self, username, *, opportunity_ids=None):
        return self.source_for(WORKER_OPPORTUNITIES).worker_opportunities(username, opportunity_ids=opportunity_ids)

    def entities(self, opp_id, kind, *, username=None, since=None):
        return self.source_for(entities_key(kind)).entities(opp_id, kind, username=username, since=since)

    def events(self, opp_id, *, since, username=None):
        return self.source_for(EVENTS).events(opp_id, since=since, username=username)

    def notifications(self, username, *, since):
        return self.source_for(NOTIFICATIONS).notifications(username, since=since)

    def learn_modules(self, opp_id):
        return self.source_for(LEARN_MODULES).learn_modules(opp_id)
