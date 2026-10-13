"""The screens a seeded clone opens on: its Supply tabs, its program's home page, its organisation's home.

`clone_supply.seed` fills the ledger; this makes sure a fresh environment shows it the
way the demo is meant to be seen, and is run by every seed (with or without --reset):

- the Stock review and Forecast workflows, owned by the clone's opportunity and
  FOLLOWING their deployed templates (so a deploy reaches them), filling the
  program's Supply tabs (`supply` Settings: Stock review in place of Workers, and
  Forecast added after it);
- the program's home page (`supply_program_home`, a page that follows its template),
  set as the program's `home` in Settings, so /labs/p/programme/<id>/ opens on it,
  with the canopy agent panel turned on (`config.agent.share`);
- the organisation's home, only when it has none: a `page_blank` page owned by the
  program, which lists the organisation's programs and links to each one's home.

Idempotent. Each workflow is found by its template before one is made, so a reseed
keeps the ids the Settings and anyone's bookmarks point at; a found one is put back
on its template if someone forked it. Everything runs in-process against the labs
database: the program and opportunity are labs-only, so no record leaves labs.
"""

from __future__ import annotations

from connect_labs.supply_chain.demo.clone_supply import SLUG

LABS_ONLY_TOKEN = "labs-only"  # noqa: S105 -- labs-only records never reach Connect, so no token is sent
VIA = "supply_seed_clone_supply"

REVIEW, FORECAST, HOME, BLANK = "supply_stock_review", "supply_stock_forecast", "supply_program_home", "page_blank"
# What the demo's Stock review counts a course as: the protocol's 168 sachets (clone_supply's
# commodity), not the template's generic 150, so its "children's courses" agrees with the forecast.
REVIEW_CONFIG = {"item": SLUG, "course_size": 168}

# The program's home carries the canopy agent panel, which may read the page (page_context).
HOME_CONFIG = {"agent": {"share": True}}

TABS = {
    "supply_chain:workers": {"template": REVIEW, "label": "Stock review", "slug": "stock-review", "position": 0},
    "forecast": {"template": FORECAST, "label": "Forecast", "slug": "forecast", "position": 1},
}


def _access(**scope):
    from connect_labs.workflow.data_access import WorkflowDataAccess

    return WorkflowDataAccess(access_token=LABS_ONLY_TOKEN, **scope)


def _find(access, template_key: str, *, prefer: int | None = None, **owner):
    """The definition made from `template_key` that `owner` owns, `prefer` first; None when there is none."""
    key, value = next(iter(owner.items()))
    found = [
        d for d in access.list_definitions() if d.template_type == template_key and getattr(d, key, None) == value
    ]
    for definition in found:
        if definition.id == prefer:
            return definition
    return min(found, key=lambda d: d.id) if found else None


def _ensure(access, template_key: str, *, opportunity_ids, prefer=None, config=None, name=None, **owner):
    """(definition id, "created" | "refreshed") for `template_key` under `owner`, following the template."""
    from connect_labs.workflow.render_source import render_source_of
    from connect_labs.workflow.templates import create_workflow_from_template, get_template

    definition = _find(access, template_key, prefer=prefer, **owner)
    if definition is None:
        definition, _, _ = create_workflow_from_template(
            access,
            template_key=template_key,
            opportunity_ids=list(opportunity_ids),
            program_id=owner.get("program_id"),
            render_source={"template": template_key},
            config_overrides=config or None,
        )
        if name and name != definition.name:
            access.update_definition(definition.id, {**definition.data, "name": name})
        return definition.id, "created"

    # Refresh what the template says the workflow reads, keep what is the instance's own.
    template = get_template(template_key)["definition"]
    data = dict(definition.data or {})
    for key in ("kind", "page", "supply_sources", "workflow_sources", "config_reads", "description"):
        if key in template:
            data[key] = template[key]
    data["config"] = {**(data.get("config") or {}), **(template.get("config") or {}), **(config or {})}
    data["opportunity_ids"] = list(opportunity_ids)
    if render_source_of(definition) != {"template": template_key}:
        data["render_source"] = {"template": template_key}
    if name:
        data["name"] = name
    if data != (definition.data or {}):
        access.update_definition(definition.id, data)
    return definition.id, "refreshed"


