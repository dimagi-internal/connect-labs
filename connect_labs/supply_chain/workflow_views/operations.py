"""Pin a workflow into a programme's supply navigation, unpin it, list the pins.

Pins live in the programme's Settings now (`supply` namespace, supply_chain/config.py),
so these operations are wrappers over that: `labs_config_set` on the same namespace
does the same and more (hide a built-in tab, fill a tab for every programme an
organisation owns). Kept so existing callers and the supply MCP tools keep working.
"""

from django.utils.text import slugify

from connect_labs.labs.access.scopes import Caller
from connect_labs.supply_chain.config import SUPPLY, pins_from, slug_of
from connect_labs.supply_chain.navigation import SUPPLY_TABS
from connect_labs.supply_chain.operations import ID, NULLABLE_ID, _data_with, obj, register_operation

REPLACEABLE = tuple(name for name, _ in SUPPLY_TABS if name != "supply_chain:home")


def wire(pin) -> dict:
    return {
        # Pins have no row of their own any more: the slug names one.
        "id": None,
        "program_id": pin.program_id,
        "slug": pin.slug,
        "label": pin.label,
        "workflow_definition_id": pin.workflow_definition_id,
        "opportunity_id": pin.opportunity_id,
        "replaces": pin.replaces or None,
        "position": pin.position,
    }


def _scope(access):
    from connect_labs.scope_config.scopes import Scope

    return Scope.of("program", access._require_program())


def _caller(access):
    # The operation has already authorised the programme; the config write checks
    # it again as the same caller, and attributes the change to them. A data access
    # with no explicit caller is a person's request: that person.
    caller = getattr(access, "caller", None)
    if caller is not None:
        return caller
    return Caller(user=getattr(access, "user", None), request=getattr(access, "request", None))


def _username(access) -> str:
    user = getattr(getattr(access, "caller", None), "user", None) or getattr(access, "user", None)
    return getattr(user, "username", "") or ""


def _program_tabs(access) -> dict:
    from connect_labs.scope_config.models import ScopeConfig

    row = ScopeConfig.objects.filter(
        scope_type="program", scope_key=str(access._require_program()), namespace=SUPPLY.key
    ).first()
    return dict((row.data if row else {}).get("tabs") or {})


def _set_tabs(access, patch: dict, via: str):
    from connect_labs.scope_config import service

    try:
        service.update(SUPPLY.key, _scope(access), {"tabs": patch}, _caller(access), via=via)
    except service.ConfigError as exc:
        raise ValueError(str(exc)) from None


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
        "updates it. Stored in the programme's Settings (labs_config_get namespace 'supply')."
    ),
    input_schema=obj({"data": _PIN}, required=("data",)),
    is_write=True,
)
def view_pin(access, data):
    program_id = access._require_program()
    slug = data.get("slug") or slugify(data["label"])[:80] or f"view-{data['workflow_definition_id']}"
    key = data.get("replaces") or slug
    tab = {
        "label": data["label"],
        "slug": slug,
        "fill": {
            "workflow": data["workflow_definition_id"],
            "opportunity_id": data.get("opportunity_id") or access.opportunity_id,
        },
        "position": data.get("position", 0),
        "pinned_by": _username(access),
    }
    patch = {key: tab}
    # The same slug under another key (re-pinned to replace a different tab) moves.
    for other, existing in _program_tabs(access).items():
        if other != key and existing.get("fill") and slug_of(other, existing) == slug:
            patch[other] = None
    _set_tabs(access, patch, via="operation:view_pin")
    pin = next(p for p in pins_from({key: tab}, program_id))
    return wire(pin)


@register_operation(
    name="view_unpin",
    summary=(
        "Take a pinned workflow out of this programme's supply navigation, by its slug; "
        "a tab it replaced comes back."
    ),
    input_schema=obj({"slug": {"type": "string", "pattern": "^[a-z0-9-]{1,80}$"}, "view_id": ID}),
    is_write=True,
)
def view_unpin(access, slug=None, view_id=None):
    if not slug and view_id is not None:
        # A pin listed before pins moved to Settings: its old row names its slug.
        from connect_labs.supply_chain.workflow_views.models import SupplyWorkflowView

        row = SupplyWorkflowView.objects.filter(program_id=access._require_program(), pk=view_id).first()
        slug = row.slug if row else None
    if not slug:
        raise ValueError("name the pin to take out by its slug (view_list shows them)")
    tabs = _program_tabs(access)
    keys = [k for k, tab in tabs.items() if tab.get("fill") and slug_of(k, tab) == slug]
    if not keys:
        raise ValueError(f"no pinned view {slug!r} in this programme")
    _set_tabs(access, {k: None for k in keys}, via="operation:view_unpin")
    return {"unpinned": slug}


@register_operation(
    name="view_list",
    summary="The workflows pinned into this programme's supply navigation, in tab order.",
    input_schema=obj({}),
)
def view_list(access):
    from connect_labs.scope_config import service

    program_id = access._require_program()
    tabs = service.get(SUPPLY.key, _scope(access), _caller(access))["value"].get("tabs") or {}
    return [wire(p) for p in pins_from(tabs, program_id)]
