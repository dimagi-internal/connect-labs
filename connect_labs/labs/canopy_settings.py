"""Which canopy agents labs offers, and which it allows over real data -- changed in the UI.

Four values, read wherever labs used to read the ``CANOPY_*`` environment settings of
the same names:

* ``site_agent`` (``CANOPY_AGENT_SLUG``) -- the agent every labs page offers, and the
  canopy tenant every token names.
* ``data_agent`` (``CANOPY_DATA_AGENT_SLUG``) -- the agent the Data pages offer in its
  place (``labsdata``), always on ZDR runners. Empty: they offer the site agent.
* ``phi_agents`` (``CANOPY_PHI_AGENTS``) -- agents labs allows over real data. One of the
  conditions in ``canopy_panel.py``; canopy's per-call statement is still required.
* ``runner_requirements`` (``CANOPY_RUNNER_REQUIREMENTS``) -- runner flags every visitor
  conversation must run on.
* ``offered_agents`` (``CANOPY_OFFERED_AGENTS``) -- further agents a workflow may choose
  for its runs, beside those two. Which one a workflow offers is its own choice
  (``config.agent.agent``, workflow/agent_sharing.py); ``choices`` lists them and
  ``resolve`` reads one. An agent allowed over real data is always offered read-only, on
  ZDR runners -- the same terms as the data agent.

The newest ``CanopyAgentSettings`` row is in force. With none, the environment settings
are, so a deploy changes nothing until someone saves. If the database cannot be read,
the environment settings are used too: they are the documented defaults.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass

from django.conf import settings

from connect_labs.utils.dimagi_user import is_dimagi_staff

logger = logging.getLogger(__name__)

#: Runner flags a conversation can be required to run on (canopy's known flags).
KNOWN_RUNNER_FLAGS = ("zdr",)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")


class InvalidSettings(ValueError):
    pass


@dataclass(frozen=True)
class AgentSettings:
    site_agent: str
    data_agent: str
    phi_agents: tuple[str, ...]
    runner_requirements: tuple[str, ...]
    offered_agents: tuple[str, ...] = ()
    #: The row in force, or None when these are the environment settings.
    row_id: int | None = None

    def as_dict(self) -> dict:
        out = asdict(self)
        out["phi_agents"] = list(self.phi_agents)
        out["runner_requirements"] = list(self.runner_requirements)
        out["offered_agents"] = list(self.offered_agents)
        out.pop("row_id")
        return out


def from_environment() -> AgentSettings:
    return AgentSettings(
        site_agent=getattr(settings, "CANOPY_AGENT_SLUG", "") or "",
        data_agent=getattr(settings, "CANOPY_DATA_AGENT_SLUG", "") or "",
        phi_agents=tuple(getattr(settings, "CANOPY_PHI_AGENTS", ()) or ()),
        runner_requirements=tuple(getattr(settings, "CANOPY_RUNNER_REQUIREMENTS", ()) or ()),
        offered_agents=tuple(getattr(settings, "CANOPY_OFFERED_AGENTS", ()) or ()),
    )


def _from_row(row) -> AgentSettings:
    return AgentSettings(
        site_agent=row.site_agent,
        data_agent=row.data_agent,
        phi_agents=tuple(row.phi_agents or ()),
        runner_requirements=tuple(row.runner_requirements or ()),
        offered_agents=tuple(row.offered_agents or ()),
        row_id=row.pk,
    )


def current() -> AgentSettings:
    """The settings in force now."""
    from connect_labs.labs.models import CanopyAgentSettings

    try:
        row = CanopyAgentSettings.objects.order_by("-id").first()
    except Exception:  # noqa: BLE001 -- no database (settings import, a test without one)
        return from_environment()
    return _from_row(row) if row else from_environment()


def history(limit: int = 50):
    from connect_labs.labs.models import CanopyAgentSettings

    return list(CanopyAgentSettings.objects.select_related("changed_by").order_by("-id")[:limit])


def _slugs(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(a.strip() for a in values or () if a and a.strip()))


def validate(*, site_agent, data_agent, phi_agents, runner_requirements, offered_agents=()) -> AgentSettings:
    site_agent = (site_agent or "").strip()
    data_agent = (data_agent or "").strip()
    phi = _slugs(phi_agents)
    offered = _slugs(offered_agents)
    flags = tuple(dict.fromkeys(f.strip() for f in runner_requirements or () if f and f.strip()))
    if not _SLUG.match(site_agent):
        raise InvalidSettings("The site agent must be a canopy agent slug (lowercase letters, digits, hyphens).")
    for slug in (data_agent, *phi, *offered) if data_agent else (*phi, *offered):
        if not _SLUG.match(slug):
            raise InvalidSettings(f"{slug!r} is not a canopy agent slug (lowercase letters, digits, hyphens).")
    unknown = [f for f in flags if f not in KNOWN_RUNNER_FLAGS]
    if unknown:
        raise InvalidSettings(f"Unknown runner flag(s): {', '.join(unknown)}. Known: {', '.join(KNOWN_RUNNER_FLAGS)}.")
    return AgentSettings(site_agent, data_agent, phi, flags, offered)


# ---------------------------------------------------------------------------
# Which agent a workflow offers
# ---------------------------------------------------------------------------

#: A workflow's ``config.agent.agent`` may name a role, which follows whatever agent
#: these settings name for it, or an agent's slug.
SITE_ROLE = "site"
DATA_ROLE = "data"


@dataclass(frozen=True)
class Offered:
    """An agent a workflow may offer, and on what terms."""

    value: str  # what config.agent.agent holds: a role or a slug
    slug: str
    #: Read-only, on ZDR runners, never ``workflow:act``: the data agent, and every
    #: agent labs allows over real data.
    read_only: bool
    real_data: bool

    def as_dict(self) -> dict:
        return asdict(self)


def choices(agents: AgentSettings | None = None) -> list[Offered]:
    """Every agent a workflow may choose, the site's first."""
    agents = agents or current()

    def offered(value, slug):
        real = slug in agents.phi_agents
        return Offered(value, slug, read_only=real or slug == agents.data_agent, real_data=real)

    out = [offered(SITE_ROLE, agents.site_agent)]
    if agents.data_agent:
        out.append(offered(DATA_ROLE, agents.data_agent))
    named = {agents.site_agent, agents.data_agent}
    for slug in agents.offered_agents:
        if slug not in named:
            named.add(slug)
            out.append(offered(slug, slug))
    return out


