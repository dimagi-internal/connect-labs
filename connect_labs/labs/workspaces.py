"""Who someone is in a workspace: a Program Manager, a Network Manager, or nobody.

A workspace (`labs.models.Workspace`) is a labs-only organisation whose
programmes are run with roles. Nothing here applies to any other organisation:
`workspace_for_program` returns None for them, and every caller falls back to
the rules it had before.

  * **Program Manager** -- Dimagi staff (`is_dimagi_staff`), or an admin of the
    workspace's organisation (`marketplace.OrgMembership`, granted for it). Sees
    and may do everything.
  * **Network Manager** -- someone in a Connect organisation that DELIVERS an
    opportunity in one of the workspace's linked Connect programmes. Their team
    is that organisation's slug; in more than one such organisation, they are on
    each of those teams.
  * anyone else -- no role, and so no access to the workspace's programmes.

**Where the Network Manager answer comes from.** The Connect organisation tree
labs already holds for the person: the session's copy on the web
(`remember_org_data`, called by `labs.context.get_org_data`), else the
TTL-cached Connect fetch the MCP route already uses. Both can be up to an hour
old (`oauth._ORG_DATA_CACHE_TTL`), so someone added to a delivering organisation
in Connect may need to refresh their organisation data (or sign in again)
before labs sees it. When the tree cannot be had at all, the answer is "no
role": a Network Manager is never assumed.

An opportunity is "delivered by" the organisation in its `organization` field.
Only organisations the person is a MEMBER of count -- a Connect programme
manager sees every opportunity in its programme, delivered by other
organisations, and must not become every team at once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PROGRAM_MANAGER = "program_manager"
NETWORK_MANAGER = "network_manager"

# Per-request memo on the user object (the web's request.user and the MCP's user
# are both fetched fresh per call, so this never outlives a request).
_ORG_DATA_ATTR = "_labs_workspace_org_data"
_ROLES_ATTR = "_labs_workspace_roles"
_PROGRAMS_ATTR = "_labs_workspace_programs"


@dataclass(frozen=True)
class WorkspaceRole:
    role: str
    # Network Manager: the Connect org slugs they deliver for, in the order their
    # organisation tree lists them (the first is the default team for new work).
    teams: tuple[str, ...] = ()
    # slug -> display name, where the tree names the organisation.
    team_names: dict = field(default_factory=dict, compare=False)

    @property
    def is_program_manager(self) -> bool:
        return self.role == PROGRAM_MANAGER

    @property
    def label(self) -> str:
        return "Program Manager" if self.is_program_manager else "Network Manager"


def workspace_for_program(program_id):
    """The workspace a labs-only programme belongs to, or None.

    None, without a query, for a real programme id; None for a labs-only
    programme whose organisation is not a workspace."""
    from connect_labs.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR

    try:
        pid = int(program_id)
    except (TypeError, ValueError):
        return None
    if pid < LABS_ONLY_OPP_ID_FLOOR:
        return None
    from django.db.models import Q

    from connect_labs.labs.models import Workspace

    return (
        Workspace.objects.select_related("org")
        .filter(
            Q(org__synthetic_opportunities__program_id=pid)
            | Q(
                org__synthetic_opportunities__program_id__isnull=True, org__synthetic_opportunities__opportunity_id=pid
            ),
            org__synthetic_opportunities__labs_only=True,
        )
        .first()
    )


def remember_org_data(user, org_data) -> None:
    """Use this Connect organisation tree for `user` for the rest of the request.

    Called with the session's copy, so the web never has to ask Connect. An
    empty tree is not remembered: a session whose organisation list failed to
    load at sign-in says nothing about the person, and the cached fetch may."""
    if user is None or not org_data:
        return
    try:
        setattr(user, _ORG_DATA_ATTR, org_data)
    except AttributeError:  # pragma: no cover - an object that takes no attributes
        pass


def connect_org_data(user) -> dict | None:
    """`user`'s Connect organisation tree, or None if it cannot be had."""
    remembered = getattr(user, _ORG_DATA_ATTR, None)
    if remembered:
        return remembered
    from connect_labs.labs.connect_tokens import get_valid_access_token
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

    try:
        token = get_valid_access_token(user)
    except Exception:  # noqa: BLE001 -- no token is "cannot tell", which is "no role"
        return None
    data = fetch_user_organization_data(token, owner=getattr(user, "username", None)) if token else None
    remember_org_data(user, data)
    return data


