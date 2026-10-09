# Labs scope config and pages: how an organisation, a programme, an opportunity or a person sets labs up

**Status:** design, revised 2026-10-09 after Jonathan's answers on #2415. Nothing built yet.

Jonathan decided:
1. an organisation layer, now;
2. members change set-up, with attribution and undo;
3. pages made real and fully customisable, not deleted;
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
build screens out of what labs already has.

## What exists today

Surveyed on `main` at 45ee9de.

| Thing | Where it lives | Scope | What it configures | Notes |
|---|---|---|---|---|
| `pages` surfaces (`/labs/p/<slug>`) | LabsRecord `type=surface` | opp / programme / org / public | an ordered list of cards from providers (`workflow`, `audit`, `audit_breakdown`) | About 550 lines plus six MCP tools; last changed 8 July (#878); nothing links to it. Each card is static: the workflow card shows a declared block and a link, because "a workflow definition carries no runtime state". The slug is resolved against the session's context, which is the same failure as the 404. There is no editing UI. |
| `SupplyWorkflowView` | labs DB, `supply_chain/workflow_views` | programme | a workflow tab that adds to, or `replaces`, a built-in supply tab | Ops `view_pin` / `view_unpin` / `view_list`. Live: 10112 has `stock-review` → 8481 (replaces Workers) and `forecast` → 8495. Renders the workflow's live runner inside the supply header: the dynamic embed that pages lacks. |
| Supply built-in tabs | code: `navigation.SUPPLY_TABS` | none | the 13 tabs | Cannot be switched off. |
| `render_source` / template workflows | workflow definition LabsRecord | per workflow | which render and config a workflow follows | Stays. |
| `config.agent.share`, `config.actions` | workflow definition | per workflow | agent sharing, actions | Stays on the workflow. |
| Benchmark cohorts + disclosure | labs DB `benchmarks` | per cohort | who compares with whom; what is shown | Stays. |
| `DispensingRule` (+ `cases`) | labs DB `supply_chain` | opportunity × item | how visits become consumption | Changes what the **numbers** are, with history and `as_of`. This is domain data, not set-up. |
| `PulseProgram` / `PulseOpportunity` | labs DB `pulse` | programme / opp | mirrored from Connect | The opportunity → programme mapping. |
| `User` flags (`view_synthetic_opps`, `show_internal_features`, ...) | `users.User` columns | user | per-person flags | Each flag is a migration. |
| Session `labs_context` | Django session | one per login | the current org / programme / opp | See section 7. |

What the context plumbing does (`labs/context.py`):

- **Context should be in the URL.** The middleware says context "is represented as
  URL parameters and backed by session". On a list of path prefixes it redirects a
  bare URL to add the session's context. `/supply/` is not on the list.
- **The session is replaced whole by the last URL's context.** A URL with only
  `?opportunity_id=` leaves no `program_id`.
- **Every entry already carries its organisation.** Programmes and opportunities in
  `org_data` carry `organization`, the org slug: on a programme it is the
  **owning** organisation; on an opportunity it is the **delivering**
  organisation. Opportunities also carry `program`. Synthetic ones are built in the
  same shape.
- **Authorisation of labs-owned data keyed to real scopes** is
  `labs/access/scopes.may_use`. It is membership only, covers organisations
  (`org_slugs`), programmes and opportunities, real and labs-only, and fails closed.

## Approaches considered

1. **A table per app, and `pages` left as it is.** Every app re-solves scope,
   permission, history, MCP and the context bug, and `pages` stays static. Rejected.
2. **`pages` as the config store.** A page is a screen, not set-up for other
   screens. Rejected, but pages become one *consumer* of the config.
3. **One scope-config store with namespaces each app declares, and pages rebuilt
   on the same scopes (recommended).** One scope model, one permission rule, one
   history and one context rule for both "how this scope has set labs up" and
   "screens this scope has built".

## 1. Scopes and layers

A **scope** is one of `organisation <slug>`, `programme <id>`, `opportunity <id>`
or `user <username>`. Organisations are keyed by slug: that is what URLs and
`org_data` use, and labs-only organisations have nothing else.

**Layering.** For a page viewed in some context, a namespace resolves as:

```
code defaults  ←  organisation  ←  programme  ←  opportunity  ←  user
```

- Later layers win, key by key.
- Maps merge by key.
- Lists are replaced whole, so namespaces model ordered things as maps with
  `position`.
- A namespace can stop a layer setting a key. For example, supply tabs cannot be
  set by a user (decision 4).

**Which organisation.** It is the one at the top of the context's ownership chain:

| Context | Organisation layer |
|---|---|
| organisation O | O |
| programme P | P's owning organisation |
| opportunity O in programme P | **P's owning organisation**, not O's delivering organisation |
| opportunity O with no programme | O's own organisation |
| user only | none (defaults ← user) |

The reason: a programme's set-up belongs to whoever runs the programme. An LLO
delivering one opportunity in someone else's programme should not change how that
programme's Supply looks by setting an org-wide default in its own organisation.
The delivering organisation's settings apply when it views **its own** scope: its
organisation page, or programmes it owns. Its opportunity pages inside another
organisation's programme do not use them. This is new decision A below.

**Who can change each layer** (decision 2: members, with attribution and undo):

| Layer | Read | Change |
|---|---|---|
| Organisation | members of the organisation | members of the organisation, and Dimagi staff |
| Programme | anyone who may use the programme | members of the programme, and Dimagi staff |
| Opportunity | anyone who may use the opportunity | members of the opportunity, and Dimagi staff |
| User | that person | that person |

"Member" is what `scopes.may_use` says, for real and labs-only scopes alike. A
programme member who is not in the owning organisation can still change the
programme's own layer, but not the organisation's. When Connect exposes roles to
labs, one line in a namespace's declaration (`edit_requires="manager"`) narrows it.

## 2. Storage

Everything lives in the labs DB:

```
ScopeConfig(scope_type, scope_key, namespace, data JSONB, version, updated_by, updated_at)
    unique (scope_type, scope_key, namespace)        # scope_key: org slug, or the id as text

Page(id, scope_type, scope_key, slug, title, layout JSONB, version,
     created_by, updated_by, updated_at, archived_at)
    unique (scope_type, scope_key, slug)

Change(target_type, target_id, version, before JSONB, after JSONB, changed_by, changed_at, via)
    append-only; target_type in {"config", "page"}; via = "settings" | "page editor" | "mcp:<tool>" | "migration"
```

Why the labs DB and not LabsRecords:

- **No Connect parallel.** Both kinds of thing exist only in labs.
- **Read on every page render.** The supply header and every page read them. A
  LabsRecord read is an HTTP call to Connect for real programmes.
- **Same authorisation as labs' other owned data** (`scopes.may_use`).
- **Cheap history.** `Change` gives "who changed this, when, from what" and powers
  **undo** on both the Settings page and the page editor.

## 3. Settings: where config is seen and changed

- **Settings page.** `/labs/settings/<scope>/<key>/`, for example
  `/labs/settings/programme/10112/`. It has one section per namespace with anything
  to set at that scope. Beside every value is where it came from ("labs default",
  "Dimagi (organisation)", "this programme"). Every section has a history with
  undo. Editors see a small **Settings** link at the end of an app's navigation;
  readers never do.
- **MCP.**
  - `labs_config_get(namespace, scope)` returns the resolved value, each layer's
    own document and provenance.
  - `labs_config_set(namespace, scope, patch, expected_version)` applies a
    schema-checked merge patch.
  - `labs_config_history(namespace, scope)` and `labs_config_undo(namespace, scope, version)`.
  - All of them work on the no-user-visit-data endpoint, because config holds ids
    and labels, never visits.
- **Agents and Hal** read `labs_config_get` before reviewing a scope: "the Stock
  review tab is workflow 8481".

## 4. Pages: rebuilt as real, dynamic, scoped screens

**Decision: rebuild the concept on the new scopes, keeping the card-provider
contract.** The parts of `pages/` that are right are kept:

- the provider registry (`register`, `get_provider`);
- the `CardProvider` / `CardPayload` contract;
- per-card `entitled`, the rule that a page never widens what a viewer can see;
- the three providers and their client renderers.

What made it unusable is replaced:

- storage in LabsRecords;
- a slug resolved against the session;
- static cards;
- no editor.

### What a page is

A **page** belongs to one scope (organisation, programme, opportunity or user). It
has a slug, a title and a **layout**: rows of cards on a 12-column grid.

```jsonc
{
  "rows": [
    { "cards": [
      { "provider": "workflow", "size": 12, "mode": "full",
        "target": { "workflow": { "role": "stock_review" } } }        // resolved through config
    ]},
    { "cards": [
      { "provider": "supply_figure", "size": 4,
        "target": { "source": "stock_forecast", "item": "rutf" },
        "options": { "figure": "programme.runs_dry_on" } },
      { "provider": "workflow", "size": 8, "mode": "tile",
        "target": { "workflow": 8495 } },
      { "provider": "text", "size": 12, "options": { "markdown": "Ask Ada before reordering." } }
    ]},
    { "repeat": "opportunities",                                       // one row per opportunity in the programme
      "cards": [ { "provider": "workflow", "size": 6, "mode": "tile",
                   "target": { "workflow": { "role": "opp_report" }, "opportunity": "$each" } } ] }
  ]
}
```

**Dynamic.** A card's target can be **bound to the context** instead of a fixed id:

- `$context` means the opportunity or programme in view.
- `$each` is the item inside a `repeat` row.
- `{"role": "..."}` is a named workflow from the `workflows` namespace (section 6).

So one organisation-level page ("our programme dashboard") renders correctly for
every programme it is opened in, and a programme page grows a row when an
opportunity is added. A card the viewer is not entitled to is left out, as today.

### Card providers

Each provider declares `key`, `label`, `target_kind`, `entitled`, `get_card_data`,
plus two new things:

- an **options schema** (JSON schema), from which the editor builds its form;
- the **modes** it supports (`tile` and/or `full`).

| Provider | Status | What it shows |
|---|---|---|
| `workflow` | kept, extended | `tile`: the workflow's declared `card` block plus a link (today's behaviour). `full`: the workflow's **live runner**, embedded the way pinned supply tabs are now, with the same run choice and `as_of` handling (moved out of `supply_chain/workflow_views` into a shared embed helper). |
| `workflow_runs` | split out of today's workflow card | its runs table |
| `audit`, `audit_breakdown` | kept | unchanged |
| `supply_figure` | new | one figure or small table from a supply source (`worker_stock`, `stock_forecast`, ...), read as the viewer |
| `indicator` | new | one indicator tile from a semantic registry, read as the viewer |
| `text`, `link` | new | short markdown; a labelled link to any labs URL or another page |

