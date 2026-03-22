# Funder Dashboard Feature Ideas

Captured during brainstorming on 2026-03-20. Grouped by priority.

## Phase 1 (In Progress)
- **Impact headline** — human-readable statement: "Your fund has reached X families across Y countries through Z health workers"
- **Enhanced KPIs** — cost/visit, budget burn rate with progress bar, weekly trends with arrows, active FLWs (last 14 days)
- **Performance comparison table** — ranked opps with sparklines, cost/visit, trend arrows, status dots (green/yellow/red based on recency)
- **Remove Linked Programs and Delivery Types sections** — low-value static displays

## Phase 2: Recent Activity + Alerts
- **"Last 7 days" highlight section** — top of page: "142 visits this week across 3 opps, $1,200 distributed"
- **Worker retention metric** — FLWs active this week vs last month, trend
- **Alerts/flags** — "KMC PIPN has 0 visits in the last 14 days", "Nama Wellness budget is 90% spent with 3 months remaining"
- **Stale opportunity detection** — auto-flag opps with no recent activity

## Phase 3: Visual Polish
- **Animated counters** — KPI numbers count up from 0 on page load
- **Map as hero** — larger map, heatmap layer showing visit density, click cluster to see opp + recent activity
- **Sparklines in allocation rows** — tiny inline charts showing each opp's visit trend

## Phase 4: Filtering
- **Delivery type multi-select** — filter all charts by CHC, KMC, ECD, etc.
- **Country multi-select** — filter by geography
- **Opportunity multi-select** — show/hide specific opps
- **Date range picker** — all charts + KPIs respond to date window
- **Cross-chart filtering** — click a bar in the visits chart to filter the map and payments chart

## Phase 5: Export + Reporting
- **PDF one-pager** — exportable fund summary for board meetings with key charts and KPIs
- **Scheduled email reports** — weekly/monthly fund performance digest

## Phase 6: Deeper Analytics
- **Beneficiary impact tracking** — downstream health outcomes when available
- **Geographic coverage analysis** — which areas are well-served vs underserved
- **Cost efficiency benchmarking** — compare cost/visit across opps, countries, delivery types
- **Forecasting** — "at current delivery pace, full impact delivered by X date"

## Data Notes
- All phase 1-4 features use data already available from `user_visits` and `completed_works` CSV endpoints
- Phase 5 (PDF) requires a server-side rendering approach (e.g., Puppeteer or ReportLab)
- Phase 6 may require additional data sources (health outcome data, geographic boundary data)
