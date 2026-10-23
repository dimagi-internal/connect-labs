"""Which canopy agents labs shows, and which it lets read real data -- chosen in the UI
from what canopy says (spec ``2026-10-22-canopy-site-agents-api-design`` §3).

Two sources, kept apart:

* **canopy's word** -- the site agents API (``canopy_sdk.django.site_agents.current()``,
  read here through ``site_agents()``): the agents canopy allows on labs' connected site,
  signed by canopy, with whether each one's author declared it handles sensitive data, which runner
  flags it can run on, and whether it is ready. Cached five minutes; when canopy cannot
  be reached its last word is used for up to a day, then None -- and labs shows no agent.
* **labs' ticks** -- the ``CanopyAgentSettings`` row in force: per agent, "Show on labs"
  (``offered``) and "Real data" (``real_data``); the ``default_agent`` every labs page
  offers (and every token names); the runner flags every visitor conversation must run
  on; whether canopy may act as the visitor at labs' MCP (``acts_as_visitor``, the
  jwt-bearer grant); the canopy workspace labs' site is registered in (``workspace``,
  canopy's ``tenant``, learnt from its answer); and the site agents ``version`` the save
  was made against.

The newest row is in force, and it is the ONLY source: no environment variable chooses
an agent. With no row (or a database that cannot be read) nothing is set up (``NOTHING``):
no agent is offered and the grant is off, until Dimagi staff save at Labs Admin › Canopy
agents.

Whether one agent may be offered on one page, and if not why, is
``canopy_panel.availability`` -- it reads both.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace

from django.conf import settings

from connect_labs.utils.dimagi_user import is_dimagi_staff

logger = logging.getLogger(__name__)

#: The runner flag real data needs: a sensitive-data runner (a ZDR API key, a locked
#: harness, egress limited to an allowlist, nothing kept on the box).
SENSITIVE_DATA = "sensitive_data"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")

#: Legacy values of a workflow's ``config.agent.agent``, from before every choice was a
#: slug: the site's agent, and the agent the Data pages offered.
SITE_ROLE = "site"
DATA_ROLE = "data"


class InvalidSettings(ValueError):
    pass


# ---------------------------------------------------------------------------
# canopy's word
# ---------------------------------------------------------------------------


def site_agents():
    """canopy's signed list of the agents labs may show (``SiteAgents``), or None when
    canopy has said nothing usable for a day (or labs is not connected): show no agent."""
    try:
        from canopy_sdk.django import site_agents as sdk
    except ImportError:  # an SDK older than 0.14
        return None
    return sdk.current()


def refresh():
    """Ask canopy for its list now (not the five-minute cache or the retry pause): the
    ``SiteAgents`` it returned. Raises ``SiteAgentsError`` (with a ``code``), which
    ``last_error`` then reports."""
    from canopy_sdk.django import site_agents as sdk

    return sdk.refresh()


def last_error():
    """Why canopy's list could not be had the last time labs asked (``SiteAgentsError``:
    ``code`` and a message safe to show), or None."""
    from canopy_sdk.django import site_agents as sdk

    return sdk.last_error()


def unreachable_reason() -> str:
    """Why labs shows no agent when canopy has said nothing usable: canopy's own reason."""
    error = last_error()
    why = f"{error.code}: {error.message}" if error is not None else "no answer yet"
    return f"canopy has not said which agents labs may show ({why})"


