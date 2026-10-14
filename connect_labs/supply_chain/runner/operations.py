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
        "Declare which organisation runs this programme. Platform support only: it decides who acts as "
        "the programme, so no member of any organisation -- including the one that runs it -- may change it."
    ),
    input_schema=obj({"org_id": ID, "note": {"type": "string"}}, required=("org_id",)),
    is_write=True,
)
def programme_runner_set(access, org_id, note=""):
    from connect_labs.supply_chain.org_access import is_staff_or_system
    from connect_labs.supply_chain.runner import DIMAGI_ORG_SLUG, declare, is_operator, open_to_any_domain

    if not is_staff_or_system(access):
        raise PermissionDenied("only platform support declares which organisation runs a program")
    org = LabsOrg.objects.filter(pk=org_id).first()
    if org is None:
        raise ValueError(f"organisation {org_id} does not exist")
    program_id = access._require_program()
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
    declare(program_id, org, by=user if is_operator(user) else None, note=note)
    if hasattr(access, "_supply_standing"):
        del access._supply_standing
    return _describe(access)
