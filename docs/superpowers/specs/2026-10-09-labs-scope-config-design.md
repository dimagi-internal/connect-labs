# Labs scope config: what a programme, an opportunity or a person has set up

**Status:** design, for Jonathan's review (2026-10-09). Nothing built yet.

## Why now

Clicking the **Stock review** tab on the supply header sometimes gave a "not found"
page. The tab is a workflow (8481) pinned into programme 10112's supply navigation.
Its link, `/supply/views/stock-review/`, does not say which programme it belongs
to. The page looks the pin up in whatever programme the session remembers. Several
things change that programme: another browser tab, or any page whose URL names only
an opportunity. When it has changed, the lookup finds nothing and the tab 404s.

The pin is one example of a broader gap. Labs has started to hold **set-up that
belongs to a programme or an opportunity**: which workflow stands in for a supply
tab, which tabs a programme uses, and which workflow is "the programme report". Each
app invents its own place for it (`SupplyWorkflowView`, a `config` key on a workflow
definition, a `pages` surface). None of these places can be seen together. Each also
has its own answer to "which programme am I in?", and that is how the 404 happened.

Labs now keeps primary data of its own (the supply ledger, benchmarks, the org
registry). It needs one place, and one rule, for "how this programme has set labs
up".

## What exists today

Surveyed on `main` at 45ee9de.

| Thing | Where it lives | Scope | What it configures | Notes |
|---|---|---|---|---|
| `pages` surfaces (`/labs/p/<slug>`) | LabsRecord `type=surface` | opp / programme / org / public | an ordered list of cards from providers (`workflow`, `audit`, `audit_breakdown`) | Last change was #878 (8 July). Nothing in the app links to a surface. The workflow card is static by design ("a workflow definition carries no runtime state"). The "fully dynamic page" it wanted is a workflow's own render. |
| `SupplyWorkflowView` | labs DB, `supply_chain/workflow_views` | programme | a workflow tab that adds to, or `replaces`, a built-in supply tab | Ops `view_pin` / `view_unpin` / `view_list`, all read from `labs_context["program_id"]`. Live: 10112 has `stock-review` → 8481 (replaces Workers) and `forecast` → 8495. |
| Supply built-in tabs | code: `navigation.SUPPLY_TABS` | none | the 13 tabs | Cannot be switched off per programme. |
| `render_source` / template workflows | workflow definition LabsRecord | per workflow | which render and config a workflow follows | Per *workflow*, not per scope. It stays. |
| `config.agent.share`, `config.actions` | workflow definition | per workflow | sharing a run with an agent; actions | Stays on the workflow. |
| Benchmark cohorts + disclosure | labs DB `benchmarks` | per cohort | who compares with whom; what is shown | A cohort is its own scope, not a programme. It stays. |
| `DispensingRule` (+ `cases`) | labs DB `supply_chain` | opportunity × item | how visits become consumption, and what a case is | This changes what the **numbers** are. It has history, `as_of` and revisions. It is domain data, not UI set-up. |
| `PulseProgram` / `PulseOpportunity` | labs DB `pulse` | programme / opp | mirrored from Connect's spine | Not set-up, but the clean opportunity → programme mapping. |
| `User.view_synthetic_opps`, `show_internal_features`, `mcp_no_uservisit_data` | `users.User` columns | user | per-person flags | Each new flag is a migration. |
| Session `labs_context` | Django session | one per login | the current org / programme / opp | See "The context rule" below. |

What I found in the context plumbing (`labs/context.py`):

- The middleware's own docstring says context "is represented as URL parameters
  and backed by session". On a list of path prefixes it **redirects a bare URL to
  add the session's context**, so a link always shows its scope. `/supply/` is not
  on that list. Supply URLs therefore never carry their programme, and supply pages
  depend on the session alone.
- The session is replaced whole by whatever the last URL carried. A URL with only
  `?opportunity_id=` leaves the session with no `program_id`, even though every
  opportunity belongs to a programme. The synthetic opportunities merged into
  `org_data` carry `"program"`, and so do Connect's.
- Authorisation of labs-DB data keyed to real scopes goes through
  `labs/access/scopes.py` (`may_use`): membership, never role.

