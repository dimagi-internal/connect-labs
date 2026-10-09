# Labs scope config and pages: how an organisation, a programme, an opportunity or a person sets labs up

**Status:** design, revised 2026-10-09 after Jonathan's answers on #2415. Nothing built yet.

Jonathan decided:
1. an organisation layer, now;
2. members change set-up, with attribution and undo;
3. pages kept, and **"pages should just be completely dynamic code that we render
   safely like workflows"**;
4. no per-person hiding of tabs, for now;
5. the UI is called **Settings**.

## Why now

Clicking the **Stock review** tab on the supply header sometimes gave a "not found"
page. The tab is a workflow (8481) pinned into programme 10112's supply navigation.
Its link, `/supply/views/stock-review/`, does not say which programme it belongs
to. The page looks the pin up in whatever programme the session remembers. Another
browser tab, or any page whose URL names only an opportunity, changes that
programme, and the tab 404s.

The pin is one example of a broader gap. Labs has started to hold **set-up that
belongs to a scope**: which workflow stands in for a supply tab, which tabs a
programme uses, and which workflow is "the programme report". Each app invents its
own place for it (`SupplyWorkflowView`, a `config` key on a workflow definition, a
`pages` surface). None of these places can be seen together. Each also has its own
answer to "which programme am I in?", and that is how the 404 happened. Labs now
keeps primary data of its own (the supply ledger, benchmarks, the org registry). It
needs one place, and one rule, for "how this scope has set labs up", and one way to
build a screen that is not a built-in page.

## What exists today

Surveyed on `main` at 45ee9de.

