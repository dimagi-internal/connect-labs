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

#: The data routes a render can be given. A page over a CommCare project is given none but its own
#: (``getPipelineData``), so nothing reaches ``/labs/...``; ``closed_endpoints`` is that set, shut. A test pins
#: that a Connect page's ``apiEndpoints`` is exactly these plus the run ones, so a route added there cannot be
#: missed here (workflow/tests/test_page_mode.py).
DATA_ENDPOINTS = (
    "getWorkers",
    "getPipelineData",
    "queryPipelineRows",
    "getSupplyData",
    "querySupply",
    "streamPipelineData",
    "authStatus",
    "updateOpportunityIds",
    "getWorkflowData",
    "queryWorkflow",
)


def closed_endpoints(**open_: str) -> dict:
    """Every endpoint a render may be given, all absent (``None``) except those named in ``open_``."""
    endpoints = dict.fromkeys((*DATA_ENDPOINTS, *RUN_ENDPOINTS))
    unknown = sorted(set(open_) - set(endpoints))
    if unknown:
        raise KeyError(f"not render endpoints: {unknown}")
    return {**endpoints, **open_}


#: The template a page starts from. A definition made from it before ``kind`` was
#: written onto every page carries the template type alone; it is still a page.
PAGE_TEMPLATE = "page_blank"


def is_page(definition) -> bool:
    """Whether a definition is a page. Fails toward "page" (and so toward the sandbox):
    ``kind: "page"``, or made from the page template, whichever ``kind`` says."""
    data = getattr(definition, "data", definition) or {}
    if not isinstance(data, dict):
        return False
    if data.get("kind") == PAGE:
        return True
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    return PAGE_TEMPLATE in (data.get("templateType"), config.get("templateType"))


_OWNER_WORDS = {"organization": "organisation", "program": "program", "opportunity": "opportunity"}

#: What someone who may not edit a page is told (never the scope check's own wording).
PAGE_EDIT_REFUSED = "You can view this page but not edit it: only people who can change its {owner} can."


def page_owner_scope(definition, tree: dict, owner: dict | None = None) -> Scope | None:
    """The scope a page's record lives in -- what decides who may edit it.

    `owner` is the page view's own (an organisation by slug, or a programme or
    opportunity id); without it, the record's own scope fields. An organisation
    record carries a Connect id, named here by its slug from the viewer's tree:
    one the viewer's tree does not hold is not one they can be shown to own.
    """
    if owner:
        opp, program, org = (owner.get(k) for k in ("opportunity_id", "program_id", "organization_id"))
    else:
        opp, program, org = (getattr(definition, k, None) for k in ("opportunity_id", "program_id", "organization_id"))
    try:
        if opp:
            return Scope.of("opportunity", opp)
        if program:
            return Scope.of("program", program)
        if org not in (None, ""):
            if str(org).isdigit():
                org = (organization_entry(tree, org) or {}).get("slug")
            return Scope.of("organization", org) if org else None
    except ValueError:
        return None
    return None


def page_edit_refusal(request, definition, owner: dict | None = None) -> str | None:
    """Why the viewer may not edit page `definition`, or None if they may.

    The rule is the page's owning scope's WRITE rule (scope_config.service.write_refusal),
    the one Settings uses for that scope: a member of the organisation, programme or
    opportunity the page lives in edits it -- as they may write any other record
    there -- and Dimagi staff: hand-granted `is_staff` on any page, a Dimagi sign-in
    on a labs-only one. It is no longer "staff or whoever made
    it": the page sandbox (workflow/sandbox.py) means a page's code cannot act as the
    person viewing it, so letting a scope's members edit their own page hands no one
    another viewer's access.
    """
    from connect_labs.labs.access.scopes import Caller
    from connect_labs.scope_config import service

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return "sign in to edit a page"
    caller = Caller(user=user, request=request)
    tree = service.tree_for(caller)
    scope = page_owner_scope(definition, tree, owner)
    if scope is None:
        # Its owner cannot be told, so neither can whether it is labs-only: hand-granted staff only.
        return None if getattr(user, "is_staff", False) else "this page's owner cannot be told"
    if service.write_refusal(caller, scope, tree) is None:
        return None
    return PAGE_EDIT_REFUSED.format(owner=_OWNER_WORDS[scope.type])


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


def run_instance(run, definition_id, *, opportunity_ids, opportunity_name=None) -> dict:
    """The ``instance`` prop for a saved run: what a render reads of a run (state, window, and its snapshot once
    completed). One shape for a Connect run page and a CommCare project's saved run."""
    return {
        "id": run.id,
        "definition_id": definition_id,
        # The RUN RECORD's own ownership, not whatever the session holds: a program-owned run has no opportunity.
        "opportunity_id": run.opportunity_id,
        # Program-owned runs have no owning opp; the render reads instance.program_id to dispatch program jobs.
        "program_id": run.program_id,
        "opportunity_ids": opportunity_ids,
        "opportunity_name": opportunity_name,
        # User-given display name ("" if never renamed; render code falls back to "Run #<id>").
        "name": run.name,
        # Canonical lifecycle: in_progress | completed.
        "status": run.status,
        "state": run.state,
        "period_start": run.period_start,
        "period_end": run.period_end,
        "completed_at": run.completed_at,
        # Null while in_progress; populated on completion. The useRunView helper reads it when completed, so
        # render code never recomputes against live data on a finalized run.
        "snapshot": run.snapshot,
    }


def scope_prop(scope_type, key, user, *, settings_url=None) -> dict:
    """The ``scope`` prop with nothing in it but who is looking: the shape every render receives, whatever owns it."""
    return {
        "type": scope_type,
        "key": key,
        "organization": None,
        "program": None,
        "opportunity": None,
        "user": {"username": getattr(user, "username", ""), "name": getattr(user, "name", "") or ""},
        "programs": [],
        "opportunities": [],
        "settings_url": settings_url,
    }


def _program(entry: dict | None) -> dict | None:
    if not entry:
        return None
    pid = entry.get("id")
    return {
        "id": pid,
        "name": entry.get("name") or f"Program {pid}",
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
    out = scope_prop(
        scope.type if scope else None,
        scope.key if scope else None,
        user,
        settings_url=f"/labs/settings/{scope.type}/{scope.key}/" if scope and scope.type != "user" else None,
    )
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
