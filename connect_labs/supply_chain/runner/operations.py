"""Reading and declaring which organisation runs a programme (#2458)."""

from django.core.exceptions import PermissionDenied

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.operations import ID, obj, register_operation


def _describe(access):
    from connect_labs.supply_chain.runner import standing

    held = standing(access)
    org = held.runner
    return {
        "program_id": int(access._require_program()),
        "org_id": org.pk if org else None,
        "org_name": org.name if org else None,
        "your_standing": held.role,
    }


@register_operation(
    name="programme_runner_get",
    summary=(
        "Which organisation runs this programme -- the one whose people tender, award, order and pay, "
        "and who 'we' are on its pages -- and your own standing in it: 'programme' (a member of that "
        "organisation), 'operator' (platform support acting for it) or 'partner' (you can read; you "
        "write through an update link). org_id is null when nobody has declared one yet."
    ),
    input_schema=obj({}),
)
def programme_runner_get(access):
    return _describe(access)


@register_operation(
    name="programme_runner_set",
    summary=(
        "Declare which organisation runs this programme: its members tender, award, contract, order and pay, "
        "and record first-hand. org_id is the LABS organisation id (`org_list` / `org_upsert` return it), not "
        "a Connect id. Who may call it: with no runner yet, a member of the organisation that manages the "
        "programme in Connect; once one is declared, an admin of that organisation (or, for an organisation "
        "Connect knows, its Connect members); platform support always. The organisation named must be the one "
        "managing the programme in Connect (bind it first with org_upsert + connect_organization_id), Dimagi "
        "(when you are Dimagi and manage it), one delivering an opportunity in it, or one awarded a call for "
        "it (award_response) -- support declares any other. Labs-only organisations are never named this way."
    ),
    input_schema=obj({"org_id": ID, "note": {"type": "string"}}, required=("org_id",)),
    is_write=True,
)
def programme_runner_set(access, org_id, note=""):
    from connect_labs.supply_chain.runner import DIMAGI_ORG_SLUG, declare, open_to_any_domain
    from connect_labs.supply_chain.runner.declaring import require_may_declare

    org = LabsOrg.objects.filter(pk=org_id).first()
    if org is None:
        raise ValueError(f"organisation {org_id} does not exist")
    program_id = access._require_program()
    require_may_declare(access, program_id, org)
    if org.slug != DIMAGI_ORG_SLUG and open_to_any_domain(program_id):
        # Another organisation's quotes and prices would be readable by every
        # partner signed in to labs. Dimagi's own invented demo programmes stay
        # open, as they always have been.
        raise ValueError(
            f"program {program_id} is labs-only and open to every email domain, so anyone signed in could read "
            f"{org.name}'s quotes and prices. Set the program's allowed domains first "
            "(synthetic_set_allowed_domains), then declare it."
        )
    user = getattr(access, "user", None)
    declare(program_id, org, by=user if getattr(user, "pk", None) else None, note=note)
    if hasattr(access, "_supply_standing"):
        del access._supply_standing
    return _describe(access)


# ---- the running organisation's team ----------------------------------------
#
# A running organisation takes no marketplace invitations (design §8, C1), so
# its people are added here: by platform support, by an admin the
# organisation already has, or by a Connect member of an organisation Connect
# knows. Seeing the programme is not enough, and neither is being an ordinary
# member of the team.

ROLE = {"type": "string", "enum": ["admin", "member"]}


def _runner_for_team(access, *, change=False):
    from connect_labs.supply_chain.org_access import _connect_member, administers, is_staff_or_system
    from connect_labs.supply_chain.runner import DIMAGI_ORG_SLUG, running_org, standing

    org = running_org(access._require_program())
    if org is None:
        raise ValueError("no organisation runs this program yet, so it has no team; declare one first")
    if org.slug == DIMAGI_ORG_SLUG:
        raise ValueError("Dimagi's team is everyone with a Dimagi address; it is not kept as a list")
    # A Connect member of a running organisation Connect knows counts as an
    # admin here: Connect's membership is that organisation's grant, and labs
    # cannot see Connect's own admin role. Without it nobody but support could
    # add the first person to a Connect organisation's team.
    if change and not (is_staff_or_system(access) or administers(access, org) or _connect_member(access, org)):
        raise PermissionDenied(
            f"only platform support, an admin of {org.name} or a Connect member of it changes its team"
        )
    if not change and not standing(access).is_programme:
        raise PermissionDenied(f"only {org.name}'s own team and platform support see who is on it")
    return org


