"""Who may declare which organisation runs a programme, and whom they may name.

Declaring the running organisation decides who "we" are in a programme, so it
is the authority every provenance rule protects. Until 2026-10-18 only platform
support could do it, which left every new programme with no runner -- and its
own managers partners in it -- until someone asked support. Now:

    first declaration    a member of the organisation that MANAGES the programme
                         in Connect. Read from Connect's own answer for the
                         caller's token (`opp_org_program_list`: a programme is
                         listed only to members of the organisation that owns
                         it), never from a labs-only registration, whose
                         organisation is a name-bucket anyone who can register a
                         synthetic opp can file under.
                         A synthetic (registered labs-only) programme has no
                         Connect manager at all, so on one of those a Dimagi
                         member who can open it declares Dimagi -- and only
                         Dimagi -- to run it (2026-10-21).
    changing it          an admin granted for the current running organisation,
                         or a Connect member of it when Connect knows it (Connect
                         membership is that organisation's grant; labs cannot see
                         Connect's own admin role). Dimagi's runs change only by
                         support: its people are a whole email domain.
    either               platform support, or the system caller.

and the organisation named must be one the evidence ties to the programme:

    - the managing organisation itself (by Connect id, never by slug);
    - Dimagi, when the caller manages the programme AND is Dimagi, or when the
      programme is synthetic, has no runner yet, and the caller is a Dimagi
      member who can open it;
    - an organisation delivering an opportunity in the programme, as Connect
      lists it to the caller (matched on the row's Connect slug; a Connect slug
      shared by two rows matches neither);
    - an organisation awarded a call for this programme by its managing
      organisation or its current runner (the awarded-LLO case);
    - the organisation whose call for this programme awarded the current
      runner (handing the programme back).

A labs-only organisation is never named this way: it is visibility, not a party.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied

from connect_labs.labs.models import LabsOrg

logger = logging.getLogger(__name__)


def _user_of(access):
    caller = getattr(access, "caller", None)
    return getattr(access, "user", None) or getattr(caller, "user", None)


def _as_int(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _connect_only(data: dict) -> dict:
    """Connect's own answer: every labs-only entry and reserved key removed."""
    from connect_labs.labs.synthetic.org_tree import drop_labs_only_keys, is_reserved_key

    data = drop_labs_only_keys(dict(data))
    return {
        "organizations": [
            o
            for o in data.get("organizations") or []
            if not o.get("labs_only") and _as_int(o.get("id")) is not None and not is_reserved_key(o.get("slug"))
        ],
        "programs": [p for p in data.get("programs") or [] if not p.get("labs_only")],
        "opportunities": [o for o in data.get("opportunities") or [] if not o.get("labs_only")],
    }


def connect_tree(access, *, refresh=False) -> dict | None:
    """The caller's organisations, programmes and opportunities as CONNECT lists them, or None.

    The token first, because it is Connect's answer now; a session's list was
    fetched at login and misses a programme made since. `refresh` skips the
    cache, for the one moment a stale list would refuse a legitimate manager:
    declaring a programme created after the list was cached.
    """
    user = _user_of(access)
    if user is None:
        return None
    from connect_labs.labs.connect_tokens import get_valid_access_token
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

    data = None
    try:
        token = get_valid_access_token(user)
    except Exception:  # noqa: BLE001 -- no token: try the session
        token = None
    if token:
        data = fetch_user_organization_data(token, force_refresh=refresh, owner=getattr(user, "username", None))
    if data is None:
        request = getattr(access, "request", None)
        session = getattr(request, "session", None)
        if session is not None:
            data = (session.get("labs_oauth") or {}).get("organization_data")
    return _connect_only(data) if data else None


def managing_org_id(tree, program_id) -> int | None:
    """The Connect id of the organisation that manages `program_id`, if the caller is one of its members."""
    if not tree:
        return None
    programme = next((p for p in tree["programs"] if _as_int(p.get("id")) == int(program_id)), None)
    if programme is None:
        return None
    slug = programme.get("organization")
    org = next((o for o in tree["organizations"] if slug not in (None, "") and o.get("slug") == slug), None)
    return _as_int(org.get("id")) if org else None


def _may_change(access, current) -> bool:
    from connect_labs.supply_chain.org_access import _connect_member, administers
    from connect_labs.supply_chain.runner import DIMAGI_ORG_SLUG

    if current.slug == DIMAGI_ORG_SLUG:
        return False
    if administers(access, current):
        return True
    return not current.labs_only and _connect_member(access, current)