def delivering_teams(org_data, connect_program_ids) -> tuple[tuple[str, ...], dict]:
    """The Connect org slugs, among the person's own organisations, that deliver
    an opportunity in any of `connect_program_ids`, plus their display names."""
    linked = {int(p) for p in connect_program_ids or []}
    if not org_data or not linked:
        return (), {}
    names = {}
    for org in org_data.get("organizations") or []:
        slug = org.get("slug")
        if slug and not org.get("labs_only"):
            names[str(slug)] = org.get("name") or str(slug)
    teams: list[str] = []
    for opp in org_data.get("opportunities") or []:
        if opp.get("labs_only"):
            continue
        try:
            program = int(opp.get("program"))
        except (TypeError, ValueError):
            continue
        slug = str(opp.get("organization") or "")
        if program in linked and slug in names and slug not in teams:
            teams.append(slug)
    return tuple(teams), {t: names[t] for t in teams}


def role_in(user, workspace) -> WorkspaceRole | None:
    """`user`'s role in `workspace`, or None. Memoised for the request."""
    if workspace is None or user is None or not getattr(user, "is_authenticated", False):
        return None
    memo = getattr(user, _ROLES_ATTR, None)
    if memo is None:
        memo = {}
        try:
            setattr(user, _ROLES_ATTR, memo)
        except AttributeError:  # pragma: no cover
            pass
    if workspace.pk not in memo:
        memo[workspace.pk] = _resolve(user, workspace)
    return memo[workspace.pk]


def _resolve(user, workspace) -> WorkspaceRole | None:
    from connect_labs.marketplace.models import OrgMembership
    from connect_labs.utils.dimagi_user import is_dimagi_staff

    org_data = None
    if workspace.connect_program_ids:
        org_data = connect_org_data(user)
    teams, names = delivering_teams(org_data, workspace.connect_program_ids)
    is_admin = OrgMembership.objects.filter(
        org_id=workspace.org_id, user_id=user.pk, role="admin", granted_for_org_id=workspace.org_id
    ).exists()
    if is_dimagi_staff(user) or is_admin:
        return WorkspaceRole(PROGRAM_MANAGER, teams=teams, team_names=names)
    if teams:
        return WorkspaceRole(NETWORK_MANAGER, teams=teams, team_names=names)
    return None


def role_for_program(user, program_id) -> WorkspaceRole | None:
    return role_in(user, workspace_for_program(program_id))


def programs_for(user) -> set[int]:
    """Labs-only opportunity ids, in workspaces, that `user` has a role in.

    What puts a workspace programme in a Network Manager's picker without the
    synthetic-data toggle (`labs.context.get_org_data`)."""
    from connect_labs.labs.models import Workspace
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    remembered = getattr(user, _PROGRAMS_ATTR, None)
    if remembered is not None:
        return remembered
    orgs = [ws.org_id for ws in Workspace.objects.all() if role_in(user, ws) is not None]
    found = set()
    if orgs:
        found = set(
            SyntheticOpportunity.objects.filter(labs_only=True, enabled=True, labs_org_id__in=orgs).values_list(
                "opportunity_id", flat=True
            )
        )
    try:
        setattr(user, _PROGRAMS_ATTR, found)
    except AttributeError:  # pragma: no cover
        pass
    return found


class AdoptRefused(Exception):
    """A labs-only program's organisation cannot be made a workspace (see `adopt_program`)."""


