"""Which canopy agents labs shows, and which it lets read real data -- chosen in the UI
from what canopy says (spec ``2026-10-22-canopy-site-agents-api-design`` §3).

Two sources, kept apart:

* **canopy's word** -- the site agents API (``canopy_sdk.django.site_agents.current()``,
  read here through ``site_agents()``): the agents canopy allows on labs' connected site,
  signed by canopy, with whether each one's author declared it PHI-safe, which runner
  flags it can run on, and whether it is ready. Cached five minutes; when canopy cannot
  be reached its last word is used for up to a day, then None -- and labs shows no agent.
* **labs' ticks** -- the ``CanopyAgentSettings`` row in force: per agent, "Show on labs"
  (``offered``) and "Real data" (``real_data``); the ``default_agent`` every labs page
  offers (and every token names); the runner flags every visitor conversation must run
  on; and the site agents ``version`` the save was made against.

The newest row is in force. With none, the ``CANOPY_*`` environment settings are (the
cold-start defaults), so a deploy changes nothing until someone saves. If the database
cannot be read, the environment settings are used too.

Whether one agent may be offered on one page, and if not why, is
``canopy_panel.availability`` -- it reads both.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from django.conf import settings

from connect_labs.utils.dimagi_user import is_dimagi_staff

logger = logging.getLogger(__name__)

#: The runner flag real data needs (zero data retention).
ZDR = "zdr"

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


def unreachable_reason() -> str:
    return (
        "canopy has not said which agents labs may show (it has been unreachable for a day, or labs is not connected)"
    )


def eligible_for_real_data(agent) -> tuple[bool, str]:
    """Whether canopy says ``agent`` (a ``SiteAgent`` or None) may read real data: listed,
    its author declared it PHI-safe, and it is ready on a runner with ``zdr``."""
    if agent is None:
        return False, "canopy does not list it for labs (canopy › Connected sites)"
    if not agent.phi_safe:
        return False, f"{agent.slug} is not declared PHI-safe in canopy (its Interface, with a passkey)"
    if not agent.ready_with_flag(ZDR):
        return False, f"{agent.slug} has no ready ZDR runner in canopy (canopy › Runners)"
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
    #: The site agents ``version`` the save was made against ("" for the defaults).
    site_agents_version: str = ""
    #: The row in force, or None when these are the environment settings.
    row_id: int | None = field(default=None, compare=False)

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
        }


def _slugs(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(a.strip() for a in values or () if a and a.strip()))


def from_environment() -> AgentSettings:
    """The cold-start defaults: the ``CANOPY_*`` environment settings, read as ticks."""
    default = getattr(settings, "CANOPY_AGENT_SLUG", "") or ""
    data = getattr(settings, "CANOPY_DATA_AGENT_SLUG", "") or ""
    phi = _slugs(getattr(settings, "CANOPY_PHI_AGENTS", ()) or ())
    others = _slugs(getattr(settings, "CANOPY_OFFERED_AGENTS", ()) or ())
    slugs = _slugs((default, data, *others, *phi))
    return AgentSettings(
        agents=tuple(AgentTicks(s, offered=True, real_data=s in phi) for s in slugs),
        default_agent=default,
        runner_requirements=_slugs(getattr(settings, "CANOPY_RUNNER_REQUIREMENTS", ()) or ()),
    )


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
        row_id=row.pk,
    )


def current() -> AgentSettings:
    """Labs' ticks in force now."""
    from connect_labs.labs.models import CanopyAgentSettings

    try:
        row = CanopyAgentSettings.objects.order_by("-id").first()
    except Exception:  # noqa: BLE001 -- no database (settings import, a test without one)
        return from_environment()
    return _from_row(row) if row else from_environment()


def history(limit: int = 50):
    from connect_labs.labs.models import CanopyAgentSettings

    return list(CanopyAgentSettings.objects.select_related("changed_by").order_by("-id")[:limit])


def validate(
    *, agents, default_agent, runner_requirements, listing=None, before: AgentSettings | None = None
) -> AgentSettings:
    """What a person asked for, checked against canopy's word (``listing``).

    An agent newly shown must be one canopy lists; one already shown that canopy no
    longer lists may stay (it is shown in red, and not offered, until canopy lists it
    again). "Real data" newly ticked needs canopy's PHI-safe declaration and a ready ZDR
    runner. A runner flag must be one canopy names (``zdr`` always may be)."""
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
    known = {ZDR, *(f.flag for f in listing.runner_flags)} if listing is not None else {ZDR}
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
    )


def save(user, *, note: str = "", agents, default_agent, runner_requirements, listing=None) -> AgentSettings:
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


def restore(user, row_id: int) -> AgentSettings:
    """Undo to an earlier row: its ticks, as a new row. Not re-checked against canopy:
    whatever canopy says now still decides what is offered (``availability``)."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record
    from connect_labs.labs.models import CanopyAgentSettings

    if not can_change(user):
        raise PermissionError("Only Dimagi staff can change which agents labs allows.")
    old = _from_row(CanopyAgentSettings.objects.get(pk=row_id))
    before = current()
    row = CanopyAgentSettings.objects.create(**old.as_dict(), note=f"restored #{row_id}", changed_by=user)
    record(
        Action.UPDATE,
        resource_type="canopy_agent_settings",
        resource_id=row.pk,
        metadata={"before": before.as_dict(), "after": old.as_dict(), "note": row.note},
        user=user,
    )
    return _from_row(row)


def can_change(user) -> bool:
    """Dimagi staff (``is_dimagi_staff``: a Dimagi sign-in, or Django's ``is_staff``).

    Not ``is_staff`` alone: an OAuth-created labs user never gets that flag and no labs
    page grants it, so the setting could be changed by nobody. These ticks are one of
    three locks (canopy's passkey-gated PHI declaration and ZDR flag are the others),
    and every change is attributed, audited and restorable."""
    return bool(user and user.is_authenticated and is_dimagi_staff(user))


# ---------------------------------------------------------------------------
# A workflow's choice
# ---------------------------------------------------------------------------


def data_agent(ticks: AgentSettings | None = None, listing=None) -> str:
    """The agent for data work: the first one labs lets read real data, else the first
    shown agent canopy declares PHI-safe. "" when there is none."""
    ticks = ticks or current()
    if ticks.real_data_agents:
        return ticks.real_data_agents[0]
    if listing is not None:
        for slug in ticks.offered:
            agent = listing.agent(slug)
            if agent is not None and agent.phi_safe:
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