def _dimagi_on_synthetic(access, program_id) -> bool:
    """Whether this caller is a Dimagi member who can open `program_id`, a registered labs-only programme.

    A synthetic programme exists nowhere in Connect, so nobody can ever manage
    it there; without this, only support (or a seeder) could give one a runner,
    and every supply write on it is refused until one is declared. Opening it is
    checked with the same rule its supply team is (`_can_open_programme`: the
    registration's `allowed_domains` / workspace), so a Dimagi address that the
    programme does not admit gets nothing here.
    """
    from connect_labs.supply_chain.runner.operations import _can_open_programme
    from connect_labs.supply_chain.scopes import is_synthetic
    from connect_labs.utils.dimagi_user import is_dimagi_user

    user = _user_of(access)
    return (
        user is not None
        and is_dimagi_user(user)
        and is_synthetic(program_id)
        and _can_open_programme(user, program_id)
    )


def _delivers_in(tree, program_id, org) -> bool:
    slug = org.connect_organization_slug
    if not tree or not slug or org.connect_organization_id is None:
        return False
    if LabsOrg.objects.filter(connect_organization_slug=slug).exclude(pk=org.pk).exists():
        return False
    return any(
        _as_int(o.get("program")) == int(program_id) and o.get("organization") == slug for o in tree["opportunities"]
    )


def why_eligible(access, program_id, org, *, tree, manager_id, current) -> str | None:
    """Why `org` may be named to run `program_id` by this caller, or None if nothing ties it there."""
    from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse
    from connect_labs.supply_chain.runner import DIMAGI_ORG_SLUG
    from connect_labs.utils.dimagi_user import is_dimagi_user

    pid = int(program_id)
    if org.labs_only:
        return None
    if current is not None and org.pk == current.pk:
        return "it already runs the program"
    if org.slug == DIMAGI_ORG_SLUG:
        user = _user_of(access)
        if manager_id is not None and user is not None and is_dimagi_user(user):
            return "you manage the program in Connect, as Dimagi"
        if manager_id is None and current is None and _dimagi_on_synthetic(access, pid):
            return "it is a synthetic labs-only program, and you are Dimagi"
        return None
    if manager_id is not None and org.connect_organization_id == manager_id:
        return "it manages the program in Connect"
    if _delivers_in(tree, pid, org):
        return "it delivers an opportunity in the program on Connect"
    awarders = list(LabsOrg.objects.filter(connect_organization_id=manager_id, labs_only=False)) if manager_id else []
    if current is not None:
        awarders.append(current)
    if (
        awarders
        and SolicitationResponse.objects.filter(
            status="awarded", llo_entity=org, solicitation__program_id=pid, solicitation__owner_org__in=awarders
        ).exists()
    ):
        return "it was awarded a call for the program"
    if (
        current is not None
        and Solicitation.objects.filter(
            program_id=pid, owner_org=org, responses__status="awarded", responses__llo_entity=current
        ).exists()
    ):
        return "its call for the program awarded the organisation that runs it now"
    return None


def require_may_declare(access, program_id, org) -> None:
    """Raise PermissionDenied unless this caller may make `org` the programme's running organisation."""
    from connect_labs.supply_chain.org_access import is_staff_or_system
    from connect_labs.supply_chain.runner import declared_runner

    if is_staff_or_system(access):
        return
    current = declared_runner(program_id)
    tree = connect_tree(access)
    manager_id = managing_org_id(tree, program_id)
    if manager_id is None:
        # A programme made since the caller's list was cached is not one they
        # do not manage: ask Connect again before refusing.
        tree = connect_tree(access, refresh=True) or tree
        manager_id = managing_org_id(tree, program_id)
    if current is None:
        from connect_labs.supply_chain.runner import DIMAGI_ORG_SLUG

        if manager_id is None and not (org.slug == DIMAGI_ORG_SLUG and _dimagi_on_synthetic(access, program_id)):
            raise PermissionDenied(
                f"program {program_id} has no running organisation yet, and only a member of the organisation that "
                "manages it in Connect (or platform support) declares the first one; on a synthetic labs-only "
                "program, a Dimagi member who can open it may declare Dimagi"
            )
    elif not _may_change(access, current):
        raise PermissionDenied(
            f"{current.name} runs program {program_id}; only its admins (or, for an organisation Connect knows, "
            "its Connect members) or platform support change who runs it"
        )
    if why_eligible(access, program_id, org, tree=tree, manager_id=manager_id, current=current) is None:
        raise PermissionDenied(
            f"nothing ties {org.name} to program {program_id}: name the organisation that manages it in Connect, one "
            "delivering an opportunity in it, or one awarded a call for it (award_response). Platform support can "
            "declare any other."
        )
