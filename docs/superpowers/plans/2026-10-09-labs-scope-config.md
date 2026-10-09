# Labs scope config and pages: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One place for how an organisation, programme, opportunity or person has set labs up (Settings), Supply's tabs as slots in it, and pages as workflows with no runs, so an organisation's landing page is a page anyone can write. Then retire the old `pages/` app.

**Architecture:** A new `connect_labs.scope_config` Django app holds `ScopeConfig` and `ScopeConfigChange` in the labs DB. A resolver layers code defaults ← organisation ← programme ← opportunity ← user, and records provenance. Namespaces (`supply`, `labs`) register a schema and the layers that may set them. Supply's header reads its tabs from the `supply` namespace instead of `SupplyWorkflowView`. The workflow runner gains a **page mode**: a definition whose `kind` is `"page"` renders with no run, plus `scope` and `config` props and a new `workflow_sources` data source. `/labs/p/<scope>/<key>/[<slug>/]` serves pages. The `labs` namespace's `home` slot is what an organisation coming in lands on.

**Tech Stack:** Django 5 (labs DB, JSONField), the existing workflow runner (React 18, Babel standalone, `components/workflow/*`, `connect_labs/static/js/workflow-runner.tsx`), the FastMCP tool registry (`connect_labs/mcp/tool_registry.py`), pytest with `--reuse-db`.

**Spec:** `docs/superpowers/specs/2026-10-09-labs-scope-config-design.md`

## Global Constraints

- The repo is PUBLIC: no secrets, emails, PII or share tokens in code, tests, fixtures or PR bodies.
- Config is labs-DB data, keyed `(scope_type, scope_key, namespace)`; `scope_key` is the org **slug**, or the id as text.
- Layer order is `defaults ← organization ← program ← opportunity ← user`; maps merge by key, lists replace whole, `null` in a patch removes a key (RFC 7386 merge patch).
- An opportunity's organisation layer is the **programme owner's** organisation (decision A). A programme's is its owning organisation.
- Write: members of the scope (`labs/access/scopes.may_use`) and Dimagi staff (`user.is_staff`); a user layer only by that user. Read: members. "Could not establish" refuses with `scopes.UNKNOWABLE`.
- Every write appends a `ScopeConfigChange` with before/after, actor and `via`; undo is a new change.
- UI name: **Settings**.
- Pages are workflow definitions with `kind: "page"`; nothing about pages is stored in `ScopeConfig` except slot fills.
- Supply's Overview tab can never be hidden. Supply tabs cannot be set at the user layer (decision 4).
- `/supply/views/stock-review/` and `/supply/views/forecast/` on programme 10112 keep working through the migration.
- Labs formats with black (line length 119); run `make test` and pre-commit before each merge.

## Review Focus

1. **A programme with config at no layer** must render Supply exactly as before (13 built-in tabs, no pins) -- pinned by a test in Task 3.
2. **An opportunity in another organisation's programme**: the organisation layer is the programme owner's, never the delivering org's -- Task 1 resolver test.
3. **A pin whose workflow the viewer cannot read** still shows the tab; opening it shows the runner's own "couldn't be loaded" message, never a 500 -- Task 5 test with a failing definition fetch.
4. **A stale `expected_version`** (two editors) is refused with a conflict, and nothing is written -- Task 1 test.
5. **A page opened at a scope the viewer is not in** is refused before any definition is read -- Task 6 test.

---

## File structure