A page cannot embed another page; a `link` card points to it. This avoids cycles and
keeps each page's access checks local.

### Addresses

The scope goes in the path, so a page never depends on the session:

```
/labs/p/org/<slug>/<page>/      /labs/p/programme/<id>/<page>/
/labs/p/opportunity/<id>/<page>/   /labs/p/me/<page>/
```

Opening a page also sets the session context to its scope, as any scoped labs URL
does. Old `/labs/p/<slug>` links redirect when exactly one page the viewer can see
has that slug, and otherwise show a "which one?" list.

### Who sees and edits a page

- **Seeing.** Anyone who may use the page's scope sees the page. A user page is
  private to its owner.
- **Editing.** Editing follows the layer table in section 1, with attribution and
  undo through `Change`. Each card still reads its data as the viewer, so a page
  shows each person only what they could already see.

### Editing

- **On the page itself.** An editor sees **Edit page**, which turns the grid
  editable:
  - add a card from the provider palette;
  - drag to reorder;
  - set the width;
  - a form built from the provider's options schema;
  - a target picker that offers this scope's workflows, roles and sources.

  **Save** writes a new version; **History** lists versions with undo. Readers
  never see the controls.
- **MCP.** The existing `pages_*` tool names stay, re-pointed at the new store, with
  the scope required.
  - `pages_list(scope)`, `pages_get(scope, slug)`, `pages_create`,
    `pages_update(scope, slug, layout, expected_version)`,
    `pages_delete` (archives the page, so it can be undone).
  - `pages_list_providers` (now returning each provider's options schema and
    modes).
  - New: `pages_preview(scope, layout)`, which renders card payloads without
    saving, so an agent can check a page before publishing it.
  - The `pages-author` skill is rewritten for the new layout.

