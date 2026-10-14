"""Who someone is in a workspace: a Program Manager, a Network Manager, or nobody.

A workspace (`labs.models.Workspace`) is a labs-only organisation whose
programmes are run with roles. Nothing here applies to any other organisation:
`workspace_for_program` returns None for them, and every caller falls back to
the rules it had before.

The first rule that answers, in this order:

  1. **The Users list** (`labs.models.WorkspaceUser`, on the workspace settings
     page): an entry for the person's email address makes them a Program
     Manager, or a Network Manager of the LLO it names. It wins over every rule
     below, so a Program Manager can look at the workspace as a partner would,
     and someone whose LLO membership does not resolve can still be let in. Any
     email address; the entry matches whoever signs in to labs with it
     (case-insensitively). `WorkspaceRole.testing` says a Network Manager role
     came from here.
  2. **Program Manager** -- Dimagi staff (`is_dimagi_staff`), or an admin of the
     workspace's organisation (`marketplace.OrgMembership`, granted for it).
     Sees and may do everything.
  3. **Network Manager of an LLO** -- a MEMBER of one of the workspace's LLOs
     (`labs.models.WorkspaceLLO`): Connect organisations listed on the settings
     page. Their team is that organisation's slug; in more than one, they are
     on each of those teams.
  4. **Network Manager, the older rule** -- someone in a Connect organisation
     that DELIVERS an opportunity in one of the workspace's
     `connect_program_ids`. Kept for workspaces set up that way; the settings
     page no longer edits that list.
  5. anyone else -- no role, and so no access to the workspace's programmes.

**Where the Connect answer comes from.** The Connect organisation tree labs
already holds for the person: the session's copy on the web
(`remember_org_data`, called by `labs.context.get_org_data`), else the
TTL-cached Connect fetch the MCP route already uses. Both can be up to an hour
old (`oauth._ORG_DATA_CACHE_TTL`), so someone added to an organisation in
Connect may need to refresh their organisation data (or sign in again) before
labs sees it. When the tree cannot be had at all, the answer is "no role": a
Network Manager is never assumed.

For rule 4 an opportunity is "delivered by" the organisation in its
`organization` field, and only organisations the person is a MEMBER of count
-- a Connect programme manager sees every opportunity in its programme,
delivered by other organisations, and must not become every team at once.

Every role that resolves is noted in `labs.models.WorkspaceAccess` (at most
every few minutes per person), which is how the settings page lists the people
each LLO has brought in.
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
    # A Network Manager because of an explicit Users entry (`WorkspaceUser`) -- often
    # a Program Manager looking at the workspace as a partner would.
    testing: bool = False

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
        if memo[workspace.pk] is not None:
            note_access(user, workspace, memo[workspace.pk])
    return memo[workspace.pk]


def user_entry(user, workspace):
    """The Users-list entry (`WorkspaceUser`) that is `user`'s here, or None.

    Matches the person it was filled in for, else their sign-in email
    (case-insensitively, as entries are stored lower-case), and fills ``user``
    in on the first match. A failed fill never costs the match."""
    from django.db import transaction
    from django.db.models import Q

    from connect_labs.labs.models import WorkspaceUser

    email = (getattr(user, "email", "") or "").strip().lower()
    match = Q(user_id=user.pk)
    if email:
        match |= Q(email=email)
    entry = WorkspaceUser.objects.filter(workspace=workspace).filter(match).order_by("pk").first()
    if entry is not None and entry.user_id is None:
        try:
            with transaction.atomic():
                WorkspaceUser.objects.filter(pk=entry.pk, user__isnull=True).update(user_id=user.pk)
            entry.user_id = user.pk
        except Exception:  # noqa: BLE001
            logger.warning("could not record who workspace user entry %s matched", entry.pk, exc_info=True)
    return entry


def llo_names(workspace) -> dict:
    """The workspace's LLOs, slug -> display name (its own name, else the slug), in the order added."""
    return {
        slug: name or slug for slug, name in workspace.llos.order_by("created_at", "pk").values_list("slug", "name")
    }


