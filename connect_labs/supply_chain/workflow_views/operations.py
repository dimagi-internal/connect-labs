"""Pin a workflow into a programme's supply navigation, unpin it, list the pins (models.py)."""

from django.utils.text import slugify

from connect_labs.supply_chain.navigation import SUPPLY_TABS
from connect_labs.supply_chain.operations import ID, NULLABLE_ID, _data_with, obj, register_operation
from connect_labs.supply_chain.workflow_views.models import SupplyWorkflowView

REPLACEABLE = tuple(name for name, _ in SUPPLY_TABS if name != "supply_chain:home")


def wire(view: SupplyWorkflowView) -> dict:
    return {
        "id": view.pk,
        "program_id": view.program_id,
        "slug": view.slug,
        "label": view.label,
        "workflow_definition_id": view.workflow_definition_id,
        "opportunity_id": view.opportunity_id,
        "replaces": view.replaces or None,
        "position": view.position,
    }


_PIN = _data_with(
    ("workflow_definition_id", "label"),
    workflow_definition_id=ID,
    label={"type": "string", "minLength": 1, "maxLength": 80},
    slug={"type": "string", "pattern": "^[a-z0-9-]{1,80}$"},
    opportunity_id=NULLABLE_ID,
    replaces={"enum": [*REPLACEABLE, ""]},
    position={"type": "integer"},
)


@register_operation(
    name="view_pin",
    summary=(
        "Pin a workflow into this programme's supply navigation as a tab, opened inside the supply header. "
        "`replaces` names a built-in supply tab it stands in for (e.g. supply_chain:workers); left out, it adds "
        "a tab. The workflow loads as each viewer, with its own access rules. Pinning the same slug again "
        "updates it."
    ),
    input_schema=obj({"data": _PIN}, required=("data",)),
    is_write=True,
)
def view_pin(access, data):
    program_id = access._require_program()
    slug = data.get("slug") or slugify(data["label"])[:80] or f"view-{data['workflow_definition_id']}"
    view, _ = SupplyWorkflowView.objects.update_or_create(
        program_id=program_id,
        slug=slug,
        defaults={
            "label": data["label"],
            "workflow_definition_id": data["workflow_definition_id"],
            "opportunity_id": data.get("opportunity_id") or access.opportunity_id,
            "replaces": data.get("replaces") or "",
            "position": data.get("position", 0),
            "created_by": getattr(getattr(access, "caller", None), "username", "") or "",
        },
    )
    return wire(view)


@register_operation(
    name="view_unpin",
    summary="Take a pinned workflow out of this programme's supply navigation; a tab it replaced comes back.",
    input_schema=obj({"view_id": ID}, required=("view_id",)),
    is_write=True,
)
def view_unpin(access, view_id):
    deleted, _ = SupplyWorkflowView.objects.filter(program_id=access._require_program(), pk=view_id).delete()
    if not deleted:
        raise ValueError(f"no pinned view {view_id} in this programme")
    return {"unpinned": view_id}


@register_operation(
    name="view_list",
    summary="The workflows pinned into this programme's supply navigation, in tab order.",
    input_schema=obj({}),
)
def view_list(access):
    return [wire(v) for v in SupplyWorkflowView.objects.filter(program_id=access._require_program())]