## Approaches considered

1. **Keep a table per app** (`SupplyWorkflowView` and its kind). This is cheap and
   already works. But every app re-solves scope, permission, history, MCP exposure
   and the context bug, and there is still no single place to look. Rejected.
2. **Revive `pages` as the config.** It already has scopes, slugs and MCP tools. But
   it is a *page of cards*, not set-up for existing pages. It lives in LabsRecords,
   which costs an HTTP call to Connect on every page render for real programmes.
   Its card model is the static thing that was already abandoned. Rejected.
3. **One scope-config store in the labs DB, with namespaces each app declares in
   code (recommended).** One table, one resolver, one permission rule, one history
   and one pair of MCP tools. Each app owns its namespace's schema and defaults.
   Supply is the first namespace.

## The design

### 1. What a config is

A **scope config** is a JSON document for one **scope** and one **namespace**:

- **Scope.** One of `programme <id>`, `opportunity <id>` or `user <username>`.
  There is no organisation scope in v1: nothing needs an org-wide default yet, and
  a programme already sits inside one organisation. Adding it later is one more
  layer in the resolver.
- **Namespace.** Declared by an app in code, for example `supply` or `workflows`.
  The declaration gives:
  - a JSON schema;
  - the code defaults;
  - which keys each layer may set. Some keys are programme-only. Some, such as a
    person's own collapsed panels, are user-only.

**Resolution.** For a page in programme P, opportunity O, viewed by user U:

```
code defaults  ←  programme P  ←  opportunity O  ←  user U
```

- Later layers win, key by key.
- Maps merge by key, so an opportunity can switch off one tab without restating
  the rest.
- A list is replaced whole, which is why namespaces model ordered things as maps
  with a `position`.
- A namespace can stop a layer setting a key. For example, supply tabs are
  programme set-up, so they are programme-only, and a person cannot override which
  workflow is the stock review.

The resolver returns the merged config **and its provenance**: which layer set each
key. The settings page can then say "set for this programme" or "labs default", and
an agent can explain why a tab is there.

### 2. Storage: the labs DB, not LabsRecords

```
ScopeConfig(scope_type, scope_id, namespace, data JSONB, version, updated_by, updated_at)
    unique (scope_type, scope_id, namespace)
ScopeConfigChange(config FK, version, before JSONB, after JSONB, changed_by, changed_at, via)
    append-only; `via` = "settings page" | "mcp:<tool>" | "migration"
```

Why the labs DB:

- **No Connect parallel.** This set-up exists only in labs. It is the rule the supply
  domain and the org registry already follow: labs is the primary store only for
  things Connect does not have.
- **Read on every page render.** The supply header resolves it on each request. A
  LabsRecord read is an HTTP call to Connect for real programmes. A local row is
  one indexed query, cached on the request, as `_pinned` caches pins today.
- **Same authorisation as other labs-owned data.** `labs/access/scopes.may_use`
  already authorises labs-DB data keyed to real and labs-only scopes, and fails
  closed. The config reuses it instead of inventing a check.
- **History is cheap and local.** `ScopeConfigChange` gives "who changed this tab,
  when, and from what", and lets the settings page offer "undo".

### 3. Who can change it

| Layer | Read | Change |
|---|---|---|
| Programme | anyone who may use the programme | members of the programme, and Dimagi staff |
| Opportunity | anyone who may use the opportunity | members of the opportunity, and Dimagi staff |
| User | that person | that person |

Membership is the only signal `scopes.py` has today; Connect's role is not exposed
to labs. So v1 lets members edit, which is what `view_pin` allows now. Every change
is attributed and can be undone. If Connect later exposes "programme manager", one
line in the namespace declaration (`edit_requires="manager"`) narrows it. That
decision is open question 2.

### 4. Where you see and change it

- **Settings page.** `/labs/settings/?program_id=…` (and `&opportunity_id=…`) shows
  one section per namespace that has anything to set. The supply section is a list
  of tabs. Each tab has an on/off switch, a drag handle, and for a workflow-backed
  tab a workflow picker filtered to that programme's workflows. Beside each value
  is where it came from ("labs default", "this programme"). Editors also see a small
  "Customise tabs" link at the end of the supply header; readers never do.