def _org_home(access, program_id: int, name: str) -> int:
    """A `page_blank` page the program owns, for its organisation's home: an existing one as it is, else a new one.

    Never refreshed: an organisation's home is the organisation's to edit, and a reseed
    must not put back the starter over what someone made of it.
    """
    from connect_labs.workflow.templates import create_workflow_from_template

    found = _find(access, BLANK, program_id=program_id)
    if found is not None:
        return found.id
    definition, _, _ = create_workflow_from_template(access, template_key=BLANK, program_id=program_id)
    access.update_definition(definition.id, {**definition.data, "name": name})
    return definition.id


def _layer(namespace: str, scope) -> dict:
    from connect_labs.scope_config.models import ScopeConfig

    row = ScopeConfig.objects.filter(scope_type=scope.type, scope_key=scope.key, namespace=namespace).first()
    return dict(row.data) if row else {}


def _set(namespace: str, scope, patch: dict) -> None:
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.scope_config import service

    service.update(namespace, scope, patch, SYSTEM, via=VIA)


def ensure_pages(*, program_id: int, opportunity_id: int) -> dict:
    """Make (or refresh) the clone's Supply tabs, its program's home page and, if unset, its organisation's home."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.labs.synthetic.org_tree import synthetic_org_name, synthetic_org_slug
    from connect_labs.scope_config.scopes import Scope
    from connect_labs.supply_chain import scopes
    from connect_labs.workflow.page_views import page_url

    scopes.require_synthetic(program_id, "set up a clone's pages")
    scopes.require_programme_opportunity(program_id, opportunity_id)
    program = Scope.of("program", program_id)
    out = {"tabs": {}}

    # ---- Supply tabs: workflows the opportunity owns, following their templates ----
    current = _layer("supply", program).get("tabs") or {}
    patch = {}
    with _access(opportunity_id=opportunity_id) as opp_access:
        for key, tab in TABS.items():
            prefer = ((current.get(key) or {}).get("fill") or {}).get("workflow")
            workflow_id, how = _ensure(
                opp_access, tab["template"], opportunity_ids=[opportunity_id], prefer=prefer,
                config=REVIEW_CONFIG if tab["template"] == REVIEW else None, opportunity_id=opportunity_id,
            )  # fmt: skip
            patch[key] = {
                "fill": {"workflow": workflow_id, "opportunity_id": opportunity_id},
                "label": tab["label"],
                "slug": tab["slug"],
                "position": tab["position"],
                "hidden": None,  # a merge patch: drop a hide someone set
            }
            out["tabs"][key] = {"workflow": workflow_id, "action": how}
    _set("supply", program, {"tabs": patch})

    # ---- the program's home: a page the program owns ----
    with _access(program_id=program_id) as program_access:
        home_id, how = _ensure(
            program_access, HOME, opportunity_ids=[opportunity_id], config=HOME_CONFIG, program_id=program_id
        )
        out["program_home"] = {"workflow": home_id, "action": how, "url": page_url(program)}
        _set(
            "labs",
            program,
            {"home": {"fill": {"workflow": home_id, "program_id": program_id}, "label": "Supply home"}},
        )

        # ---- the organisation's home, only when it has none ----
        opp = SyntheticOpportunity.objects.select_related("labs_org").filter(opportunity_id=opportunity_id).first()
        out["organization_home"] = None
        if opp is not None and not str(synthetic_org_slug(opp)).startswith("labs-synthetic-unfiled-"):
            org = Scope.of("organization", synthetic_org_slug(opp))
            existing = (_layer("labs", org).get("home") or {}).get("fill")
            if existing:
                out["organization_home"] = {
                    "workflow": existing.get("workflow"),
                    "action": "kept",
                    "url": page_url(org),
                }
            else:
                name = f"{synthetic_org_name(opp)} home"
                org_home = _org_home(program_access, program_id, name)
                _set("labs", org, {"home": {"fill": {"workflow": org_home, "program_id": program_id}, "label": name}})
                out["organization_home"] = {"workflow": org_home, "action": "created", "url": page_url(org)}
    return out
