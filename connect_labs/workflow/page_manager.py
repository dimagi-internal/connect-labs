"""A scope's pages: list them, make a new one, choose which one the scope opens on.

Served at `/labs/p/<org|programme|opportunity>/<key>/pages/` (page_views.PagesView)
and read by Settings' home picker. Before this, pages and homes were MCP-only:
`workflow_create_from_template page_blank`, `workflow_update_definition`, then
`labs_config_set` with a workflow id typed into JSON.

Where a scope's pages live:

* a programme's or opportunity's: definitions it owns;
* an organisation's: definitions it owns (a real organisation, by its Connect id)
  AND those its programmes own. A labs-only organisation has no Connect id to own a
  record with, so its pages are always kept in one of its programmes -- which is
  what its home already names (`home.fill.program_id`).

Who may change them is who may change the scope (scope_config.service.write_refusal,
Dimagi staff included), the rule Settings and "Edit page" already use. Making a page
the home goes through `service.update`, so it is attributed, versioned, undoable and
checked exactly as a Settings save is (`_require_owners`, `check_targets`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from connect_labs.labs.access.scopes import Caller
from connect_labs.scope_config import service
from connect_labs.scope_config.builtin import OWNER_KEYS, fill_owner
from connect_labs.scope_config.scopes import Scope
from connect_labs.workflow.page_mode import is_page

#: A page slug a scope's own address uses: `/labs/p/<scope>/<key>/pages/` is the manager.
RESERVED_SLUGS = frozenset({"pages"})
# Within what the address's `<slug:page>` converter accepts.
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,59}$")
VIA = "pages"


def slug_problem(slug) -> str | None:
    """Why `slug` cannot be a page's slug, or None."""
    if not isinstance(slug, str) or not SLUG.match(slug):
        return (
            "a page's address is 1-60 lowercase letters, digits, hyphens and underscores, "
            "starting with a letter or digit"
        )
    if slug in RESERVED_SLUGS:
        return f"{slug!r} is taken: /labs/p/<scope>/<key>/{slug}/ is the list of a scope's pages"
    return None


@dataclass
class Owner:
    """A scope that keeps page records, and how a fill or an address names it."""

    scope: Scope
    fill: dict  # {"program_id": 25} -- the `home.fill` owner keys
    label: str

    @property
    def token(self) -> str:
        """How a form names this owner: "program:25"."""
        return f"{self.scope.type}:{self.scope.key}"

    @property
    def query(self) -> str:
        return "&".join(f"{k}={v}" for k, v in self.fill.items())


def _caller(request) -> Caller:
    return Caller(user=request.user, request=request)


def owners_for(request, scope: Scope, tree: dict) -> list[Owner]:
    """The scopes whose page records belong to `scope`'s list, the scope itself first."""
    own = Owner(scope, {"organization_id": scope.key} if scope.type == "organization" else scope.kwargs(), "")
    own.label = "this " + scope.label(tree).split(" ", 1)[0]
    if scope.type != "organization":
        return [own]
    out = [own]
    for program in tree.get("programs") or []:
        if program.get("organization") == scope.key and str(program.get("id", "")).isdigit():
            program_scope = Scope.of("program", program["id"])
            out.append(Owner(program_scope, program_scope.kwargs(), program_scope.label(tree)))
    return out


def owner_from_token(request, scope: Scope, token: str, tree: dict) -> Owner | None:
    """The owner a form names, only if it is one of `scope`'s own (never an arbitrary scope)."""
    return next((o for o in owners_for(request, scope, tree) if o.token == token), None)


def _owns(definition, owner: Owner, context: dict) -> bool:
    if owner.scope.type == "opportunity":
        return str(getattr(definition, "opportunity_id", None)) == owner.scope.key
    if owner.scope.type == "program":
        return str(getattr(definition, "program_id", None)) == owner.scope.key and not getattr(
            definition, "opportunity_id", None
        )
    return str(getattr(definition, "organization_id", None)) == str(context.get("organization_id")) and not (
        getattr(definition, "program_id", None) or getattr(definition, "opportunity_id", None)
    )


def _access(request, context: dict):
    from connect_labs.workflow.data_access import WorkflowDataAccess

    return WorkflowDataAccess(request=request, **{k: v for k, v in context.items() if k.endswith("_id")})


def pages_of(request, owner: Owner) -> list:
    """The page definitions `owner` keeps, read as the viewer; [] where the viewer may not read them."""
    from connect_labs.workflow.page_views import _owner_context

    context = _owner_context(request, owner.fill)
    if context is None:
        return []
    access = _access(request, context)
    try:
        found = [d for d in access.list_definitions() if is_page(d) and _owns(d, owner, context)]
    finally:
        access.close()
    return sorted(found, key=lambda d: ((d.data or {}).get("name") or "").lower())


