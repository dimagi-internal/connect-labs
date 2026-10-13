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
    from connect_labs.supply_chain.runner import declare, is_operator

    if not is_staff_or_system(access):
        raise PermissionDenied("only platform support declares which organisation runs a program")
    org = LabsOrg.objects.filter(pk=org_id).first()
    if org is None:
        raise ValueError(f"organisation {org_id} does not exist")
    user = getattr(access, "user", None)
    declare(access._require_program(), org, by=user if is_operator(user) else None, note=note)
    if hasattr(access, "_supply_standing"):
        del access._supply_standing
    return _describe(access)