## 5. Navigation: how pages and app tabs relate

**An app's navigation is a list of slots. Each slot is filled by one of three
things:**

| Filler | Example | Rendered by |
|---|---|---|
| `builtin` | Supply's Stock, Orders, Network | the app's own Django view, which keeps its forms, `as_of` rewind and permissions |
| `workflow` | Stock review → workflow 8481 | the page renderer, as a one-card page holding a full-mode `workflow` card |
| `page` | "RUTF operations" → page `rutf-ops` | the page renderer, inside the app's header |

So **a Supply tab is a slot, not a page**: built-in screens remain Django views,
because they are not made of cards. **A page can be pinned as a tab**, and a
workflow tab becomes shorthand for a one-card page. That leaves exactly one way to
render anything that is not built in.

Slots are config. For Supply, in the `supply` namespace:

```jsonc
// programme 10112
{
  "tabs": {
    "supply_chain:workers": { "fill": { "workflow": 8481, "opportunity_id": 10113 },
                              "label": "Stock review", "slug": "stock-review" },
    "forecast":             { "fill": { "workflow": 8495, "opportunity_id": 10113 },
                              "label": "Forecast", "slug": "forecast", "after": "supply_chain:flow" },
    "ops":                  { "fill": { "page": "programme/10112/rutf-ops" },
                              "label": "Operations", "slug": "ops", "after": "forecast" },
    "supply_chain:alerts":  { "hidden": true }
  }
}
```