| Thing | Where it lives | Scope | What it configures | Notes |
|---|---|---|---|---|
| `pages` surfaces (`/labs/p/<slug>`) | LabsRecord `type=surface` | opp / programme / org / public | an ordered list of cards from providers (`workflow`, `audit`, `audit_breakdown`) | About 550 lines plus six MCP tools; last changed 8 July (#878); nothing links to it. Its cards are static. The slug is resolved against the session, the same failure as the 404. No editing UI. |
| Workflow render code | workflow definition LabsRecord (`workflow_render_code`) | owned by an opportunity or a programme | a whole screen as JSX | The dynamic, AI-authored screen Jonathan means. See section 4. |
| `SupplyWorkflowView` | labs DB, `supply_chain/workflow_views` | programme | a workflow tab that adds to, or `replaces`, a built-in supply tab | Ops `view_pin` / `_unpin` / `_list`. Live: 10112 has `stock-review` → 8481 and `forecast` → 8495. To render, it **creates a run every day** (`workflow_views/runs.current_run_id`) just to have an instance. |
| Supply built-in tabs | code: `navigation.SUPPLY_TABS` | none | the 13 tabs | Cannot be switched off. |
| `render_source` / template workflows | workflow definition | per workflow | draft / preview / publish / rollback of render, config defaults and snapshot spec; followers pick up a publish with no deploy | Section 4 reuses this whole. |
| `config.agent.share`, `config.actions` | workflow definition | per workflow | agent sharing, actions | Stays on the workflow. |
| Benchmark cohorts + disclosure | labs DB `benchmarks` | per cohort | who compares with whom | Stays. |
| `DispensingRule` (+ `cases`) | labs DB `supply_chain` | opportunity × item | how visits become consumption | Domain data that changes numbers, with history and `as_of`. Not set-up. |
| `PulseProgram` / `PulseOpportunity` | labs DB `pulse` | programme / opp | mirrored from Connect | The opportunity → programme mapping. |
| `User` flags | `users.User` columns | user | per-person flags | Access, not preference. |
| Session `labs_context` | Django session | one per login | the current org / programme / opp | See section 7. |

What the context plumbing does (`labs/context.py`):

- **Context should be in the URL.** The middleware says context "is represented as
  URL parameters and backed by session". On a list of path prefixes it redirects a
  bare URL to add the session's context. `/supply/` is not on the list.
- **The session is replaced whole by the last URL's context.** A URL with only
  `?opportunity_id=` leaves no `program_id`.
- **Every entry already carries its organisation.** In `org_data`, a programme's
  `organization` is its **owning** org slug. An opportunity's `organization` is its
  **delivering** org slug, and the opportunity also carries `program`. Synthetic
  entries are built in the same shape.
- **Authorisation of labs-owned data keyed to real scopes** is
  `labs/access/scopes.may_use`. It is membership only, covers organisations,
  programmes and opportunities, real and labs-only, and fails closed.

## Approaches considered

1. **A table per app, and `pages` left as it is.** Rejected: every app re-solves
   scope, permission, history and the context bug, and pages stay static.
2. **`pages` as the config store.** Rejected: a page is a screen, not set-up.
3. **One scope-config store, with pages as workflows that have no runs
   (recommended).** One scope model, permission rule, history and context rule for
   set-up. One render path, data path and authoring path for every non-built-in
   screen: the workflow's.

## 1. Scopes and layers

A **scope** is `organisation <slug>`, `programme <id>`, `opportunity <id>` or
`user <username>`. Organisations are keyed by slug: that is what URLs and `org_data`
use, and labs-only organisations have nothing else.

**Layering.** For something viewed in a context, a namespace resolves as:

```
code defaults  ←  organisation  ←  programme  ←  opportunity  ←  user
```

- Later layers win, key by key.
- Maps merge by key.
- Lists are replaced whole, so ordered things are maps with `position`.
- A namespace can stop a layer setting a key. For example, supply tabs cannot be set
  by a user (decision 4).
- The resolver returns the merged value **and its provenance**: which layer set each
  key.

**Which organisation.** It is the one at the top of the context's ownership chain:

| Context | Organisation layer |
|---|---|
| organisation O | O |
| programme P | P's owning organisation |
| opportunity O in programme P | **P's owning organisation**, not O's delivering organisation |
| opportunity O with no programme | O's own organisation |
| user only | none |

A programme's set-up belongs to whoever runs the programme. An LLO delivering one
opportunity in someone else's programme should not restyle that programme by
setting a default in its own organisation. Its settings apply to scopes it owns.

**Who can change each layer** (decision 2):

| Layer | Read | Change |
|---|---|---|
| Organisation | members of the organisation | members of the organisation, and Dimagi staff |
| Programme | anyone who may use the programme | members of the programme, and Dimagi staff |
| Opportunity | anyone who may use the opportunity | members of the opportunity, and Dimagi staff |
| User | that person | that person |

- "Member" is what `scopes.may_use` says.
- A programme member outside the owning organisation can change the programme's
  layer but not the organisation's.
- Every change is attributed and can be undone.
- When Connect exposes roles to labs, `edit_requires="manager"` on a namespace
  narrows it.

## 2. Storage

Config lives in the labs DB:

```
ScopeConfig(scope_type, scope_key, namespace, data JSONB, version, updated_by, updated_at)
    unique (scope_type, scope_key, namespace)        # scope_key: org slug, or the id as text

ScopeConfigChange(config FK, version, before JSONB, after JSONB, changed_by, changed_at, via)
    append-only; via = "settings" | "mcp:<tool>" | "migration"
```

Why the labs DB, not LabsRecords:

- **No Connect parallel.** This set-up exists only in labs.
- **Read on every render.** The supply header reads it on each request. A LabsRecord
  read is an HTTP call to Connect for real programmes.
- **Same authorisation as labs' other owned data** (`scopes.may_use`).
- **Cheap history and undo.**

Pages are **not** stored here. A page is a workflow definition (section 4), so it
lives where workflows live and inherits their versioning.

## 3. Settings: where config is seen and changed

- **Settings page.** `/labs/settings/<scope>/<key>/`, for example
  `/labs/settings/programme/10112/`. It has one section per namespace. Each value
  shows where it came from ("labs default", "Dimagi (organisation)", "this
  programme"), and every section has a history with undo. Editors see a **Settings**
  link at the end of an app's navigation; readers never do.
- **MCP.**
  - `labs_config_get(namespace, scope)` returns the resolved value, each layer and
    provenance.
  - `labs_config_set(namespace, scope, patch, expected_version)` applies a
    schema-checked merge patch.
  - `labs_config_history` and `labs_config_undo`.
  - All of them work on the no-user-visit-data endpoint, because config holds ids
    and labels, never visits.
- **Agents and Hal** read `labs_config_get` before reviewing a scope.

## 4. Pages: dynamic code, rendered the way workflows are

### A page is a workflow with no runs

**Decision: a page is a workflow definition whose template declares
`kind: "page"`.** It is not a separate record type.

| | Workflow | Page |
|---|---|---|
| Record | `workflow_definition` + `workflow_render_code` | the same |
| Render | the workflow runner | the same runner, in **page mode** |
| Runs, statuses, snapshots, completion | yes | none: no run is created, and `instance` is an in-memory blank like edit mode's |
| Data | declared sources, read as the viewer | the same, plus page-only sources below |
| Authoring | workflow MCP tools and template workflows | the same |

Why the same record and not a new type:

- **Everything a page needs already exists for workflows**, tested and in use:
  - the runner, Babel, the error boundary, the shared `window.Labs*` /
    `window.LabsReport` primitives;
  - declared pipelines and supply sources, and the semantic endpoint;
  - actions with preview → confirm;
  - the render-code lint;
  - 45 `workflow_*` MCP tools;
  - template workflows with draft, preview, publish, rollback and follow;
  - `workflow_sync_from_deployed_template`;
  - the workflow-author skill.

  A separate type would fork all of it, or wrap all of it.
- **The difference is a missing feature (runs), not a different thing.** The
  runner already renders without a persisted run: edit mode passes a temporary
  instance. Page mode is that, without the editor.
- **It removes a wart.** Today a pinned supply tab creates a run every day just to
  have an instance to render. A page-kind tab creates nothing.

What page mode changes in the runner:

- `instance` is `{state: {}}`.
- `onUpdateState` keeps state in the browser only. A page that needs to remember
  something writes it through an action, not run state.
- The run-only endpoints (`updateState`, `completeRun`, `renameRun`, `getSnapshot`,
  worker results) are absent.
- `view` reads live data.
- The workflow list, the program view and `workflow_run_default` skip page-kind
  definitions, which have no runs to list or open.

### What "render safely" means today, and what pages add

Today, for workflows:

1. **The code runs in labs' own page, not in a sandbox.**
   - `DynamicWorkflow.tsx` wraps the JSX in a function, transpiles it with Babel
     standalone, and `eval`s it in the labs origin, with React passed in.
   - It can touch the DOM and `window`, and can `fetch` any labs URL with the
     viewer's cookies.
   - The CSP (`utils/csp.py`) is **report-only** and allows `'unsafe-eval'`
     precisely for this.
2. **A crash is contained.** `DynamicErrorBoundary` catches render errors and shows
   them instead of a blank page.
3. **The real boundary is on the server.** Every data call goes to an endpoint keyed
   by the definition (`/labs/workflow/api/<id>/supply-query/`, `.../pipeline-query/`,
   `.../semantic/`, ...). The endpoint reads only what that definition declares and
   runs it **as the viewer**, with the scope and access checks of the source
   (`supply_sources.py` runs supply READ operations as the viewer; pipelines are
   scoped to the definition's opportunities). Writes are actions: preview → confirm
   → background execution as the person, recorded.
4. **Only someone who can write the definition can change its code.** That is the
   LabsRecord ACL on the definition's scope, plus the render-code lint on write.
   The lint warns about missing Tailwind classes; its policy checks were removed
   because they blocked valid JS (`mcp/tools/workflows.py::_validate_render_code`).

So "safe" today means: **crash-isolated, author-trusted, with data authorised on
the server.** It does not mean sandboxed.

What a page adds is reach, not power. A page can be organisation-wide and is meant
to be linked, so more people run code that fewer people wrote. Render code could
already fetch any labs URL as the viewer, so a page can do nothing a workflow
cannot. Pages therefore need **no new mechanism to ship**. They get three
guarantees, all of which also apply to workflows:

1. **A page renders only from a definition the viewer can read**, at a path that
   names its scope (section 7). A link cannot pull in a page from a scope the
   viewer is not in.
2. **Data only through declared sources and actions.** The page-mode endpoints
   refuse a source the definition does not declare, as the workflow endpoints
   already do.
3. **Every write to a page's render code is attributed** in the template-workflow
   version history, so "who changed this page" has an answer.

The real hardening is to render workflow and page code in a sandboxed iframe on a
separate origin, with data passed over `postMessage`. That would turn "author
trusted" into "author contained". It applies equally to workflows and pages, so it
belongs to the runner and is its own piece of work (new decision C).

### How a page gets data

Always as the viewer, and always through sources the page's definition declares:

| Source | Declared as | Read by render code as | Exists? |
|---|---|---|---|
| Pipelines (visit data) | `pipeline_sources` | `pipelines.<alias>`, `actions.queryPipelineRows` | yes |
| Supply figures | `supply_sources` | `supply.<alias>`, `actions.querySupply` | yes |
| Indicators | the bound semantic registry | the `semantic/` endpoint | yes |
| **Other workflows' runs** | `workflow_sources: [{alias, workflow: <id> \| {role}, read: "latest_run" \| "saved_runs" \| "summary"}]` | `workflows.<alias>`, `actions.queryWorkflow(alias, {...})` | **new** |
| **The scope in view** | always present | `scope`: `{organization, program, opportunity, user}`, with names | **new**, page and workflow |
| **Config** | `config_reads: ["workflows", "supply", ...]` | `config.<namespace>`, resolved for the scope in view | **new**, page and workflow |

`workflow_sources` reads another workflow's runs the way `snapshot_state` and
`workflow_history_runs` do now: the summary without fetching the child snapshot,
the latest saved run, or a list of saved runs. It runs as the viewer, with the
target definition's own LabsRecord ACL, so a page can show a programme report's
latest scores only to people who could open that report. A source can name a
workflow by **role** (section 6), so an organisation's page template reads "the
programme report" in whichever programme it is opened.

**Links.** `links.page(slug)`, `links.workflow(id | {role})` and
`links.supply(tab)` build scoped URLs, so code never hand-assembles one.

### Where a page lives and what it is called

A page definition is owned like a workflow definition: by an opportunity, a
programme (program-owned, as `program_view.py` defines), or, new, **an
organisation**. An organisation-owned definition is the same LabsRecord pattern
with the organisation FK (`WorkflowDataAccess` already takes `organization_id`).

**User pages are not in v1.** The LabsRecord API cannot keep a record private to one
person inside a scope its colleagues can read. New decision B.

**Addresses** put the scope in the path:

```
/labs/p/org/<slug>/<page>/   /labs/p/programme/<id>/<page>/   /labs/p/opportunity/<id>/<page>/
```

- `<page>` is the definition's `page.slug`, unique within its scope;
  `/labs/p/<definition id>/` also works.
- Opening a page sets the session context to its scope.
- Old `/labs/p/<slug>` links redirect when exactly one readable page has that slug,
  and otherwise show a "which one?" list.

### Authoring: AI and MCP first

The workflow-author loop as it is today: pull, edit the JSX, push, preview.

- **Create.** `workflow_create_from_template(template_key="page_blank", program_id=…)`.
  `page_blank` is a new seed template with `kind: "page"`, no statuses, and a
  starter render that shows `scope` and one supply figure. `workflow_create` with
  `kind: "page"` works too.
- **Edit.** `workflow_get`, `workflow_update_render_code` / `workflow_patch_render_code`,
  `workflow_update_definition` (to declare sources), and `workflow_preview_as_of`.
- **Draft, preview, publish, rollback: yes, free.**
  - Make the page a **template workflow** (`workflow_template_create`).
  - Edit with `workflow_template_update_draft` and check with
    `workflow_template_preview`.
  - `workflow_template_publish` / `_rollback` publish and roll back.
  - Every other page that `workflow_follow_template`s it updates on publish with no
    deploy.

  This is how one organisation-wide page design reaches every programme: the
  template is written once, and each programme's page follows it and reads its own
  `scope`.
- **Skills.** The `workflow-author` skill gains a "pages" section. The
  `pages-author` skill is rewritten as a short pointer to it.

**People who don't write code** edit through the workflow's existing chat (the AI
agent beside the runner), the same way workflows are edited now. There is no drag
editor.

## 5. Navigation: how pages and app tabs relate

**An app's navigation is a list of slots. A slot is filled by a built-in screen or
by a workflow** (and a page is a workflow):

| Filler | Example | Rendered by |
|---|---|---|
| `builtin` | Supply's Stock, Orders, Network | the app's own Django view, which keeps its forms, `as_of` rewind and permissions |
| `workflow` (kind `page`) | "RUTF operations" | the runner in page mode, inside the app's header; no run |
| `workflow` (a workflow with runs) | Stock review → 8481 | the runner with the current run (today's pinned-tab behaviour) |

So **a Supply tab is a slot, not a page**: built-in screens remain Django views.
**A page can be pinned as a tab**, and the runner decides from the definition's
`kind` whether it needs a run.

Slots are config, in the app's namespace:

```jsonc
// supply, programme 10112
{
  "tabs": {
    "supply_chain:workers": { "fill": { "workflow": 8481, "opportunity_id": 10113 },
                              "label": "Stock review", "slug": "stock-review" },
    "forecast":             { "fill": { "workflow": 8495, "opportunity_id": 10113 },
                              "label": "Forecast", "slug": "forecast", "after": "supply_chain:flow" },
    "ops":                  { "fill": { "workflow": { "role": "supply_ops_page" } },
                              "label": "Operations", "slug": "ops", "after": "forecast" },
    "supply_chain:alerts":  { "hidden": true }
  }
}
```

- **Defaults** are today's 13 tabs, all on. Overview cannot be hidden.
- **A hidden tab's page** says "turned off in Settings" and links editors there. It
  does not 404.
- An organisation can set Supply's tabs for every programme it owns, naming
  workflows by **role** so each programme fills in its own.
- `/supply/views/<slug>/` resolves the slug from the resolved config.

The `labs` namespace's `home` slot names the page a scope opens on, so
`/labs/p/programme/10112/` with no page slug lands on it.

**The boundary everywhere:** config and pages say what is shown and what is wired to
what. Anything that changes a number, like a dispensing rule, stays domain data with
its own history and `as_of`.

## 6. The `workflows` namespace: named workflows

```jsonc
{ "roles": { "stock_review": 8481, "stock_forecast": 8495, "programme_report": 8301,
             "supply_ops_page": 8520 } }       // programme 10112
{ "roles": { "opp_report": 8350 } }           // opportunity 10113
```

- Slot fills, `workflow_sources` and `links.workflow` can name a role instead of an
  id, so an organisation's template page or tab set works in every programme.
- Agents find "the programme report" by asking.
- Later, benchmarks and the KMC hand-down read their source workflow here instead of
  a hand-wired `source_workflow_id`.

## 7. The context rule (fixes the 404 class)

1. **A link to something scoped names its scope.** App header links carry their
   scope through one helper (`scoped_url`), `/supply/` joins the middleware's
   redirect list, and pages carry their scope in the path.
2. **An opportunity implies its programme**, and a programme or opportunity implies
   its organisation, from `org_data`, falling back to `PulseOpportunity`.
3. **No scope means "pick one", not "not found."** A 404 is kept for a slug that is
   genuinely absent from a known scope.

The scoped pinned-tab link and "opportunity implies programme" are shipping
separately ahead of this work.

## 8. What happens to existing things

- **The `pages/` app is replaced, and its address is kept.**
  - `SurfaceDataAccess`, `resolve_surface`, the provider registry, the three
    providers, the card templates and their views are **deleted**.
  - What they did is now render code. The audit breakdown is already a shared
    runner primitive (#869). The "workflow card" becomes a `link` in render code
    plus `workflow_sources` for live figures.
  - `/labs/p/...` is re-routed to the runner in page mode.
- **`pages_*` MCP tools: deleted**, not wrapped. Every one maps to a workflow tool:

  | Old tool | New equivalent |
  |---|---|
  | `pages_create` | `workflow_create_from_template(template_key="page_blank")` |
  | `pages_list` | `workflow_list(kind="page")` |
  | `pages_get` | `workflow_get` |
  | `pages_update` | `workflow_update_render_code` / `_definition` |
  | `pages_delete` | `workflow_delete` |
  | `pages_list_providers` | — (nothing to list) |

  Keeping six wrappers would leave two names for one thing. The CLAUDE.md MCP
  counts are updated.
- **Existing surfaces.** Count `type=surface` LabsRecords first:
  - for each programme Dimagi staff can see, via `pages_list(program_id=…)`;
  - in the labs-only local backend.

  Have the workflow-author agent rewrite each as a page definition in the same
  scope. Its cards become JSX using the same data. Archive the old records.
- **`SupplyWorkflowView`.** A data migration writes each programme's pins into the
  `supply` namespace as `workflow` fills, keeping slugs, so
  `/supply/views/stock-review/` and `/supply/views/forecast/` keep working.
  `view_pin` / `_unpin` / `_list` become wrappers for one release, then go. A test
  pins that every programme's tabs render identically before and after.

## 9. Testing

- **Resolver.**
  - Layering and provenance.
  - The org layer of an opportunity in another organisation's programme is the
    owner's.
  - A forbidden layer is refused on write.
- **Authorisation.**
  - Non-members neither read nor write, at any layer.
  - A programme member outside the owning organisation can edit the programme, not
    the organisation.
  - "Could not establish" refuses with its own message.
- **History.** One change row per write; undo restores `before`.
- **Pages.**
  - A `kind: "page"` definition renders in page mode with **no run created** and no
    run endpoints.
  - It is refused at a path whose scope the viewer cannot read.
  - Its endpoints refuse an undeclared source.
  - `workflow_sources` returns another workflow's latest run only to a viewer who
    can read that workflow, and resolves roles.
  - `scope` and `config` props match the path.
  - A page following a template workflow updates on publish.
  - Old `/labs/p/<slug>` redirects or offers a choice.
  - Page-kind definitions are absent from run lists and `workflow_run_default`.
- **Supply slots.**
  - Builtin, workflow and page fills render inside the header; a page fill creates
    no run.
  - Hidden tabs explain themselves; Overview cannot be hidden.
  - The pin migration reproduces today's tabs.
- **Context.**
  - Every scoped header link carries its scope.
  - An opportunity-only URL yields its programme and organisation.
  - Switching programmes in another browser tab no longer breaks an open supply
    page's links.
- **MCP.**
  - `labs_config_*` round-trips with `expected_version` conflicts refused, on the
    restricted endpoint.
  - `workflow_list(kind="page")` works.
  - `pages_*` are gone.

## Open questions for Jonathan

- **A. Which organisation is an opportunity's organisation layer?** *Recommend the
  programme owner's* (section 1). The alternative layers both organisations, which
  lets an LLO restyle part of a programme it does not run.
- **B. User pages.** *Recommend deferring them.* The LabsRecord API cannot keep a
  record private to one person inside a shared scope. When needed, a user page can
  be a page in a scope the person belongs to, marked `owner_only` and filtered by
  labs. That filter is labs-enforced, not Connect-enforced, so it should wait for a
  real need.
- **C. Sandbox the runner?** Render workflow and page code in a sandboxed iframe on
  a separate origin, with data over `postMessage`. *Recommend yes, as its own piece
  of work after pages ship*, because it hardens workflows and pages equally. Until
  then, pages follow the workflow rule: authors are trusted, and data is authorised
  on the server.
- **D. Can an organisation lock a setting** so programmes cannot override it?
  *Recommend not in v1.*

(The earlier questions about sharing user pages and which card providers to build
first no longer apply: there are no cards.)

## Build order

1. **The rest of the context rule** (section 7): `scoped_url` for every supply
   header link, `/supply/` on the redirect list, organisation implied from
   programme, and no-scope → the app's home. The pinned-tab URL fix and
   "opportunity implies programme" are already shipping separately.
2. **The config core.** `ScopeConfig`, `ScopeConfigChange`, the resolver with the
   organisation layer, namespace registration, `scopes.py` authorisation, and
   `labs_config_*`.
3. **The `supply` namespace with slots.** Builtin and workflow fills, hidden tabs,
   the pin migration and the `view_pin` wrappers.
4. **The Settings page**, with the supply section and history/undo.
5. **Page mode in the runner.**
   - `kind: "page"`; a page renders with no run and no run endpoints.
   - Page-kind definitions are skipped in run lists.
   - Supply page fills render through it.
   - The `scope` and `config` props.
6. **Page data and addresses.**
   - `workflow_sources` + `actions.queryWorkflow`.
   - Organisation-owned definitions.
   - `/labs/p/<scope>/<key>/<slug>/` and the old-slug redirect.
   - The `page_blank` seed template and `workflow_list(kind=…)`.
7. **Retire `pages/`.** Count the surfaces and rewrite any into page definitions;
   delete the app's data access, providers, views and `pages_*` tools; update the
   skills and CLAUDE.md counts.
8. **The `workflows` namespace.** Roles in slot fills, `workflow_sources` and
   `links`; the `home` slot.
9. **Later.**
   - The sandboxed runner (decision C).
   - User pages (decision B).
   - Benchmarks and the KMC hand-down reading roles.
   - Other apps' navigation on slots.