def member_slugs(org_data) -> set[str]:
    """The Connect organisations (slugs) a Connect tree says the person is a member of."""
    return {
        str(o.get("slug"))
        for o in (org_data or {}).get("organizations") or []
        if o.get("slug") and not o.get("labs_only")
    }


def _resolve(user, workspace, *, use_entry=True) -> WorkspaceRole | None:
    from connect_labs.labs.models import WorkspaceUser
    from connect_labs.marketplace.models import OrgMembership
    from connect_labs.utils.dimagi_user import is_dimagi_staff

    llos = llo_names(workspace)
    # 1. An explicit Users entry wins over every rule below.
    entry = user_entry(user, workspace) if use_entry else None
    if entry is not None and entry.role == WorkspaceUser.NETWORK_MANAGER and entry.team:
        return WorkspaceRole(
            NETWORK_MANAGER,
            teams=(entry.team,),
            team_names={entry.team: llos.get(entry.team) or entry.team},
            testing=True,
        )

    org_data = connect_org_data(user) if (llos or workspace.connect_program_ids) else None
    members = member_slugs(org_data)
    teams = [slug for slug in llos if slug in members]
    names = {slug: llos[slug] for slug in teams}
    derived, derived_names = delivering_teams(org_data, workspace.connect_program_ids)
    for slug in derived:
        if slug not in names:
            teams.append(slug)
            names[slug] = llos.get(slug) or derived_names.get(slug) or slug
    teams = tuple(teams)

    if entry is not None and entry.role == WorkspaceUser.PROGRAM_MANAGER:
        return WorkspaceRole(PROGRAM_MANAGER, teams=teams, team_names=names)
    # 2. Dimagi staff, or an admin of the workspace's organisation.
    is_admin = OrgMembership.objects.filter(
        org_id=workspace.org_id, user_id=user.pk, role="admin", granted_for_org_id=workspace.org_id
    ).exists()
    if is_dimagi_staff(user) or is_admin:
        return WorkspaceRole(PROGRAM_MANAGER, teams=teams, team_names=names)
    # 3 + 4. A member of an LLO, or of an organisation delivering in a linked programme.
    if teams:
        return WorkspaceRole(NETWORK_MANAGER, teams=teams, team_names=names)
    return None


def fallback_role(user, workspace) -> WorkspaceRole | None:
    """`user`'s role here without their Users entry -- what deleting it would leave
    them with. Neither memoised nor noted."""
    if workspace is None or user is None or not getattr(user, "is_authenticated", False):
        return None
    return _resolve(user, workspace, use_entry=False)


# A role noted this recently is not written again, unless it changed.
ACCESS_NOTE_INTERVAL_SECONDS = 300


def note_access(user, workspace, role) -> None:
    """Record that `user` had `role` in `workspace` just now (`WorkspaceAccess`).

    Writes at most once per ``ACCESS_NOTE_INTERVAL_SECONDS`` per person unless the
    role or teams changed -- otherwise it is one indexed read. Never raises, and
    runs in its own savepoint: a failed note must not cost anyone the page."""
    if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "pk", None) is None:
        return
    from datetime import timedelta

    from django.db import transaction
    from django.utils import timezone

    from connect_labs.labs.models import WorkspaceAccess

    try:
        now = timezone.now()
        fields = {"role": role.role, "teams": list(role.teams), "explicit": bool(role.testing)}
        with transaction.atomic():
            row = (
                WorkspaceAccess.objects.filter(workspace=workspace, user_id=user.pk)
                .only("pk", "role", "teams", "explicit", "last_seen")
                .first()
            )
            if row is None:
                WorkspaceAccess.objects.create(workspace=workspace, user_id=user.pk, last_seen=now, **fields)
                return
            unchanged = all(getattr(row, k) == v for k, v in fields.items())
            if unchanged and row.last_seen >= now - timedelta(seconds=ACCESS_NOTE_INTERVAL_SECONDS):
                return
            WorkspaceAccess.objects.filter(pk=row.pk).update(last_seen=now, **fields)
    except Exception:  # noqa: BLE001
        logger.warning("could not note workspace access (workspace %s)", getattr(workspace, "pk", None), exc_info=True)


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