- **Defaults** are today's 13 tabs, all on. Overview cannot be hidden, because it
  holds the programme picker and the checks.
- **A hidden tab** leaves the header. Its page says "turned off in Settings" and
  links to Settings for editors. It does not 404.
- An organisation can set Supply's tabs for every programme it owns. A programme
  overrides them for itself.
- `/supply/views/<slug>/` resolves the slug from the resolved config and renders
  the fill inside the supply header.

The same slot model gives every scope a **home**: the `labs` namespace's `home` slot
names the page a scope opens on (`/labs/p/programme/10112/` with no page slug). An
organisation can set one home page for all its programmes, with `$context` cards
filling in each programme.

**The boundary everywhere:** config and pages say what is shown and what is wired to
what. Anything that changes a number, like a dispensing rule, stays domain data with
its own history and `as_of`.

## 6. The `workflows` namespace: named workflows

```jsonc
// programme 10112
{ "roles": { "stock_review": 8481, "stock_forecast": 8495, "programme_report": 8301 } }
// opportunity 10113 (overrides or adds)
{ "roles": { "opp_report": 8350 } }
```

- Page cards and tab fills can name a **role** instead of an id, so an
  organisation-level page works in every programme.
- Agents find "the programme report" by asking instead of listing and reading
  names.
- `benchmarks_create_opp_reports` and the KMC hand-down can read their source
  workflow from here instead of a hand-wired `source_workflow_id`. That move is
  later work; the role names come first.

## 7. The context rule (fixes the 404 class)

1. **A link to something scoped names its scope.** App header links carry their
   scope through one helper (`scoped_url`), `/supply/` joins the middleware's
   redirect list, and pages carry their scope in the path.
2. **An opportunity implies its programme**, and a programme or opportunity implies
   its organisation, both from `org_data` (falling back to `PulseOpportunity`). The
   session never loses its programme by visiting an opportunity page.
3. **No scope means "pick one", not "not found."** A 404 is kept for a slug that is
   genuinely absent from a known scope.

The scoped pinned-tab link and rule 2 are shipping separately ahead of this work.
The rest of the rule lands with step 1 below.

## 8. What happens to existing things

- **`pages/` code.** It is kept and rebuilt in place:
  - `SurfaceDataAccess` and `resolve_surface` are replaced by the `Page` model and
    path resolution;
  - the three providers move to the new contract (options schemas, modes);
  - the views gain the editor and the scoped routes.

  Before the switch, every `type=surface` LabsRecord is counted and imported:
  - for each programme Dimagi staff can see, via `pages_list`;
  - for labs-only scopes, from the local backend.

  Each becomes a `Page` in the same scope, with its cards as one row. The
  LabsRecords are then archived, not deleted.