```
connect_labs/scope_config/            NEW app (labs DB)
  __init__.py  apps.py
  models.py           ScopeConfig, ScopeConfigChange
  migrations/0001_initial.py
  migrations/0002_supply_pins.py      data migration: SupplyWorkflowView -> supply namespace
  scopes.py           Scope, chain_for(), org/programme lookups from org_data
  namespaces.py       Namespace registry + validate_layer()
  merge.py            merge_patch(), resolve_layers()
  service.py          get(), set(), history(), undo(), resolved_for_request()
  builtin.py          registers the `labs` namespace (home slot)
  views.py            Settings page (+ history/undo POSTs)
  urls.py
  templates/scope_config/settings.html
  tests/...
connect_labs/mcp/tools/scope_config.py   labs_config_get/_set/_history/_undo
connect_labs/supply_chain/config.py      registers the `supply` namespace; tabs_for(request)
connect_labs/supply_chain/navigation.py  _pinned() reads config; hidden tabs
connect_labs/supply_chain/hidden_tabs.py middleware: a hidden tab's page explains itself
connect_labs/supply_chain/workflow_views/operations.py  view_pin/_unpin/_list write config
connect_labs/workflow/page_mode.py       page_scope(), page_config(), is_page()
connect_labs/workflow/workflow_sources.py  the `workflows` prop / queryWorkflow
connect_labs/workflow/page_views.py      /labs/p/... pages, home, old-slug redirect
connect_labs/workflow/templates/page_blank.py  seed template, kind "page"
components/workflow/types.ts, DynamicWorkflow.tsx, static/js/workflow-runner.tsx  scope/config/workflows props
```

---

### Task 1: The config core

**Files:**
- Create: `connect_labs/scope_config/{__init__,apps,models,scopes,namespaces,merge,service}.py`, `migrations/0001_initial.py`
- Modify: `config/settings/base.py` (LOCAL_APPS)
- Test: `connect_labs/scope_config/tests/test_merge.py`, `test_scopes.py`, `test_service.py`

**Interfaces (produced):**
- `Scope(type: Literal["organization","program","opportunity","user"], key: str)`; `Scope.of(type, key)` normalises ints to str; `.kwargs()` → `{"organization_id": slug}` / `{"program_id": int}` / ...
- `chain_for(scope: Scope, tree: dict, username: str | None) -> list[Scope]` — owner chain, org first, the scope last.
- `merge_patch(target: dict, patch: dict) -> dict`; `resolve_layers(defaults: dict, layers: list[tuple[Scope, dict]]) -> tuple[dict, dict]` (value, provenance keyed by dotted path of leaf → scope label).
- `register_namespace(Namespace(key, label, description, defaults, schema, layers: frozenset[str]))`, `get_namespace(key)`, `all_namespaces()`.
- `service.get(namespace, scope, caller) -> dict`; `service.set(namespace, scope, patch, caller, *, expected_version=None, via="mcp") -> dict`; `service.history(namespace, scope, caller) -> list[dict]`; `service.undo(namespace, scope, caller, *, change_id=None, via) -> dict`; `service.resolved_for_request(request, namespace, scope=None) -> dict`.
- Errors: `ConfigError`, `Forbidden(ConfigError)`, `VersionConflict(ConfigError)`, `Invalid(ConfigError)`.

- [ ] **Step 1: Failing tests** (merge semantics, chain, permissions, versions, history/undo)

```python
def test_merge_patch_maps_merge_lists_replace_null_removes():
    assert merge_patch({"a": {"x": 1, "y": 2}, "l": [1]}, {"a": {"y": None, "z": 3}, "l": [2]}) == {"a": {"x": 1, "z": 3}, "l": [2]}

def test_an_opportunity_in_another_orgs_programme_takes_the_owners_org():
    tree = {"programs": [{"id": 7, "organization": "owner"}], "opportunities": [{"id": 70, "program": 7, "organization": "llo"}]}
    assert [s.key for s in chain_for(Scope.of("opportunity", 70), tree, None)] == ["owner", "7", "70"]

def test_a_stale_version_is_refused_and_nothing_written(member):
    service.set("t", Scope.of("program", 7), {"a": 1}, member)
    with pytest.raises(VersionConflict):
        service.set("t", Scope.of("program", 7), {"a": 2}, member, expected_version=0)
    assert ScopeConfig.objects.get().data == {"a": 1}

def test_undo_restores_before_as_a_new_change(member): ...
def test_a_non_member_neither_reads_nor_writes(stranger): ...
def test_staff_writes_a_programme_they_are_not_in(staff): ...
def test_a_layer_the_namespace_forbids_is_refused(member): ...
def test_unknowable_holdings_refuse_with_their_own_message(...): ...
```