def _member_row(membership):
    user = membership.user
    return {
        "user_id": user.pk,
        "name": getattr(user, "name", "") or user.get_full_name() or user.username,
        "email": user.email,
        "role": membership.role,
        "added_on": membership.created_at.date().isoformat(),
    }


def _team(org):
    from connect_labs.marketplace.models import OrgMembership

    memberships = OrgMembership.objects.filter(org=org, granted_for_org_id=org.pk).select_related("user")
    return {
        "org_id": org.pk,
        "org_name": org.name,
        "members": [_member_row(m) for m in memberships.order_by("role", "user__email")],
    }


@register_operation(
    name="programme_team_list",
    summary=(
        "Who is on the team of the organisation that runs this programme: the people who tender, award, "
        "order and pay for it. Readable by that team and by platform support."
    ),
    input_schema=obj({}),
)
def programme_team_list(access):
    return _team(_runner_for_team(access))


@register_operation(
    name="programme_team_add",
    summary=(
        "Add a person to the team of the organisation that runs this programme, by the email they sign in to "
        "labs with (they must have signed in once). 'admin' may also change the team; 'member' acts for the "
        "programme. Platform support, an existing admin of that organisation, or a Connect member of it, only."
    ),
    input_schema=obj({"email": {"type": "string", "minLength": 3}, "role": ROLE}, required=("email",)),
    is_write=True,
)
def programme_team_add(access, email, role="member"):
    from django.contrib.auth import get_user_model

    from connect_labs.marketplace.models import OrgMembership

    org = _runner_for_team(access, change=True)
    address = email.strip().lower()
    user = get_user_model().objects.filter(email__iexact=address).order_by("pk").first()
    if user is None:
        raise ValueError(f"nobody has signed in to labs as {address} yet; ask them to sign in once, then add them")
    if not _can_open_programme(user, access._require_program()):
        raise ValueError(
            f"{address} could not open this program: it is open only to its allowed email domains. "
            "Add their domain to the program first (synthetic_set_allowed_domains)."
        )
    membership = OrgMembership.objects.filter(org=org, user=user).first()
    if membership is None:
        OrgMembership.objects.create(org=org, user=user, role=role, added_by=getattr(access, "user", None))
    elif membership.role != role:
        membership.role = role
        membership.save(update_fields=["role"])
    return _team(org)


@register_operation(
    name="programme_team_remove",
    summary=(
        "Take a person off the team of the organisation that runs this programme. Platform support, an "
        "admin of that organisation, or a Connect member of it, only. The last admin cannot be removed."
    ),
    input_schema=obj({"user_id": ID}, required=("user_id",)),
    is_write=True,
)
def programme_team_remove(access, user_id):
    from connect_labs.marketplace.models import OrgMembership

    org = _runner_for_team(access, change=True)
    membership = OrgMembership.objects.filter(org=org, user_id=user_id).first()
    if membership is None:
        raise ValueError(f"user {user_id} is not on {org.name}'s team")
    if (
        membership.role == "admin"
        and not OrgMembership.objects.filter(org=org, role="admin", granted_for_org_id=org.pk)
        .exclude(pk=membership.pk)
        .exists()
    ):
        raise ValueError(f"{org.name} would have no admin left; add another admin first")
    membership.delete()
    return _team(org)


def _can_open_programme(user, program_id) -> bool:
    """Whether `user` may open the programme at all: a labs-only one is governed by its registration."""
    from django.db.models import Q

    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    pid = int(program_id)
    registrations = list(
        SyntheticOpportunity.objects.filter(labs_only=True).filter(
            Q(program_id=pid) | Q(program_id__isnull=True, opportunity_id=pid)
        )
    )
    return not registrations or any(opp.is_accessible_to(user) for opp in registrations)
