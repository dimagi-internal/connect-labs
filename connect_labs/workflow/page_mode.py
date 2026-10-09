"""Pages: workflows with no runs, and the `scope` and `config` every render receives.

A page is a workflow definition whose data says `kind: "page"`. It renders in the
same runner as any workflow (DynamicWorkflow, the shared window.Labs* primitives,
declared sources read as the viewer) in **page mode**: no run is created or read,
`instance` is a blank in-memory one, and the run-only endpoints (state, rename,
complete, snapshot, worker results, actions) are absent. Design:
docs/superpowers/specs/2026-10-09-labs-scope-config-design.md, section 4.

"Safe" here means what it means for workflows: render code is written by people who
can write the definition, a crash is contained by the runner's error boundary, and
every byte of data comes through an endpoint keyed by the definition that reads only
what the definition declares, as the viewer. A page adds reach, not power.

Two props every render gets (page or workflow):

* ``scope`` -- the organisation / programme / opportunity in view, with names, plus
  the programmes and opportunities under it the viewer can see, each with the
  addresses a page links to. From the viewer's own org data, so a page never lists
  a scope the viewer is not in.
* ``config`` -- the resolved Settings for each namespace the definition names in
  ``config_reads`` (scope_config/), for the scope in view.
"""

from __future__ import annotations

from datetime import date

from connect_labs.scope_config.scopes import (
    Scope,
    opportunity_entry,
    organization_entry,
    owner_org,
    program_entry,
)

PAGE = "page"

# Run-only endpoints a page never has: it has no run.
RUN_ENDPOINTS = ("updateState", "saveWorkerResult", "renameRun", "completeRun", "getSnapshot")


def is_page(definition) -> bool:
    data = getattr(definition, "data", definition) or {}
    return isinstance(data, dict) and data.get("kind") == PAGE


def blank_instance(definition_id, opportunity_id, program_id, opportunity_ids) -> dict:
    today = date.today().isoformat()
    return {
        # No run: id 0, as edit mode's temporary instance is. Nothing persists it.
        "id": 0,
        "definition_id": definition_id,
        "opportunity_id": opportunity_id,
        "program_id": program_id,
        "opportunity_ids": list(opportunity_ids or []),
        "name": "",
        "status": "in_progress",
        "state": {},
        "period_start": today,
        "period_end": today,
    }


def _program(entry: dict | None) -> dict | None:
    if not entry:
        return None
    pid = entry.get("id")
    return {
        "id": pid,
        "name": entry.get("name") or f"Programme {pid}",
        "organization": entry.get("organization"),
        "supply_url": f"/supply/?program_id={pid}",
        "workflows_url": f"/labs/workflow/?program_id={pid}",
        "page_url": f"/labs/p/programme/{pid}/",
    }


def _opportunity(entry: dict | None) -> dict | None:
    if not entry:
        return None
    oid = entry.get("id")
    return {
        "id": oid,
        "name": entry.get("name") or f"Opportunity {oid}",
        "program": entry.get("program"),
        "organization": entry.get("organization"),
        "visit_count": entry.get("visit_count"),
        "workflows_url": f"/labs/workflow/?opportunity_id={oid}",
        "page_url": f"/labs/p/opportunity/{oid}/",
    }


def _organization(entry: dict | None, slug: str | None = None) -> dict | None:
    slug = (entry or {}).get("slug") or slug
    if not slug:
        return None
    return {"slug": slug, "name": (entry or {}).get("name") or slug, "page_url": f"/labs/p/org/{slug}/"}


def page_scope(request, scope: Scope | None) -> dict:
    """The `scope` prop for `scope`, from the viewer's own organisations, programmes and opportunities."""
    from connect_labs.labs.context import get_org_data

    tree = get_org_data(request) or {}
    user = getattr(request, "user", None)
    out = {
        "type": scope.type if scope else None,
        "key": scope.key if scope else None,
        "organization": None,
        "program": None,
        "opportunity": None,
        "user": {"username": getattr(user, "username", ""), "name": getattr(user, "name", "") or ""},
        "programs": [],
        "opportunities": [],
        "settings_url": f"/labs/settings/{scope.type}/{scope.key}/" if scope and scope.type != "user" else None,
    }
    if scope is None:
        return out
    org_slug = owner_org(tree, scope)
    out["organization"] = _organization(organization_entry(tree, org_slug) if org_slug else None, org_slug)
    programs = tree.get("programs") or []
    opportunities = tree.get("opportunities") or []
    if scope.type == "organization":
        mine = [p for p in programs if p.get("organization") == scope.key]
        ids = {str(p.get("id")) for p in mine}
        out["programs"] = [_program(p) for p in mine]
        # Its programmes' opportunities, and any it delivers outside a programme.
        out["opportunities"] = [
            _opportunity(o)
            for o in opportunities
            if str(o.get("program")) in ids or (not o.get("program") and o.get("organization") == scope.key)
        ]
    elif scope.type == "program":
        out["program"] = _program(program_entry(tree, scope.key) or {"id": scope.id})
        out["opportunities"] = [_opportunity(o) for o in opportunities if str(o.get("program")) == scope.key]
    elif scope.type == "opportunity":
        opp = opportunity_entry(tree, scope.key) or {"id": scope.id}
        out["opportunity"] = _opportunity(opp)
        if opp.get("program"):
            out["program"] = _program(program_entry(tree, opp["program"]) or {"id": opp["program"]})
    return out


def page_config(request, definition, scope: Scope | None) -> dict:
    """The `config` prop: each namespace in `config_reads`, resolved for `scope`."""
    from connect_labs.scope_config import service
    from connect_labs.scope_config.namespaces import get_namespace

    data = getattr(definition, "data", definition) or {}
    out = {}
    for key in data.get("config_reads") or []:
        try:
            get_namespace(key)
        except KeyError:
            continue
        out[key] = service.resolved_for_request(request, key, scope) if scope else get_namespace(key).defaults
    return out
