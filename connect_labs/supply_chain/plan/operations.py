"""The stock plan's operations: read and save the plan, and read the planning picture it shapes.

Registered into the single registry, so the HTTP API and the MCP server get them with
no second list. Design and method: plan/service.py (Sophie Feintuch's RUTF feedback,
"Combining Supply with our CBI dashboards").
"""

import json
from datetime import date
from decimal import Decimal

from connect_labs.supply_chain.history.labels import actor_label
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.operations import ID, obj, record, register_operation
from connect_labs.supply_chain.plan import service
from connect_labs.supply_chain.plan.models import StockPlan

_DATE = {"type": "string", "format": "date"}

_PLAN_DATA = obj(
    {
        "item_id": ID,
        "enrolment_per_flw_per_week": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 50,
            "description": (
                "New children each field worker is expected to enrol a week. "
                "Null goes back to the default: each worker's own recent rate."
            ),
        },
        "buffer_days": {"type": "integer", "minimum": 0, "maximum": 60},
        "buffer_days_by_point": {
            "type": "object",
            "additionalProperties": {"type": "integer", "minimum": 0, "maximum": 60},
            "description": (
                "Extra days for one field worker or facility, keyed by its supply point id; replaces the whole set."
            ),
        },
        "target_cases": {"type": "integer", "minimum": 1, "maximum": 1000000},
        "target_cases_by_partner": {
            "type": "object",
            "additionalProperties": {"type": "integer", "minimum": 1, "maximum": 1000000},
            "description": (
                "How many children each partner means to treat, keyed by its organisation id; replaces the "
                "set. When any is set the programme's target is their sum. An empty object goes back to one "
                "programme-wide target."
            ),
        },
    },
    required=("item_id",),
)


# What a field reads as when nobody has chosen it.
_UNSET = {
    "enrolment_per_flw_per_week": (None,),
    "buffer_days": (service.DEFAULT_BUFFER_DAYS,),
    "buffer_days_by_point": ({},),
    "target_cases": (service.DEFAULT_TARGET_CASES,),
    "target_cases_by_partner": ({},),
}


def history(program_id, plan):
    """Who changed the plan, when, and what each input was before, with the default beside it."""
    if plan is None:
        return []
    revisions = (
        Revision.objects.filter(
            program_id=program_id,
            content_type__model="stockplan",
            object_id=str(plan.pk),
        )
        .select_related("call", "call__actor")
        .order_by("-recorded_at", "-id")
    )
    defaults = {
        "enrolment_per_flw_per_week": service.INPUTS["enrolment_per_flw_per_week"]["default_text"],
        "buffer_days": service.INPUTS["buffer_days"]["default_text"],
        "target_cases": service.INPUTS["target_cases"]["default_text"],
        "target_cases_by_partner": service.INPUTS["target_cases_by_partner"]["default_text"],
        "buffer_days_by_point": "the same for everyone",
    }
    out = []
    for revision in revisions:
        changes = []
        for field, pair in (revision.changes or {}).items():
            if field in ("item_id", "program_id", "id"):
                continue
            label = service.INPUTS.get(field, {}).get("label") or (
                "Extra days for named places" if field == "buffer_days_by_point" else field
            )
            old, new = (pair if isinstance(pair, list) else [None, pair])[:2]
            if old == new or (revision.action == "create" and new in _UNSET.get(field, ())):
                continue  # a new plan lists only what it set away from the defaults
            changes.append(
                {
                    "field": field,
                    "label": label,
                    "was": old,
                    "now": new,
                    "default": defaults.get(field, ""),
                }
            )
        if changes:
            out.append(
                {
                    "when": revision.recorded_at.isoformat(),
                    "who": actor_label(revision.call),
                    "action": revision.action,
                    "changes": changes,
                }
            )
    return out


def _wire(value):
    return json.loads(json.dumps(value, default=lambda o: str(o) if isinstance(o, (Decimal, date)) else repr(o)))


@register_operation(
    name="stock_plan_get",
    summary=(
        "The saved stock plan for one item: how many new children each field worker is expected to enrol a week "
        "(default: each worker's own recent rate), the buffer in extra days (for everyone, or for named workers "
        "and facilities; default 0) and the target number of children (default 500, or one per partner), each with "
        "the default beside it, plus who changed what and when. Nothing saved yet reads as all defaults. Read-only."
    ),
    input_schema=obj({"item_id": ID}, required=("item_id",)),
)
def stock_plan_get(access, item_id):
    program_id = access._require_program()
    item = access._resolve_item(item_id)
    plan = service.saved_plan(program_id, item)
    values = service.plan_values(plan)
    return _wire({"item_id": item.pk, "saved": plan is not None, "plan": values, "history": history(program_id, plan)})


