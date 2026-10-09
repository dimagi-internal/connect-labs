"""Supply's settings: which tabs a programme shows, and what fills them.

The `supply` namespace (scope_config/) holds one map, `tabs`. A key is either a
built-in tab's view name ("supply_chain:workers") or a slug for a tab the
programme adds:

    {"tabs": {
        "supply_chain:workers": {"fill": {"workflow": 8481, "opportunity_id": 10113},
                                 "label": "Stock review", "slug": "stock-review"},
        "forecast":             {"fill": {"workflow": 8495, "opportunity_id": 10113},
                                 "label": "Forecast", "after": "supply_chain:flow"},
        "supply_chain:alerts":  {"hidden": true}}}

A `fill` makes the tab a workflow (or a page, which is a workflow with no runs),
opened inside the supply header at /supply/views/<slug>/. Set by an
organisation for every programme it owns, or by a programme; never by a person
(decision 4). Overview cannot be hidden: it is where "pick a programme" and
"this tab is turned off" send you.

Replaces `SupplyWorkflowView` as where pins live (migration
scope_config/0002); `Pin` keeps that model's attribute names, so everything
that read a pin reads one of these unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.text import slugify

from connect_labs.scope_config.namespaces import Namespace, register_namespace

HOME = "supply_chain:home"
# Pins that ADD a tab sat with the stock pages before there was config; that
# stays the default place for one.
DEFAULT_AFTER = "supply_chain:flow"


def _builtin_names() -> tuple[str, ...]:
    from connect_labs.supply_chain.navigation import SUPPLY_TABS

    return tuple(name for name, _ in SUPPLY_TABS)


TAB = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "minLength": 1, "maxLength": 80},
        "slug": {"type": "string", "pattern": "^[a-z0-9-]{1,80}$"},
        "fill": {
            "type": "object",
            "properties": {
                "workflow": {"type": "integer", "minimum": 1},
                "opportunity_id": {"type": ["integer", "null"]},
            },
            "required": ["workflow"],
            "additionalProperties": False,
        },
        "hidden": {"type": "boolean"},
        "after": {"type": "string"},
        "position": {"type": "integer"},
        # Who filled the tab: offered the workflow's editor on it (workflow_views.builds_the_view).
        "pinned_by": {"type": "string", "maxLength": 150},
    },
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {"tabs": {"type": "object", "additionalProperties": TAB}},
    "additionalProperties": False,
}


def _check_layer(data: dict) -> None:
    """What one layer may say on its own: keys, and Overview left alone."""
    builtins = set(_builtin_names())
    for key, tab in (data.get("tabs") or {}).items():
        if key == HOME and tab.get("hidden"):
            raise ValueError("Overview cannot be hidden: it is where Supply sends you to pick a programme")
        if key == HOME and tab.get("fill"):
            raise ValueError("Overview cannot be replaced by a workflow")
        if key not in builtins and (not all(c.isalnum() or c == "-" for c in key) or key != key.lower()):
            raise ValueError(f"tabs.{key}: an added tab's key is a lower-case slug")


def _check_resolved(value: dict) -> None:
    """The tabs in EFFECT, organisation and programme together.

    Checked on the resolved value, not one layer: a programme hiding or relabelling a
    tab its organisation added says only {"hidden": true} -- the fill is the
    organisation's -- and two layers can each pick a slug the other already uses.
    """
    builtins = set(_builtin_names())
    slugs: dict[str, str] = {}
    for key, tab in (value.get("tabs") or {}).items():
        if key not in builtins and not tab.get("fill"):
            raise ValueError(f"tabs.{key}: a tab Supply does not have needs a fill (a workflow) to show")
        if tab.get("fill"):
            slug = slug_of(key, tab)
            if slug in slugs:
                raise ValueError(f"tabs.{key}: slug {slug!r} is already used by tabs.{slugs[slug]}")
            slugs[slug] = key


def _owners(data: dict) -> list:
    """The opportunities this layer's tabs read their workflows in."""
    from connect_labs.scope_config.scopes import Scope

    return [
        Scope.of("opportunity", tab["fill"]["opportunity_id"])
        for tab in (data.get("tabs") or {}).values()
        if (tab.get("fill") or {}).get("opportunity_id")
    ]


SUPPLY = register_namespace(
    Namespace(
        key="supply",
        label="Supply",
        description="Which tabs Supply shows, and which workflow or page fills a tab.",
        schema=SCHEMA,
        defaults={"tabs": {}},
        layers=frozenset({"organization", "program"}),
        check=_check_layer,
        check_resolved=_check_resolved,
        owners=_owners,
    )
)


@dataclass(frozen=True)
class Pin:
    """A tab filled by a workflow -- `SupplyWorkflowView`'s attribute names, from config."""

    program_id: int
    slug: str
    label: str
    workflow_definition_id: int
    opportunity_id: int | None
    replaces: str
    position: int
    after: str
    created_by: str = ""


def slug_of(key: str, tab: dict) -> str:
    if tab.get("slug"):
        return tab["slug"]
    if ":" not in key:
        return key
    return slugify(tab.get("label") or key.split(":", 1)[1])[:80] or "view"


def pins_from(tabs: dict, program_id: int) -> list[Pin]:
    builtins = set(_builtin_names())
    pins = []
    for key, tab in (tabs or {}).items():
        fill = tab.get("fill")
        if not fill or tab.get("hidden"):
            continue
        replaces = key if key in builtins else ""
        builtin_label = dict(_builtin_tabs()).get(key, "")
        pins.append(
            Pin(
                program_id=int(program_id),
                slug=slug_of(key, tab),
                label=tab.get("label") or builtin_label or key,
                workflow_definition_id=int(fill["workflow"]),
                opportunity_id=fill.get("opportunity_id"),
                replaces=replaces,
                position=int(tab.get("position") or 0),
                after="" if replaces else (tab.get("after") or DEFAULT_AFTER),
                created_by=tab.get("pinned_by") or "",
            )
        )
    return sorted(pins, key=lambda p: (p.position, p.slug))


def _builtin_tabs():
    from connect_labs.supply_chain.navigation import SUPPLY_TABS

    return SUPPLY_TABS


def hidden_from(tabs: dict) -> set[str]:
    return {key for key, tab in (tabs or {}).items() if tab.get("hidden") and key != HOME}


def tabs_for(request, program_id) -> dict:
    """The programme's resolved `tabs` (organisation layer, then programme)."""
    from connect_labs.scope_config import service
    from connect_labs.scope_config.scopes import Scope

    if not program_id:
        return {}
    try:
        scope = Scope.of("program", int(program_id))
    except (TypeError, ValueError):
        return {}
    return service.resolved_for_request(request, SUPPLY.key, scope).get("tabs") or {}