- **MCP.** Two tools:
  - `labs_config_get(namespace, program_id | opportunity_id | user)` returns the
    resolved config, each layer's own document, and provenance.
  - `labs_config_set(namespace, scope, patch, expected_version)` applies a
    schema-checked merge patch, with optimistic concurrency like the workflow tools.

  Both work on the no-user-visit-data endpoint, because config holds ids and labels,
  never visits. `supply_chain_view_pin` / `_unpin` / `_list` become thin wrappers
  that write the supply namespace, then retire after one release.
- **Agents and Hal** read `labs_config_get` to learn what a programme has set up
  before reviewing it. A first-use review can then say "the Stock review tab is
  workflow 8481" without being told.

### 5. Supply, the first namespace

```jsonc
// namespace "supply", programme 10112
{
  "tabs": {
    "supply_chain:workers": {            // a built-in tab stood in for by a workflow
      "workflow": 8481, "opportunity_id": 10113,
      "label": "Stock review", "slug": "stock-review"
    },
    "forecast": {                        // a workflow tab that adds to the nav
      "workflow": 8495, "opportunity_id": 10113,
      "label": "Forecast", "slug": "forecast", "after": "supply_chain:flow"
    },
    "supply_chain:alerts": { "hidden": true }   // a built-in tab switched off
  }
}
```

- **Defaults** are today's 13 tabs in today's order (`SUPPLY_TABS`), all on.
- `supply_tabs(request)` reads the resolved namespace instead of
  `SupplyWorkflowView`. Positions and `replaces` behave as now. A tab keyed by a
  built-in name with a `workflow` stands in for that tab; any other key adds one.
- **A hidden tab** drops out of the header and its page shows "turned off for this
  programme", with a link to the settings page for editors. It is not a 404: a
  bookmark should explain itself. **Overview cannot be hidden**, because it is
  where the programme picker and the checks live.
- `/supply/views/<slug>/` resolves the slug from the programme's supply config. The
  run logic (`workflow_views/runs.current_run_id`) is unchanged.
- **Out of scope:** `DispensingRule` stays a supply record. **The boundary
  everywhere is: config says what is shown and what is wired to what; anything
  that changes a number is domain data, with its own history and `as_of`.**

### 6. The context rule (fixes the 404 class, not just this 404)

1. **A link to something that belongs to a scope names that scope.** Supply header
   links (built-in and workflow tabs) carry `?program_id=`, made by one helper
   (`scoped_url(name, request)`) instead of bare `reverse()`. `/supply/` joins the
   middleware's redirect list, so a bare supply URL picks up the session's
   programme and shows it, as every other labs app already does.
2. **An opportunity implies its programme.** When a URL names only
   `opportunity_id`, the middleware fills in `program_id` from that opportunity's
   entry in `org_data`. Synthetic and Connect opportunities both carry it. When the
   opportunity is not in the cached data, the programme falls back to
   `PulseOpportunity`. The session then never loses its programme by visiting an
   opportunity page.
3. **No scope means "pick one", not "not found".** A scoped page with no programme
   in view redirects to that app's home, which already renders the programme
   picker. A 404 is kept for a programme that is known but genuinely has no such
   tab.

Rule 1 removes the dependence on the session. Rule 2 removes the most common way the
session goes wrong. Rule 3 makes the remaining case explain itself.

### 7. What happens to `pages/`

**Recommendation: delete it.** It is about 550 lines plus six MCP tools. Nothing
links to it, and its last change was 8 July. The idea it carried was "quickly
linkable workflows for a programme". Workflow-backed tabs in the scope config now
cover that, and a workflow's own render is the dynamic page. Before deleting, count
`type=surface` records:

- for each programme Dimagi staff can see, via `pages_list(program_id=…)`;
- in the labs-only local backend.

If any are live, move each one's workflow cards into a `workflows` namespace (see
"Later consumers") and redirect its `/labs/p/<slug>` to the programme's settings
page. Delete the app in the same PR as the redirect. This is open question 3.

### 8. Moving the existing pins