- **`SupplyWorkflowView`.** A data migration writes each programme's pins into the
  `supply` namespace as `workflow` fills, keeping slugs, so
  `/supply/views/stock-review/` and `/supply/views/forecast/` keep working.
  - Programme 10112 becomes the document in section 5, without the `ops` and
    `alerts` lines.
  - `view_pin` / `view_unpin` / `view_list` become wrappers for one release, then
    go.
  - A test pins that every programme's tabs render identically before and after.
- **`User` flags** stay columns. They are access, not preference.

## 9. Testing

- **Resolver.**
  - Defaults ← org ← programme ← opp ← user, merged by key.
  - **The org layer of an opportunity in another organisation's programme is the
    programme owner's.**
  - A forbidden layer is refused on write.
  - Provenance is right.
- **Authorisation.**
  - A non-member reads and writes nothing, at every layer.
  - A programme member outside the owning organisation can edit the programme but
    not the organisation.
  - "Could not establish" refuses with its own message.
- **History.** One `Change` per write. Undo restores `before`, for config and pages.
- **Pages.**
  - Scoped addresses resolve with no session.
  - `$context`, `$each` and roles bind.
  - A card the viewer is not entitled to is left out.
  - A page cannot embed a page.
  - The full workflow card renders the same run as today's pinned tab.
  - Imported surfaces render.
  - Old `/labs/p/<slug>` redirects or offers a choice.
- **Supply slots.**
  - Builtin, workflow and page fills all render inside the header.
  - Hidden tabs explain themselves; Overview cannot be hidden.
  - The pin migration reproduces today's tabs.
- **Context.**
  - Every scoped header link carries its scope.
  - An opportunity-only URL yields its programme and organisation.
  - Switching programmes in another browser tab no longer breaks an open supply
    page's links.
- **MCP.**
  - `labs_config_*` and `pages_*` round-trip.
  - `expected_version` conflicts are refused.
  - Both families work on the restricted endpoint.

## Open questions for Jonathan

These are new, and come out of decisions 1 and 3.

- **A. Which organisation is an opportunity's organisation layer?** *Recommend the
  programme owner's* (section 1). The delivering organisation's own settings apply
  only to scopes it owns. The alternative layers both organisations, delivering
  then owning, which lets an LLO restyle part of a programme it does not run.
- **B. Can an organisation lock a setting** so programmes cannot override it (for
  example, "Supply always shows Orders")? *Recommend not in v1.* Every layer can
  already see where a value came from, and locks add a second rule to explain. Add
  `locked_keys` to a namespace if a real case appears.
- **C. Can a user page be shared?** *Recommend no for v1.* To share a page, copy it
  into a programme or organisation ("Copy to…" in the editor). Per-person sharing
  needs a sharing model labs does not have.
- **D. Which new card providers first?** *Recommend `supply_figure` and `text`/`link`
  with the rebuild* (Supply is the first consumer), then `indicator`.

## Build order

1. **The context rule (section 7).** The pinned-tab URL fix and "opportunity
   implies programme" are already being shipped separately. This step adds the
   rest: `scoped_url` for all supply header links, `/supply/` on the redirect list,
   the organisation implied from a programme, and no-scope → the app's home.
2. **The config core.** `ScopeConfig`, `Change`, the resolver with the
   organisation layer, namespace registration, `scopes.py` authorisation, and the
   `labs_config_*` MCP tools.
3. **The `supply` namespace with slots.** Builtin and workflow fills, hidden tabs,
   the pin migration and the `view_pin` wrappers.
4. **The Settings page**, with the supply section and history/undo.
5. **The pages rebuild.**
   - `Page` model, scoped addresses, the renderer and the shared workflow embed.
   - Providers ported to the new contract, plus `text`, `link` and `supply_figure`.
   - `pages_*` re-pointed, plus `pages_preview`.
   - Existing surfaces imported.
6. **The page editor UI** and the rewritten `pages-author` skill.
7. **The `workflows` namespace and dynamic targets.** Roles, `$context` and
   `repeat` bindings; page fills in navigation slots; the `home` slot.
8. **Later.** The `indicator` provider; benchmarks and KMC hand-down reading
   roles; other apps' navigation (microplans, WA Revisit) on slots.