- [ ] **Step 2:** Run `make test ARGS="connect_labs/scope_config -q"` — FAIL (module missing).
- [ ] **Step 3:** Implement models, migration, merge, scopes, namespaces, service. `set` runs in `transaction.atomic()` with `select_for_update`, validates the merged layer with `jsonschema` against the namespace schema, bumps `version`, appends a change.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit.

### Task 2: `labs_config_*` MCP tools

**Files:** Create `connect_labs/mcp/tools/scope_config.py`; modify `connect_labs/mcp/tools/__init__.py` (import), `connect_labs/mcp/token_scopes.py` (`RESTRICTED_TOOLS` gets get/history; `DEFINITION_WRITE_TOOLS` gets set/undo); test `connect_labs/mcp/tests/test_scope_config_tools.py`.

**Interfaces:** `labs_config_get(namespace, scope_type, scope_key)`, `labs_config_set(namespace, scope_type, scope_key, patch, expected_version)`, `labs_config_history(...)`, `labs_config_undo(..., change_id?)`, plus `labs_config_namespaces()`. Caller: `Caller(user=user, access_token=require_connect_token(user))`.

- [ ] Failing test: round-trip set → get shows provenance "this programme"; stale version → `VERSION_CONFLICT`; a forbidden scope → `FORBIDDEN`.
- [ ] Implement; map `Forbidden`→`FORBIDDEN`, `VersionConflict`→`VERSION_CONFLICT`, `Invalid`→`INVALID_SCHEMA`.
- [ ] Tests pass; the restricted-endpoint parity test still passes; commit.

### Task 3: The `supply` namespace and Supply tabs as slots

**Files:** Create `connect_labs/supply_chain/config.py`, `connect_labs/supply_chain/hidden_tabs.py`, `connect_labs/scope_config/migrations/0002_supply_pins.py`; modify `navigation.py` (`_pinned`, `supply_tabs`), `workflow_views/views.py` (pin by slug from config), `workflow_views/operations.py` (wrappers), `config/settings/base.py` (middleware); tests `connect_labs/supply_chain/tests/test_supply_slots.py`, update `test_workflow_views.py`.

**Interfaces:**
- Namespace `supply`: `{"tabs": {<tab key>: {"label"?, "slug"?, "fill"?: {"workflow": int, "opportunity_id"?: int}, "hidden"?: bool, "after"?: str, "position"?: int}}}`; layers `{"organization", "program"}`. A tab key is a built-in view name (`supply_chain:workers`) or a slug for an added tab.
- `Pin` (frozen dataclass): `program_id, slug, label, workflow_definition_id, opportunity_id, replaces, position, after` — same attribute names as `SupplyWorkflowView`, so every reader (`pinned_replacement`, `as_of`) is unchanged.
- `supply_config.tabs_for(request) -> dict` and `hidden_tabs(request) -> set[str]`.

- [ ] Failing tests: no config → today's 13 tabs; a `fill` replacing workers → "Stock review" in its place with `?program_id=`; an added fill sits after "Where it went"; hidden Alerts → gone from the nav and `/supply/alerts/` says "turned off in Settings" with a link for editors; Overview cannot be hidden (`Invalid`); the migration turns two pins into the same tabs.
- [ ] Implement. `view_pin` writes `tabs.<replaces or slug>`; `view_unpin` takes `slug` (or a legacy `view_id`); `view_list` reads config.
- [ ] Tests pass (whole supply suite); commit; PR; merge; **deploy; verify 10112's Stock review and Forecast tabs live.**

### Task 4: The Settings page

**Files:** Create `connect_labs/scope_config/{views,urls}.py`, `templates/scope_config/settings.html`; modify `connect_labs/labs/urls.py` (`settings/`), supply nav template (a **Settings** link for editors); test `connect_labs/scope_config/tests/test_views.py`.

- [ ] Failing tests: a member sees each namespace with provenance; a non-member gets 404; saving this layer's JSON with a stale version shows the conflict; undo restores; a reader sees no edit form; the supply section toggles a tab's visibility.
- [ ] Implement; tests pass; commit.

### Task 5: Page mode in the runner