1. A data migration writes each programme's `SupplyWorkflowView` rows into
   `ScopeConfig(programme, <id>, "supply")` under `tabs`, and records a
   `ScopeConfigChange` with `via="migration"`. Slugs are kept, so
   `/supply/views/stock-review/` and `/supply/views/forecast/` keep working.
   Programme 10112 becomes the document in section 5.
2. For one release, `SupplyWorkflowView` is still written by the `view_pin`
   wrappers (both stores in one transaction) and read by nothing. The release after
   drops the model and the wrappers.
3. A test pins that every live programme's tabs render identically before and after
   the migration.

## Later consumers, ranked

1. **`workflows`: a programme's or opportunity's named workflows.** For example
   `{"programme_report": 8301, "worker_review": 8302, "stock_review": 8481}`.
   Today an agent finds "the programme report" by listing workflows and reading
   names. The KMC family hand-wires `source_workflow_id` on each instance. Named
   roles let a page link "open the programme report", let Hal find it, and let
   `benchmarks_create_opp_reports` read the source instead of being told. This is
   the most valuable second namespace.
2. **Labs home per programme.** Which workflows and apps a programme's landing
   shows, in what order. This is the `pages` idea again, as config over existing
   screens instead of a card page.
3. **User preferences.** The remembered context (replacing the session's
   `labs_context`, so a person's last programme survives a new login), and future
   per-person flags. Flags gating security (`view_synthetic_opps`,
   `mcp_no_uservisit_data`) stay as `User` columns: they are access, not preference.
4. **Other apps' navigation**, such as microplans and WA Revisit, if they grow
   per-programme tabs. None needs it today.

These stay where they are: `render_source` and workflow `config` (per workflow);
benchmark cohorts and disclosure (per cohort); `DispensingRule` (domain data); and
Pulse's mirrored programmes (Connect's data).

## Testing

- **Resolver.** Defaults ← programme ← opportunity ← user, merged by key. A
  forbidden layer is refused on write, not ignored on read. Provenance names the
  right layer.
- **Authorisation.**
  - A non-member reads and writes nothing.
  - "Could not establish" from `scopes.py` refuses with its own message.
  - A user layer is writable only by its owner.
- **History.** Every write makes one `ScopeConfigChange`. Undo restores `before`.
- **Supply.**
  - A hidden tab leaves the header and its page explains itself.
  - Overview cannot be hidden.
  - A workflow tab renders with the same run logic as today.
  - The migration reproduces today's tabs exactly.
- **Context.**
  - Every supply header link carries `program_id`.
  - A URL with only `opportunity_id` yields that opportunity's programme.
  - A workflow tab with no programme redirects to Supply home.
  - Switching programmes in another tab no longer 404s an open supply page's
    links. This is the regression test for the bug that started this.
- **MCP.** `labs_config_get` / `_set` round-trip.
  - It works on the restricted endpoint.
  - `expected_version` conflicts are refused.

## Open questions for Jonathan

1. **Organisation scope now or later?** *Recommend later:* nothing needs an
   org-wide default yet, and adding a layer is cheap.
2. **Who may change a programme's set-up?** Any member (as `view_pin` allows
   today), or programme managers only? *Recommend members plus attribution plus
   undo for v1.* Narrow it when Connect exposes the role to labs.
3. **Delete `pages/`?** *Recommend yes*, after the surface count in section 7.
4. **Should a person be able to hide supply tabs for themselves?** *Recommend no for
   v1:* tabs are the programme's shared vocabulary, and a person hiding Stock would
   confuse a colleague reading the same page.
5. **Name in the UI.** "Settings" or "Set-up"? *Recommend "Set-up"*, because
   "settings" reads as personal preferences.

## Build order

1. The context rule (section 6): scoped header links, `/supply/` on the redirect
   list, opportunity → programme, and no-programme → home. This can ship on its own
   and fixes the 404 class now.
2. `ScopeConfig` + `ScopeConfigChange`, the resolver, namespace registration,
   `labs_config_get` / `_set` and the `scopes.py` authorisation.
3. The supply namespace: `supply_tabs` reads it, the pin migration, and the
   `view_pin` wrappers.
4. The settings page, with the supply section.
5. The surface count, then delete `pages/`, or migrate it into `workflows`.
6. The `workflows` namespace.