def adopt_program(program_id, *, user=None):
    """Turn on workspace roles for the labs-only program `program_id`.

    Flags the program's labs-only organisation as a workspace -- the one thing
    both the `setup_autoconnect_workspace` command and the "Turn on workspace
    roles" button do, so the two can never disagree. Returns
    ``(workspace, opp, created)``; for an organisation that already is one it
    changes nothing (``created`` False). Logged on the audit trail when it does.

    Raises `AdoptRefused` for an id that is not a labs-only program, for one not
    filed under a labs-only organisation, and for an organisation that holds any
    OTHER program -- the roles would then decide who opens those too.
    """
    from django.db import transaction
    from django.db.models import Q

    from connect_labs.labs.models import Workspace
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.labs.synthetic.org_tree import is_labs_only_org_row, synthetic_program_id

    try:
        pid = int(program_id)
    except (TypeError, ValueError):
        raise AdoptRefused(f"{program_id!r} is not a program id") from None
    opp = (
        SyntheticOpportunity.objects.filter(labs_only=True)
        .filter(Q(program_id=pid) | Q(program_id__isnull=True, opportunity_id=pid))
        .select_related("labs_org")
        .order_by("pk")
        .first()
    )
    if opp is None:
        raise AdoptRefused(f"there is no labs-only program {pid}")
    org = opp.labs_org
    if not is_labs_only_org_row(org):
        raise AdoptRefused(f"labs-only program {pid} is not filed under a labs-only organisation")
    others = sorted({synthetic_program_id(o) for o in SyntheticOpportunity.objects.filter(labs_org=org)} - {pid})
    if others:
        raise AdoptRefused(
            f"the organisation {org.name!r} also holds program(s) {', '.join(map(str, others))}; "
            "workspace roles would decide who can open those too, so they were not turned on"
        )
    with transaction.atomic():
        workspace, created = Workspace.objects.get_or_create(org=org)
    if created:
        from connect_labs.audit_trail.models import Action
        from connect_labs.audit_trail.service import record

        record(
            Action.CREATE,
            resource_type="workspace",
            resource_id=workspace.pk,
            program_id=pid,
            labs_only=True,
            metadata={"organisation": org.slug},
            user=user,
        )
        logger.info("workspace roles turned on for labs-only program %s (organisation %s)", pid, org.slug)
    return workspace, opp, created


def delivering_orgs(org_data, connect_program_ids) -> dict:
    """Every Connect organisation this tree shows delivering in the linked programmes.

    Unlike `delivering_teams`, not limited to the person's own organisations: a
    Program Manager whose Connect organisation manages the programmes sees their
    partners' opportunities, and this is how labs lists the teams for them. Name
    where the tree has one, else the slug."""
    linked = {int(p) for p in connect_program_ids or []}
    if not org_data or not linked:
        return {}
    names = {str(o.get("slug")): o.get("name") for o in org_data.get("organizations") or [] if o.get("slug")}
    found = {}
    for opp in org_data.get("opportunities") or []:
        try:
            program = int(opp.get("program"))
        except (TypeError, ValueError):
            continue
        slug = str(opp.get("organization") or "")
        if program in linked and slug and not opp.get("labs_only"):
            found.setdefault(slug, names.get(slug) or slug)
    return found


def team_choices(workspace, viewer, user=None, records=()) -> list[dict]:
    """The teams to offer someone filing new work, as ``[{"slug", "name"}]``.

    A Network Manager: their own teams. A Program Manager: every team their
    Connect tree shows delivering in the linked programmes, plus any team already
    on a record (`records`: plans/groups with ``data["team"]``), plus "" -- work
    only Program Managers see. A Program Manager's tree may not show every
    partner (only what their own Connect account can see); the record teams fill
    the rest in as partners start work."""
    if viewer is None:
        return []
    if not viewer.is_program_manager:
        return [{"slug": t, "name": viewer.team_names.get(t) or t} for t in viewer.teams]
    org_data = connect_org_data(user) if user is not None else None
    teams = dict(delivering_orgs(org_data, workspace.connect_program_ids))
    for t in viewer.teams:
        teams.setdefault(t, viewer.team_names.get(t) or t)
    for r in records:
        t = (getattr(r, "data", None) or {}).get("team")
        if t:
            teams.setdefault(t, t)
    choices = [{"slug": "", "name": "Program Managers only"}]
    choices += [{"slug": slug, "name": name} for slug, name in sorted(teams.items(), key=lambda kv: kv[1].lower())]
    return choices