# ---- the settings page's two lists: LLOs and Users --------------------------


class AccessRefused(Exception):
    """A change to a workspace's LLOs or Users that is refused, with why."""


def _audit(action, resource_type, resource_id, *, by, program_id, metadata):
    from connect_labs.audit_trail.service import record

    record(
        action,
        resource_type=resource_type,
        resource_id=resource_id,
        program_id=program_id,
        labs_only=True,
        metadata=metadata,
        user=by,
    )


def clean_slug(raw) -> str:
    return (raw or "").strip().lower()[:200]


def add_llo(workspace, slug: str, name: str = "", *, by, program_id=None, known_names=None):
    """Add the Connect organisation `slug` as an LLO of `workspace`: its members
    become Network Managers of it here. A blank ``name`` takes the one
    `known_names` (slug -> name; the Program Manager's own Connect tree) has.
    Logged on the audit trail."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.labs.models import WorkspaceLLO

    slug = clean_slug(slug)
    if not slug or any(c.isspace() for c in slug):
        raise AccessRefused("give the Connect organisation's slug, for example riverbend-health")
    name = (name or "").strip()[:255] or (known_names or {}).get(slug, "")
    llo, created = WorkspaceLLO.objects.get_or_create(
        workspace=workspace, slug=slug, defaults={"name": name, "added_by": by}
    )
    if not created:
        raise AccessRefused(f"{slug} is already an LLO here")
    _audit(
        Action.CREATE,
        "workspace_llo",
        llo.pk,
        by=by,
        program_id=program_id,
        metadata={"workspace": workspace.pk, "slug": slug},
    )
    return llo


def remove_llo(workspace, slug: str, *, by, program_id=None, metadata=None) -> bool:
    """Delete the LLO `slug`: its members stop being Network Managers here. Logged
    on the audit trail, with `metadata`. False if it was not an LLO. (Its
    microplans are the caller's to hand to Program Managers.)"""
    from connect_labs.audit_trail.models import Action
    from connect_labs.labs.models import WorkspaceLLO

    llo = WorkspaceLLO.objects.filter(workspace=workspace, slug=clean_slug(slug)).first()
    if llo is None:
        return False
    _audit(
        Action.DELETE,
        "workspace_llo",
        llo.pk,
        by=by,
        program_id=program_id,
        metadata={"workspace": workspace.pk, "slug": llo.slug, **(metadata or {})},
    )
    llo.delete()
    return True


def clean_email(raw) -> str:
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    email = (raw or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise AccessRefused(f"{(raw or '').strip()!r} is not an email address") from None
    return email[:254]


def is_dimagi_email(email: str) -> bool:
    """Whether `email` is a Dimagi sign-in (the rule `utils.dimagi_user` applies to users)."""
    from types import SimpleNamespace

    from connect_labs.utils.dimagi_user import is_dimagi_user

    return is_dimagi_user(SimpleNamespace(email=(email or "").strip().lower()))


def set_user(workspace, email: str, role: str, team: str = "", *, by, program_id=None, keep_team=""):
    """Give the email address `email` this access in `workspace` (`WorkspaceUser`):
    ``role`` "program_manager", or "network_manager" of ``team`` -- an LLO of the
    workspace, or `keep_team` (the team an entry already had). Adds the entry, or
    changes an existing one in place. Any email address, signed in yet or not.
    Logged on the audit trail, with ``non_dimagi_pm`` when it makes an address
    outside Dimagi a Program Manager. Returns ``(entry, created)``."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.labs.models import WorkspaceUser

    email = clean_email(email)
    if role not in (WorkspaceUser.PROGRAM_MANAGER, WorkspaceUser.NETWORK_MANAGER):
        raise AccessRefused("choose Program Manager, or Network Manager of an LLO")
    team = clean_slug(team) if role == WorkspaceUser.NETWORK_MANAGER else ""
    if role == WorkspaceUser.NETWORK_MANAGER:
        if not team:
            raise AccessRefused("choose the LLO they are a Network Manager of")
        if team not in llo_names(workspace) and team != clean_slug(keep_team):
            raise AccessRefused(f"{team} is not an LLO of this workspace; add it to the LLO list first")
    entry, created = WorkspaceUser.objects.get_or_create(
        workspace=workspace, email=email, defaults={"role": role, "team": team, "added_by": by}
    )
    metadata = {"workspace": workspace.pk, "role": role, "team": team, "matched_user_id": entry.user_id}
    if not created:
        metadata["before"] = {"role": entry.role, "team": entry.team}
        entry.role, entry.team = role, team
        entry.save(update_fields=["role", "team", "updated_at"])
    if role == WorkspaceUser.PROGRAM_MANAGER and not is_dimagi_email(email):
        metadata["non_dimagi_pm"] = True
    _audit(
        Action.CREATE if created else Action.UPDATE,
        "workspace_user",
        entry.pk,
        by=by,
        program_id=program_id,
        metadata=metadata,
    )
    return entry, created


def remove_user(workspace, entry_id, *, by, program_id=None) -> bool:
    """Delete a Users entry: that person falls back to the other rules. Logged on
    the audit trail. False if there was no such entry."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.labs.models import WorkspaceUser

    entry = WorkspaceUser.objects.filter(workspace=workspace, pk=entry_id).first()
    if entry is None:
        return False
    _audit(
        Action.DELETE,
        "workspace_user",
        entry.pk,
        by=by,
        program_id=program_id,
        metadata={"workspace": workspace.pk, "role": entry.role, "team": entry.team, "matched_user_id": entry.user_id},
    )
    entry.delete()
    return True


def entry_is_for(entry, user) -> bool:
    """Whether the Users entry `entry` is `user`'s own."""
    email = (getattr(user, "email", "") or "").strip().lower()
    return entry.user_id == getattr(user, "pk", None) or bool(email and entry.email == email)


def delivering_orgs(org_data, connect_program_ids) -> dict:
    """Every Connect organisation this tree shows delivering in the linked programmes.

    Unlike `delivering_teams`, not limited to the person's own organisations: a
    Program Manager whose Connect organisation manages the programmes sees their
    partners' opportunities. Name where the tree has one, else the slug."""
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

    A Network Manager: their own teams. A Program Manager: every LLO, every team
    their Connect tree shows delivering in the linked programmes (the older
    rule), plus any team already on a record (`records`: plans/groups with
    ``data["team"]``), plus "" -- work only Program Managers see."""
    if viewer is None:
        return []
    if not viewer.is_program_manager:
        return [{"slug": t, "name": viewer.team_names.get(t) or t} for t in viewer.teams]
    teams = dict(llo_names(workspace))
    org_data = connect_org_data(user) if (user is not None and workspace.connect_program_ids) else None
    for slug, name in delivering_orgs(org_data, workspace.connect_program_ids).items():
        teams.setdefault(slug, name)
    for t in viewer.teams:
        teams.setdefault(t, viewer.team_names.get(t) or t)
    for r in records:
        t = (getattr(r, "data", None) or {}).get("team")
        if t:
            teams.setdefault(t, t)
    choices = [{"slug": "", "name": "Program Managers only"}]
    choices += [{"slug": slug, "name": name} for slug, name in sorted(teams.items(), key=lambda kv: kv[1].lower())]
    return choices
