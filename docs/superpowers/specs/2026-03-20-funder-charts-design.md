# Funder Dashboard Visualizations Design

## Summary

Add interactive charts and maps to the funder dashboard fund detail page, using the existing pipeline infrastructure (`PipelineDataAccess` + `AnalysisPipeline`) to fetch, cache, and aggregate per-opportunity visit data from the Connect CSV export API.

## Motivation

The fund detail page currently shows a static allocations table. Funders need to see:
- How many verified deliveries are happening over time across their portfolio
- How money is flowing (USD distributed over time)
- Where delivery is happening geographically

## Architecture

### Data Flow

```
Fund Detail Page loads
  → Read fund.allocations → extract opportunity IDs
  → For each opp: execute pipeline via PipelineDataAccess
    → AnalysisPipeline fetches Connect CSV (/export/opportunity/{id}/user_visits/)
    → SQL backend caches raw visits in Postgres
    → Extracts fields, aggregates per schema
    → Returns rows with visit_date, status, location, payment amounts
  → React component merges all opp datasets
  → Renders combined Chart.js charts + Leaflet map
  → Filters apply across all charts simultaneously
```

### Pipeline Schemas

Two pipeline definitions created as LabsRecords (via `PipelineDataAccess.create_definition()`):

**1. Funder Visits Pipeline** (`funder_visits`)
- Data source: `connect_csv` (user_visits endpoint)
- Terminal stage: `visit_level` (need per-visit rows for map + time series)
- Fields extracted:
  - `visit_date` — date transform
  - `status` — string (approved/pending/rejected)
  - `location` — string (lat lon altitude accuracy)
  - `entity_name` — string

**2. Funder Payments Pipeline** (`funder_payments`)
- Data source: `connect_csv` — but this needs `completed_works` endpoint, not `user_visits`
- Terminal stage: `aggregated` (per-FLW sums are fine for money charts)
- Fields:
  - `saved_payment_accrued_usd` — float, sum
  - `saved_org_payment_accrued_usd` — float, sum
  - `status_modified_date` — date (approval date)
  - `payment_date` — date

**Note on completed_works:** The `connect_csv` data source fetches `user_visits`. The `completed_works` endpoint returns a separate CSV with USD payment fields (`saved_payment_accrued_usd`, `saved_org_payment_accrued_usd`, `status_modified_date`). The `completed_work` column in `user_visits` is just an integer ID reference, not embedded JSON — so USD amounts are NOT available via the visits pipeline. We fetch `completed_works` CSV directly via httpx.

### Revised approach for payment data

Since `completed_works` is a separate CSV endpoint with different fields (`saved_payment_accrued_usd`, `status_modified_date`), and the pipeline system is designed for `user_visits`:

- **Visits + Map**: Use pipeline (`connect_csv` → `user_visits`)
- **Payments**: Fetch `completed_works` CSV directly per opp, parse client-side or server-side, pass as JSON to React

This avoids extending the pipeline data source system for a second endpoint type. If we want to unify later, we can add a `connect_completed_works` data source.

## View Changes

### Fund Detail View (`views.py`)

The fund detail page needs to:
1. Load the fund record (existing)
2. For each allocation with an `opportunity_id`:
   - Execute the visits pipeline via `PipelineDataAccess`
   - Fetch completed_works CSV via httpx
3. Pass combined data to the template as JSON

### New endpoint: Fund Pipeline Data (SSE)

New SSE streaming endpoint at `/funder/fund/<pk>/pipeline-data/` that:
1. Reads the fund's allocations
2. For each opp, executes the visits pipeline (streaming progress events)
3. For each opp, fetches completed_works CSV
4. Returns combined results as the final SSE event

Inherits from `BaseSSEStreamView` (in `labs/analysis/sse_streaming.py`) for automatic heartbeat support — important for the 43-opp Founders Pledge fund where pipeline execution may take time. Follows the same pattern as `PipelineDataStreamView` in workflow.

## React Component

The fund detail template gets a new React component (inline JSX transpiled by Babel, same as workflow templates) that:

### Charts (Chart.js)

1. **Visits Over Time** — stacked bar chart
   - X-axis: weeks or months (auto-scaled)
   - Y-axis: approved visit count
   - Stacks: one color per opportunity (or per delivery type)
   - Hover shows opp name + count

2. **USD Distributed Over Time** — area chart
   - X-axis: weeks or months
   - Y-axis: cumulative USD (from `saved_payment_accrued_usd + saved_org_payment_accrued_usd`)
   - Line per opp or combined with breakdown on hover

3. **GPS Map** — Leaflet with marker clustering
   - Plot visit locations from the `location` field (parsed: "lat lon alt accuracy")
   - Color markers by opp or delivery type
   - Cluster for density
   - Click cluster to zoom, click marker to see entity name + visit date

### KPI Cards (above charts)

- Total approved visits
- Total USD distributed (FLW + Org)
- Active FLWs (distinct usernames)
- Countries covered (from allocation metadata, not visit data — visits don't have country)
- Date range

### Filters

- Delivery type multi-select (CHC, KMC, ECD, etc.)
- Country multi-select
- Opportunity multi-select
- Date range picker
- All filters apply simultaneously to all charts + map

## Implementation Files

| File | Change |
|------|--------|
| `funder_dashboard/views.py` | Add `FundPipelineDataView` SSE endpoint |
| `funder_dashboard/urls.py` | Register pipeline-data endpoint |
| `templates/funder_dashboard/fund_detail.html` | Add React chart component, connect to SSE |
| `funder_dashboard/data_access.py` | Add methods to fetch completed_works CSV per opp |

No changes to the pipeline infrastructure itself. We use `PipelineDataAccess.execute_pipeline()` as-is.

## Pipeline Definition Setup

A single shared pipeline definition is created once (via MCP or management command) and reused for all funds. The `opportunity_id` is passed at execution time — the definition itself is opp-agnostic. Schema:

```json
{
  "name": "Funder Visits",
  "description": "Visit-level data for funder dashboard charts and maps",
  "grouping_key": "username",
  "terminal_stage": "visit_level",
  "data_source": {"type": "connect_csv"},
  "fields": [
    {"name": "visit_date", "path": "visit_date", "transform": "date"},
    {"name": "status", "path": "status", "transform": "string"},
    {"name": "location", "path": "location", "transform": "string"},
    {"name": "entity_name", "path": "entity_name", "transform": "string"},
    {"name": "entity_id", "path": "entity_id", "transform": "string"}
  ]
}
```

## Caching Strategy

- Pipeline caches raw visits in Postgres per opp (existing behavior)
- Cache tolerance: set high (95%+) so pre-demo cache priming sticks
- Completed works: cache in-memory or session for the request duration
- Subsequent page loads hit cache — fast for all fund sizes including 43-opp Founders Pledge

## Scope

### In scope
- Chart.js visit + payment charts on fund detail page
- Leaflet GPS map with clustering
- Filter controls (delivery type, country, opp, date range)
- KPI summary cards
- SSE streaming for pipeline data loading
- completed_works CSV fetching for payment data

### Out of scope
- Per-opportunity drill-down page (future)
- Pipeline infrastructure changes (using as-is)
- Exchange rate conversion beyond what's already in completed_works CSV
- Real-time data (cache-based, not live)