**Files:** Create `connect_labs/workflow/page_mode.py`; modify `workflow/views.py` (`WorkflowRunView._run_context_data` page branch), `workflow/data_access.py` (`create_definition` keeps `kind`, `page`, `workflow_sources`, `config_reads`), `workflow/templates/__init__.py` (template `kind`), `supply_chain/workflow_views/views.py` (page fill: no run), `components/workflow/types.ts`, `DynamicWorkflow.tsx`, `static/js/workflow-runner.tsx` (`scope`, `config`, `workflows` props); the workflow list and `workflow_run_default` skip page-kind definitions; tests `connect_labs/workflow/tests/test_page_mode.py`.

**Interfaces:** `is_page(definition) -> bool`; `page_scope(request, scope: Scope) -> dict` (`{organization, program, opportunity, user, programs, opportunities}` with names); `page_config(request, definition, scope) -> dict`.

- [ ] Failing tests: a page-kind definition renders with `instance.id == 0`, no run created, `updateState`/`completeRun`/`renameRun`/`getSnapshot` all `None`, `is_page` true, `scope` matches the context; a supply tab filled by a page creates no run; page-kind definitions are absent from `workflow_run_default`.
- [ ] Implement; JS type-check (`npx tsc --noEmit`) and `npm run build` locally; tests pass; commit.

### Task 6: Page data, addresses and the authoring path

**Files:** Create `connect_labs/workflow/workflow_sources.py`, `connect_labs/workflow/page_views.py`, `connect_labs/workflow/templates/page_blank.py`; modify `workflow/urls.py` (`api/<id>/workflow-data/`, `api/<id>/workflow-query/`), `labs/urls.py` (`p/` → page views, after the old app is removed in Task 8; until then mount the new routes under `p/org|programme|opportunity/`), `mcp/tools/workflows.py` (`workflow_list(kind=)`, `workflow_create_from_template(organization_id=)`), `.claude/skills/workflow-author/SKILL.md`; tests `test_workflow_sources.py`, `test_page_views.py`.

**Interfaces:** `workflow_sources` declaration `{alias, workflow: int, read: "latest_run"|"saved_runs"|"summary", opportunity_id?|program_id?|organization_id?}`; `read_workflow_source(request, source) -> dict`; endpoint returns `{workflows: {alias: {...} | {error}}}`; `actions.queryWorkflow(alias)`.

- [ ] Failing tests: an undeclared alias is refused; a source the viewer cannot read returns its error under the alias, not a 500; `/labs/p/programme/<id>/<slug>/` renders the page; a scope the viewer is not in is refused before any read; `page_blank` creates a page-kind definition.
- [ ] Implement; tests pass; commit; PR; merge.

### Task 7: The `home` slot and an organisation coming in

**Files:** Create `connect_labs/scope_config/builtin.py` (namespace `labs`: `{"home": {"fill": {"workflow": int, <owner scope>}}}`, layers organisation/programme/opportunity); modify `page_views.py` (`/labs/p/<scope>/<key>/` → home), `labs/views.py` (`LabsOverviewView`: `?organization_id=<slug>` with a home → redirect); tests `test_home.py`.

- [ ] Failing tests: an org with a home lands on it at `/labs/p/org/<slug>/` and from `/labs/overview/?organization_id=<slug>`; the page's `scope.organization` is that org and `scope.programs` lists its programmes; a remembered (session-only) org does not redirect; no home → the overview as today.
- [ ] Implement; tests pass; PR; merge; **deploy; create the Labs Synthetic example and verify live.**

### Task 8: Retire `pages/`

**Files:** Delete `connect_labs/pages/`, `connect_labs/mcp/tools/pages.py`, `.claude/skills/pages-author/` (replaced by a pointer); modify `config/settings/base.py`, `labs/urls.py` (old `p/<slug>/` → redirect-or-explain view), `mcp/token_scopes.py`, `CLAUDE.md` (app map, tool counts), tests that name `pages_*`.

- [ ] Count live surfaces (local backend + every programme visible to the ace account); rewrite any as page definitions.
- [ ] Failing test: `/labs/p/<old-slug>/` with one matching page redirects to its scoped address; with none, explains the move (200, no 404).
- [ ] Delete; tests pass; re-measure the MCP tool count; PR; merge; deploy; verify an old link live.