def home_of(request, scope: Scope) -> dict:
    """The home in effect at `scope`: {fill, label, own (set on this scope's own layer), source}."""
    got = service.get("labs", scope, _caller(request))
    home = got["value"].get("home") or {}
    source = got["provenance"].get("home.fill.workflow", "")
    own = bool((got["data"].get("home") or {}).get("fill"))
    return {"fill": home.get("fill"), "label": home.get("label") or "", "own": own, "source": source, "got": got}


def _is_home(home: dict, definition, owner: Owner) -> bool:
    fill = home.get("fill") or {}
    return bool(fill) and int(fill.get("workflow") or 0) == definition.id and fill_owner(fill) == owner.scope


def list_pages(request, scope: Scope) -> dict:
    """Everything the pages list shows: each page, which is home, who may change what."""
    from connect_labs.workflow.page_views import page_url

    caller = _caller(request)
    tree = service.tree_for(caller)
    home = home_of(request, scope)
    can_manage = service.write_refusal(caller, scope, tree) is None
    owners = owners_for(request, scope, tree)
    pages, writable = [], []
    for owner in owners:
        may_write = service.write_refusal(caller, owner.scope, tree) is None
        if may_write and keeps_records(request, owner):
            writable.append(owner)
        for definition in pages_of(request, owner):
            data = definition.data or {}
            slug = (data.get("page") or {}).get("slug") or ""
            pages.append(
                {
                    "id": definition.id,
                    "name": data.get("name") or f"Page {definition.id}",
                    "slug": slug,
                    "url": page_url(owner.scope, slug) if slug and slug not in RESERVED_SLUGS else "",
                    "owner": owner,
                    "is_home": _is_home(home, definition, owner),
                    "made_by": getattr(definition, "username", "") or "",
                    "edit_url": (f"/labs/workflow/{definition.id}/run/?{owner.query}&edit=true" if may_write else ""),
                }
            )
    return {
        "scope": scope,
        "scope_label": scope.label(tree),
        "pages": pages,
        "home": home,
        "home_url": page_url(scope),
        "can_manage": can_manage,
        "write_note": None if can_manage else service.write_refusal(caller, scope, tree),
        "new_page_owners": writable if can_manage else [],
    }


def _owner_context_for(request, owner: Owner):
    from connect_labs.workflow.page_views import _owner_context

    return _owner_context(request, owner.fill)


def keeps_records(request, owner: Owner) -> bool:
    """Whether a page can be kept in `owner`: a labs-only organisation has no Connect id to own one."""
    context = _owner_context_for(request, owner)
    if context is None:
        return False
    return owner.scope.type != "organization" or isinstance(context.get("organization_id"), int)


# --- changing --------------------------------------------------------------------


def _require_write(request, scope: Scope, tree: dict) -> None:
    reason = service.write_refusal(_caller(request), scope, tree)
    if reason:
        raise service.Forbidden(reason)


def create_page(request, scope: Scope, owner: Owner, name: str, slug: str) -> tuple[int, Owner]:
    """A new page made from `page_blank`, kept in `owner` under `scope`. Returns (definition id, owner)."""
    from connect_labs.workflow.templates import create_workflow_from_template

    name = (name or "").strip()
    if not name or len(name) > 120:
        raise service.Invalid("a page needs a title of at most 120 characters")
    problem = slug_problem(slug)
    if problem:
        raise service.Invalid(problem)
    tree = service.tree_for(_caller(request))
    _require_write(request, scope, tree)
    _require_write(request, owner.scope, tree)
    if not keeps_records(request, owner):
        raise service.Invalid(f"{owner.label} cannot keep pages: choose one of its programs")
    context = _owner_context_for(request, owner)
    if any(((d.data or {}).get("page") or {}).get("slug") == slug for d in pages_of(request, owner)):
        raise service.Invalid(f"{owner.label} already has a page at /{slug}/: choose another address")
    access = _access(request, context)
    try:
        definition, _, _ = create_workflow_from_template(
            access, "page_blank", request=request, program_id=context.get("program_id")
        )
        access.update_definition(definition.id, {**(definition.data or {}), "name": name, "page": {"slug": slug}})
    finally:
        access.close()
    return definition.id, owner


def set_home(request, scope: Scope, owner: Owner | None, definition_id: int | None, *, label="", version=None):
    """Make page `definition_id` (kept in `owner`) the page `scope` opens on; None clears this scope's own home.

    As the person, through `service.update`: attributed, versioned, undoable, and checked
    as a Settings save is -- the owner must be one they may use, and it must be a page.
    """
    caller = _caller(request)
    if definition_id is None:
        patch = {"home": None}
    else:
        if owner is None:
            raise service.Invalid("say which scope keeps the page")
        # A merge patch merges `fill`; clear the other owner keys so it names exactly one.
        fill = {k: None for k in OWNER_KEYS}
        fill.update({"workflow": int(definition_id), **owner.fill})
        patch = {"home": {"fill": fill, "label": (label or "")[:80] or None}}
    return service.update("labs", scope, patch, caller, expected_version=version, via=VIA)
