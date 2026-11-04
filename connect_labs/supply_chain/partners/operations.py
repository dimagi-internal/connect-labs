"""Partner operations: how each partner distributes, its network, and who runs which place.

Registered into the one registry in operations.py, so the screens, the HTTP API and the MCP
server all reach the same four.
"""

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import records
from connect_labs.supply_chain.operations import ID, _data_with, obj, record, register_operation
from connect_labs.supply_chain.partners import services
from connect_labs.supply_chain.partners.models import DISTRIBUTION_MODELS

_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}

_PLAN_DATA = _data_with(
    ("org_id", "distribution_model", "starts_on", "source"),
    org_id=ID,
    distribution_model={"enum": list(DISTRIBUTION_MODELS)},
    starts_on=_DATE,
    build_network={"type": "boolean"},
    move_workers={"type": "boolean"},
    source={"enum": list(records.SOURCES)},
    recorded_by_org_id=ID,
    note={"type": "string"},
)


def _org(org_id):
    org = LabsOrg.objects.filter(pk=org_id).first()
    if org is None:
        raise ValueError(f"organisation {org_id} does not exist")
    return org


@register_operation(
    name="partner_plan_list",
    summary=(
        "How each partner says it distributes (through facilities, straight to field workers, or a mix), "
        "kept as dated history: the current plan has no ended_on. Filter to one partner by org_id."
    ),
    input_schema=obj({"org_id": ID, "current_only": {"type": "boolean"}}),
)
def partner_plan_list(access, org_id=None, current_only=False):
    from connect_labs.supply_chain.models import PartnerPlan

    plans = PartnerPlan.objects.filter(program_id=access._require_program())
    if org_id is not None:
        plans = plans.filter(org_id=org_id)
    if current_only:
        plans = plans.filter(ended_on__isnull=True)
    return [record(p) for p in plans]


@register_operation(
    name="partner_plan_set",
    summary=(
        "State how a partner distributes from a day on. A change of model is recorded as a dated change of "
        "plan (the plan before ends that day), never as field workers quietly moved. With build_network the "
        "partner's warehouse is created if it has none, every place it runs gets a sensible minimum and maximum "
        "stock, and field workers are placed under the right point: straight under the warehouse for "
        "direct_to_workers, under a facility for through_facilities. Field workers move only with move_workers; "
        "otherwise the ones who would move are listed in the result."
    ),
    input_schema=obj({"data": _PLAN_DATA}, required=("data",)),
    is_write=True,
)
def partner_plan_set(access, data):
    program_id = access._require_program()
    org = _org(data["org_id"])
    starts_on = services.as_day(data["starts_on"])
    previous = services.current_plan(program_id, org.pk)
    plan = services.set_plan(
        program_id,
        org,
        data["distribution_model"],
        starts_on,
        source=data["source"],
        recorded_by_org_id=data.get("recorded_by_org_id"),
        note=data.get("note", ""),
    )
    result = {"plan": record(plan), "changed_from": previous.distribution_model if previous else None}
    if data.get("build_network") or data.get("move_workers"):
        built = services.build_network(
            program_id,
            org,
            plan.distribution_model,
            previous_model=previous.distribution_model if previous else None,
            move_workers=bool(data.get("move_workers")),
        )
        if built["workers_moved"]:
            plan.note = (plan.note + "\n" if plan.note else "") + (
                f"{len(built['workers_moved'])} field worker(s) moved with this change: "
                + ", ".join(built["workers_moved"])
            )
            plan.save(update_fields=["note", "updated_at"])
            result["plan"] = record(plan)
        result["network"] = {
            "warehouse": record(built["warehouse"]),
            "created_warehouse": built["created_warehouse"],
            "bands_set": built["bands_set"],
            "workers_moved": built["workers_moved"],
            "workers_waiting": built["workers_waiting"],
        }
    return result


@register_operation(
    name="partner_places_assign",
    summary=(
        "Say which partner runs these places, so 'Run by: not recorded' goes away. Everything below a place "
        "that names no one belongs to the partner above it. Places that already name an organisation are left "
        "alone unless only_unassigned is false."
    ),
    input_schema=obj(
        {
            "org_id": ID,
            "supply_point_ids": {"type": "array", "items": ID, "minItems": 1},
            "only_unassigned": {"type": "boolean"},
        },
        required=("org_id", "supply_point_ids"),
    ),
    is_write=True,
)
def partner_places_assign(access, org_id, supply_point_ids, only_unassigned=True):
    changed = services.assign_places(
        access._require_program(), _org(org_id), supply_point_ids, only_unassigned=only_unassigned
    )
    return {"assigned": [{"id": p.pk, "name": p.name} for p in changed], "count": len(changed)}