def resolve(value: str, agents: AgentSettings | None = None) -> Offered | None:
    """The agent a workflow's choice names now, or None when labs does not offer it
    (a slug since removed here, or the data role with no data agent)."""
    by_value = {o.value: o for o in choices(agents)}
    if value in by_value:
        return by_value[value]
    # A slug that is also one of the roles' agents: the same agent, on the role's terms.
    return next((o for o in by_value.values() if o.slug == value), None)


def save(user, *, note: str = "", **values) -> AgentSettings:
    """Put new settings in force (a new row), attributed to ``user`` and audited."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record
    from connect_labs.labs.models import CanopyAgentSettings

    if not can_change(user):
        raise PermissionError("Only Dimagi staff can change which agents labs allows.")
    new = validate(**values)
    before = current()
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
    """Undo to an earlier row: its values, as a new row."""
    from connect_labs.labs.models import CanopyAgentSettings

    old = CanopyAgentSettings.objects.get(pk=row_id)
    return save(user, note=f"restored #{old.pk}", **_from_row(old).as_dict())


def can_change(user) -> bool:
    """Dimagi staff (``is_dimagi_staff``: a Dimagi sign-in, or Django's ``is_staff``).

    Not ``is_staff`` alone: an OAuth-created labs user never gets that flag and no labs
    page grants it, so the setting could be changed by nobody. This allow-list is one of
    three locks (canopy's passkey-gated PHI declaration and ZDR flag are the others),
    and every change is attributed, audited and restorable."""
    return bool(user and user.is_authenticated and is_dimagi_staff(user))