@register_operation(
    name="stock_plan_set",
    summary=(
        "Change the stock plan for one item. Only what is sent changes: enrolment_per_flw_per_week (null goes back "
        "to each worker's own recent rate), buffer_days (extra days of stock for everyone), buffer_days_by_point "
        "(extra days for named workers or facilities, by supply point id; replaces the set) and target_cases. "
        "The change is recorded with who made it and what it was before. The pages and the forecast-based "
        "operations (stock_plan_view) use the plan at once."
    ),
    input_schema=obj({"data": _PLAN_DATA}, required=("data",)),
    is_write=True,
)
def stock_plan_set(access, data):
    program_id = access._require_program()
    item = access._resolve_item(data["item_id"])
    fields = {}
    if "enrolment_per_flw_per_week" in data:
        rate = data["enrolment_per_flw_per_week"]
        fields["enrolment_per_flw_per_week"] = None if rate is None else Decimal(str(rate))
    if "buffer_days" in data:
        fields["buffer_days"] = data["buffer_days"]
    if "buffer_days_by_point" in data:
        known = {str(pk) for pk in SupplyPoint.objects.filter(program_id=program_id).values_list("pk", flat=True)}
        unknown = sorted(set(data["buffer_days_by_point"]) - known)
        if unknown:
            raise ValueError(f"no such supply point in this programme: {', '.join(unknown)}")
        fields["buffer_days_by_point"] = data["buffer_days_by_point"]
    if "target_cases" in data:
        fields["target_cases"] = data["target_cases"]
    if "target_cases_by_partner" in data:
        from connect_labs.labs.models import LabsOrg

        asked = data["target_cases_by_partner"]
        found = LabsOrg.objects.filter(pk__in=[int(k) for k in asked if str(k).isdigit()]).values_list("pk", flat=True)
        unknown = sorted(set(asked) - {str(pk) for pk in found})
        if unknown:
            raise ValueError(f"no such organisation: {', '.join(unknown)}")
        fields["target_cases_by_partner"] = data["target_cases_by_partner"]
    # One save, so the history reads as one change by one person (a create then an edit would be two).
    plan = service.saved_plan(program_id, item)
    if plan is None:
        plan = StockPlan.objects.create(program_id=program_id, item=item, **fields)
    else:
        for name, value in fields.items():
            setattr(plan, name, value)
        plan.save()
    plan.refresh_from_db()
    return record(plan)


@register_operation(
    name="stock_plan_view",
    summary=(
        "The planning picture for one item, shaped by the saved plan: per field worker what to issue at the next "
        "handout (children due a follow-up, expected new children, buffer, less what Supply's records say they "
        "hold, step by step with where each number came from) and their run-dry day; who may run short this "
        "week; the break-even (does the stock left cover the children in treatment and those still to come, up "
        "to the target, and up to how many visits each); each store's need and whether it lasts until its next "
        "delivery; and each worker's real dose against the protocol. Built on the stock forecast. A partner (anyone "
        "who is not in the running organisation) gets only the places their own organisations run, and no "
        "programme-wide break-even. Read-only."
    ),
    input_schema=obj(
        {
            "item_id": ID,
            "as_of": _DATE,
            "horizon_weeks": {"type": "integer", "minimum": 1, "maximum": 26},
            "course_size": {"type": "number", "exclusiveMinimum": 0},
        },
        required=("item_id",),
    ),
)
def stock_plan_view(access, item_id, as_of=None, horizon_weeks=8, course_size=None):
    program_id = access._require_program()
    item = access._resolve_item(item_id)
    picture = service.compute(
        program_id,
        item,
        on_date=date.fromisoformat(as_of) if as_of else None,
        horizon_weeks=horizon_weeks,
        course_size=Decimal(str(course_size)) if course_size else None,
    )
    picture.pop("forecast")  # the forecast has its own operation; the picture is built on it
    mine = service.partner_point_ids(access, program_id)
    picture = {**picture, "scoped": False} if mine is None else service.scope_to(picture, mine)
    return _wire(picture)