def canopy_url(path: str = "") -> str:
    """A page on canopy (``CANOPY_BASE_URL`` + ``path``), or "" when labs has no canopy."""
    base = (getattr(settings, "CANOPY_BASE_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else ""


#: canopy's pages a problem sentence names, by the words it uses for them.
CONNECTED_SITES = "canopy › Connected sites"
RUNNERS = "canopy › Runners"


def eligible_for_real_data(agent) -> tuple[bool, str]:
    """Whether canopy says ``agent`` (a ``SiteAgent`` or None) may read real data: listed,
    its author declared it handles sensitive data, and it is ready on a runner with
    ``sensitive_data``."""
    if agent is None:
        return False, f"canopy does not list it for labs ({CONNECTED_SITES})"
    if not agent.handles_sensitive:
        return (
            False,
            f"{agent.slug} is not declared to handle sensitive data in canopy "
            "(its owner or an admin sets it on the agent's Interface)",
        )
    if not agent.ready_with_flag(SENSITIVE_DATA):
        return False, f"{agent.slug} has no ready sensitive-data runner in canopy ({RUNNERS})"
    return True, ""


# ---------------------------------------------------------------------------
# labs' ticks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentTicks:
    """One row of the Labs Admin table: an agent, and what labs lets it do."""

    slug: str
    offered: bool = True
    real_data: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AgentSettings:
    agents: tuple[AgentTicks, ...]
    default_agent: str
    runner_requirements: tuple[str, ...] = ()
    #: The site agents ``version`` the save was made against ("" before any save).
    site_agents_version: str = ""
    #: Whether canopy may act as the visitor at labs' MCP: the jwt-bearer grant, with
    #: ``CLIENT_ID`` derived from ``CANOPY_BASE_URL`` (``canopy.host_settings``).
    acts_as_visitor: bool = False
    #: The canopy workspace (tenant) labs' site is registered in, from canopy's answer
    #: (``listing.tenant``); "" until learnt, when the default agent names the tenant.
    workspace: str = ""
    #: The row in force, or None when nothing is set up.
    row_id: int | None = field(default=None, compare=False)

    @property
    def is_set_up(self) -> bool:
        return self.row_id is not None

    def ticks(self, slug: str) -> AgentTicks | None:
        return next((a for a in self.agents if a.slug == slug), None)

    @property
    def offered(self) -> tuple[str, ...]:
        """Agents labs shows, the default first."""
        out = [a.slug for a in self.agents if a.offered]
        if self.default_agent in out:
            out.remove(self.default_agent)
            out.insert(0, self.default_agent)
        return tuple(out)

    @property
    def real_data_agents(self) -> tuple[str, ...]:
        return tuple(a.slug for a in self.agents if a.offered and a.real_data)

    def as_dict(self) -> dict:
        return {
            "agents": [a.as_dict() for a in self.agents],
            "default_agent": self.default_agent,
            "runner_requirements": list(self.runner_requirements),
            "site_agents_version": self.site_agents_version,
            "acts_as_visitor": self.acts_as_visitor,
            "workspace": self.workspace,
        }


#: What is in force before anyone has saved: no agent, no runner flag, no grant.
NOTHING = AgentSettings(agents=(), default_agent="")


def _slugs(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(a.strip() for a in values or () if a and a.strip()))


def _from_row(row) -> AgentSettings:
    agents = tuple(
        AgentTicks(str(a.get("slug")), bool(a.get("offered")), bool(a.get("real_data")))
        for a in row.agents or ()
        if isinstance(a, dict) and a.get("slug")
    )
    return AgentSettings(
        agents=agents,
        default_agent=row.default_agent,
        runner_requirements=tuple(row.runner_requirements or ()),
        site_agents_version=row.site_agents_version or "",
        acts_as_visitor=bool(row.acts_as_visitor),
        workspace=row.workspace or "",
        row_id=row.pk,
    )


#: The settings read for the request in hand, where the database cannot be read: the
#: MCP's DPoP gate builds canopy's host config on the event loop (``bound``).
_bound: ContextVar[AgentSettings | None] = ContextVar("canopy_agent_settings", default=None)


@contextmanager
def bound(ticks: AgentSettings):
    """Within this block (and the sync code it calls through ``sync_to_async``, which
    copies the context), ``current()`` is ``ticks``: read once, off the event loop, by
    the async caller (``mcp/server.py:dpop_gate``)."""
    token = _bound.set(ticks)
    try:
        yield ticks
    finally:
        _bound.reset(token)


def current() -> AgentSettings:
    """Labs' ticks in force now. On the event loop, outside ``bound``, the database
    cannot be read and nothing is set up (fail closed)."""
    from connect_labs.labs.models import CanopyAgentSettings

    pinned = _bound.get()
    if pinned is not None:
        return pinned

    try:
        row = CanopyAgentSettings.objects.order_by("-id").first()
    except Exception:  # noqa: BLE001 -- no database (settings import, a test without one)
        return NOTHING
    return _from_row(row) if row else NOTHING


def history(limit: int = 50):
    from connect_labs.labs.models import CanopyAgentSettings

    return list(CanopyAgentSettings.objects.select_related("changed_by").order_by("-id")[:limit])


def validate(
    *,
    agents,
    default_agent,
    runner_requirements,
    acts_as_visitor=None,
    listing=None,
    before: AgentSettings | None = None,
) -> AgentSettings:
    """What a person asked for, checked against canopy's word (``listing``).

    An agent newly shown must be one canopy lists; one already shown that canopy no
    longer lists may stay (it is shown in red, and not offered, until canopy lists it
    again). "Real data" newly ticked needs canopy's declaration that the agent handles
    sensitive data and a ready sensitive-data runner. A runner flag must be one canopy
    names (``sensitive_data`` always may be). ``acts_as_visitor`` None keeps the grant
    as it is."""
    before = before or current()
    listed = {a.slug: a for a in listing.agents} if listing is not None else {}
    ticks: list[AgentTicks] = []
    for a in agents:
        slug = (a.slug or "").strip()
        if not _SLUG.match(slug):
            raise InvalidSettings(f"{slug!r} is not a canopy agent slug.")
        if any(t.slug == slug for t in ticks):
            continue
        was = before.ticks(slug)
        real = bool(a.real_data)
        offered = bool(a.offered) or real  # real data implies shown
        if offered and slug not in listed and not (was and was.offered):
            raise InvalidSettings(f"canopy does not list {slug} for labs: allow it on canopy's Connected sites first.")
        if real and not (was and was.real_data):
            ok, why = eligible_for_real_data(listed.get(slug))
            if not ok:
                raise InvalidSettings(f"{slug} cannot be allowed over real data: {why}.")
        ticks.append(AgentTicks(slug, offered, real))
    default_agent = (default_agent or "").strip()
    if not any(t.slug == default_agent and t.offered for t in ticks):
        raise InvalidSettings("The default agent must be one labs shows.")
    known = {SENSITIVE_DATA, *(f.flag for f in listing.runner_flags)} if listing is not None else {SENSITIVE_DATA}
    flags = _slugs(runner_requirements)
    unknown = [f for f in flags if f not in known and f not in before.runner_requirements]
    if unknown:
        raise InvalidSettings(
            f"Unknown runner flag(s): {', '.join(unknown)}. canopy names: {', '.join(sorted(known))}."
        )
    return AgentSettings(
        agents=tuple(ticks),
        default_agent=default_agent,
        runner_requirements=flags,
        site_agents_version=listing.version if listing is not None else before.site_agents_version,
        acts_as_visitor=before.acts_as_visitor if acts_as_visitor is None else bool(acts_as_visitor),
        workspace=_workspace(listing, before),
    )


def _workspace(listing, *fallbacks: AgentSettings) -> str:
    """The workspace canopy's answer names (its ``tenant``), else the first one known."""
    tenant = (getattr(listing, "tenant", "") or "").strip() if listing is not None else ""
    return tenant or next((f.workspace for f in fallbacks if f.workspace), "")


def save(
    user, *, note: str = "", agents, default_agent, runner_requirements, acts_as_visitor=None, listing=None
) -> AgentSettings:
    """Put new ticks in force (a new row), attributed to ``user`` and audited."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record
    from connect_labs.labs.models import CanopyAgentSettings

    if not can_change(user):
        raise PermissionError("Only Dimagi staff can change which agents labs allows.")
    before = current()
    new = validate(
        agents=agents,
        default_agent=default_agent,
        runner_requirements=runner_requirements,
        acts_as_visitor=acts_as_visitor,
        listing=listing,
        before=before,
    )
    row = CanopyAgentSettings.objects.create(**new.as_dict(), note=(note or "")[:500], changed_by=user)
    record(
        Action.UPDATE,
        resource_type="canopy_agent_settings",
        resource_id=row.pk,
        metadata={"before": before.as_dict(), "after": new.as_dict(), "note": row.note},
        user=user,
    )
    return _from_row(row)


def restore(user, row_id: int, listing=None) -> AgentSettings:
    """Undo to an earlier row: its ticks, as a new row -- checked against canopy's word
    (``listing``) exactly as a save is, and refused when canopy no longer lists its
    default agent. The workspace is canopy's current answer, else the one known."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record
    from connect_labs.labs.models import CanopyAgentSettings

    if not can_change(user):
        raise PermissionError("Only Dimagi staff can change which agents labs allows.")
    old = _from_row(CanopyAgentSettings.objects.get(pk=row_id))
    if listing is not None and listing.agent(old.default_agent) is None:
        raise InvalidSettings(
            f"#{row_id} cannot be restored: its default agent, {old.default_agent}, is no longer listed for "
            f"labs in canopy ({CONNECTED_SITES}). Allow it there first, or choose another default."
        )
    before = current()
    try:
        new = validate(
            agents=old.agents,
            default_agent=old.default_agent,
            runner_requirements=old.runner_requirements,
            acts_as_visitor=old.acts_as_visitor,
            listing=listing,
            before=before,
        )
    except InvalidSettings as e:
        raise InvalidSettings(f"#{row_id} cannot be restored: {e}") from e
    new = replace(new, workspace=_workspace(listing, old, before))
    row = CanopyAgentSettings.objects.create(**new.as_dict(), note=f"restored #{row_id}", changed_by=user)
    record(
        Action.UPDATE,
        resource_type="canopy_agent_settings",
        resource_id=row.pk,
        metadata={"before": before.as_dict(), "after": new.as_dict(), "note": row.note},
        user=user,
    )
    return _from_row(row)


def learn_workspace(listing) -> str:
    """Fill the workspace in on the row in force from canopy's answer, when it is blank.

    Not a choice anyone makes -- the workspace labs' site is registered in is canopy's
    fact -- so it is filled in place (audited) rather than as a new change. Until it is,
    the default agent names the tenant, as before. Returns the workspace in force."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record
    from connect_labs.labs.models import CanopyAgentSettings

    ticks = current()
    # Only from a fresh answer: a stale one is a day-old word, not a fact to record.
    tenant = "" if getattr(listing, "stale", False) else _workspace(listing)
    if ticks.row_id is None or ticks.workspace or not tenant:
        return ticks.workspace
    if CanopyAgentSettings.objects.filter(pk=ticks.row_id, workspace="").update(workspace=tenant):
        record(
            Action.UPDATE,
            resource_type="canopy_agent_settings",
            resource_id=ticks.row_id,
            metadata={"workspace": tenant, "note": "learnt from canopy's answer"},
            user=None,
        )
    return tenant


def forget_workspace(user) -> AgentSettings:
    """A new row like the one in force, with no workspace: the default agent names the
    tenant again. For a site canopy has moved to another workspace (``workspace_mismatch``):
    the next answer then names the new one."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record
    from connect_labs.labs.models import CanopyAgentSettings

    if not can_change(user):
        raise PermissionError("Only Dimagi staff can change which agents labs allows.")
    before = current()
    if before.row_id is None:
        raise InvalidSettings("Nothing is set up yet.")
    new = replace(before, workspace="", row_id=None)
    row = CanopyAgentSettings.objects.create(**new.as_dict(), note="workspace forgotten", changed_by=user)
    record(
        Action.UPDATE,
        resource_type="canopy_agent_settings",
        resource_id=row.pk,
        metadata={"before": before.as_dict(), "after": new.as_dict(), "note": row.note},
        user=user,
    )
    return _from_row(row)


def can_change(user) -> bool:
    """Dimagi staff (``is_dimagi_staff``: a Dimagi sign-in, or Django's ``is_staff``).

    Not ``is_staff`` alone: an OAuth-created labs user never gets that flag and no labs
    page grants it, so the setting could be changed by nobody. These ticks are one of
    three locks (the others are canopy's: the agent's sensitive-data declaration, set by
    its owner or an admin, and the passkey-gated runner flag), and every change is
    attributed, audited and restorable."""
    return bool(user and user.is_authenticated and is_dimagi_staff(user))


# ---------------------------------------------------------------------------
# A workflow's choice
# ---------------------------------------------------------------------------


def data_agent(ticks: AgentSettings | None = None, listing=None) -> str:
    """The agent for data work: the first one labs lets read real data, else the first
    shown agent canopy declares handles sensitive data. "" when there is none."""
    ticks = ticks or current()
    if ticks.real_data_agents:
        return ticks.real_data_agents[0]
    if listing is not None:
        for slug in ticks.offered:
            agent = listing.agent(slug)
            if agent is not None and agent.handles_sensitive:
                return slug
    return ""


def resolve_choice(value: str, ticks: AgentSettings | None = None, listing=None) -> str:
    """The slug a workflow's ``config.agent.agent`` names: legacy ``"site"`` (or nothing)
    is the default agent, legacy ``"data"`` the data agent (``data_agent``)."""
    ticks = ticks or current()
    if not value or value == SITE_ROLE:
        return ticks.default_agent
    if value == DATA_ROLE:
        return data_agent(ticks, listing)
    return value
