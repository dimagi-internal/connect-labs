# Synthetic UI Polish — Design

**Date:** 2026-04-21
**Author:** jjackson + Claude
**Status:** Approved; implementing

## Problem

The `/labs/synthetic/` pages (list, new/edit form, delete confirm) shipped with bare Bootstrap 5 classes (`table table-striped`, `alert alert-warning`, `btn btn-outline-secondary`) while the rest of labs uses a cohesive Tailwind + FontAwesome + brand-deep-purple design system. The synthetic feature reads as "bolted on" — different spacing, different typography, different button styling, generic tables with no visual hierarchy.

Observed friction: the list shows opp IDs as bare integers with no Connect opportunity name, making it hard to identify which opp is which. The dump-progress log is a monospace text dump with no per-file status. The "security" banner is a huge yellow alert instead of a subtle hint.

## Goal

Bring the three synthetic templates in line with the labs design system (matching `labs/explorer/cache_manager.html` and `labs/overview.html`) so the feature visually belongs to the rest of the app. No backend changes; no URL or view-method changes.

## Scope

**In scope:**
- `list.html`, `form.html`, `confirm_delete.html` — full visual rework using Tailwind classes
- Pass opportunity name into the list + form context so opps are human-readable
- Small cleanups: summary stats strip on the list, clearer "data source" radio sections on the form, per-file dump progress icons

**Out of scope (keeps risk low):**
- View signatures (no URL changes, no response-shape changes)
- Form field names / validation logic
- SSE event envelope — frontend JS continues to consume the same `{message, data: {event, ...}}` shape

## Design system reference

Cribbed from `labs/explorer/cache_manager.html` and `labs/overview.html`:

- Layout: `container mx-auto px-4 py-8 max-w-7xl`
- H1: `text-3xl font-bold text-gray-900` with leading icon `text-{accent}-600`
- Subtitle: `text-gray-600` paragraph below H1
- Breadcrumb/back: `inline-flex items-center text-gray-600 hover:text-gray-900` with `fa-arrow-left`
- Card section: `bg-white rounded-lg shadow-sm p-6 border border-gray-200`
- Stat card: `bg-white rounded-lg shadow-sm p-4 border border-gray-200` with small gray label + large bold value
- Buttons: existing custom `button button-sm primary-dark` / `button button-sm outline-style` / `button button-sm outline-style text-red-600 border-red-300 hover:bg-red-50`
- Status pill: `inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-{color}-100 text-{color}-700`
- Table: `min-w-full divide-y divide-gray-200` with `bg-gray-50` header, `text-xs font-medium text-gray-500 uppercase tracking-wider` TH, `whitespace-nowrap` body cells

Accent color for synthetic: **purple** (`text-purple-600`, `bg-purple-50`, etc.). Icon: `fa-vial` (synthesized/alchemy). Matches nothing else in labs, so it's distinct.

## Components

### List page

- Header with fa-vial icon, "Synthetic Opportunities" H1, descriptive subtitle, back link to `labs:overview`, right-aligned action bar with Refresh cache + + New buttons
- Stat strip (3 cards): Active count, Disabled count, Accessible opps count
- Security note: single-line `bg-purple-50 border border-purple-200 rounded-md p-3 text-sm` with lock icon — not a big yellow alert
- Table card — `bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden`:
  - Columns: Opportunity (name + id), Label, GDrive folder (truncated + copy), Status (pill), Updated (relative)
  - Inline action buttons in last column
- Empty state: icon + "No synthetic opportunities yet. Pick an opp from the context selector and click + New to get started." with primary CTA

**Opp name lookup:** view's context now includes a `user_opportunities` map keyed by id. Template does `{{ opp_names|dict_get:opp.opportunity_id|default:"(no access)" }}`.

### New/Edit form

- Header: H1 with fa-vial, back-to-list link
- Context panel (Create only): `bg-purple-50 border border-purple-200 rounded-lg p-4` with opp name/id/org
- Card section for form: `bg-white rounded-lg shadow-sm p-6 border border-gray-200` containing:
  - Label field (single row)
  - Data source block with two bordered sub-panels:
    - "Use existing folder ID" (radio, input, Test access button, result message)
    - "Dump fresh data from Connect" (radio, Start dump button, dump log) — Create only
  - Enabled toggle
  - Notes textarea
- Submit bar: right-aligned `Cancel` (ghost) + `Save` (primary-dark)

**Dump log** becomes a structured list, not a monospace dump. Each event renders a row with:
- `▸` (purple) for informational ("Created folder …")
- `⋯` (gray, animated spinner) for in-progress ("Fetching …")
- `✓` (green) for success ("user_visits.json, 1001 rows")
- `✗` (red) for errors

### Confirm-delete

Small centered card. Warning icon + heading + one-liner explanation. Cancel (ghost) + Delete (red outline).

## Data-flow changes

- `SyntheticListView.get_context_data` builds a `{opp_id: name}` dict from `get_org_data(request)` and passes it as `opp_names` — enables the name column + summary counts
- `SyntheticCreateView.get_context_data` already exposes `context_opp_name`; add `context_opp_org` for the context panel subtitle
- Add a tiny `dict_get` template filter (or inline resolve in context) so the template can do `opp_names.<id>` cleanly

## Testing

Keep the existing 13 view tests; extend them to assert the new behaviors:

- `test_list_shows_opportunity_name` — new row renders with opp name, not just id
- `test_list_empty_state_renders_cta` — empty queryset shows the empty-state block
- `test_create_context_panel_shows_opp_name_and_org` — context panel renders name + org

No backend changes beyond the small context-data additions, so no new DB migrations, no new env vars, no new management commands.

## Rollout

1. Add context-data additions + `dict_get` template filter (if needed)
2. Rewrite `list.html`
3. Rewrite `form.html` (including dump JS for structured log)
4. Rewrite `confirm_delete.html`
5. Update/add view tests
6. Manual smoke: runserver, log in, visit `/labs/synthetic/`, click through flows
7. Deploy

## Out of scope reminders (future work, not now)

- Multi-opp bulk operations
- Folder content preview inline (a "Preview" button that shows filenames + sizes in a modal)
- "Share a copy" button that clones a synthetic opp to a new timestamped folder
