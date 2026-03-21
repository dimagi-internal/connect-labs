# Enhanced KPIs + Impact Headline + Performance Table

## Summary

Transform the fund detail page from a static data display into an impact dashboard. Add an impact headline, enhanced KPI cards with trends, and a performance comparison table with sparklines and status indicators. Remove the low-value Linked Programs and Delivery Types sections.

## Changes

### 1. Impact Headline

Bold statement at the top of the Activity & Payments section:

> **Your fund has reached 6,200 families across 3 countries through 77 community health workers**
> *$2.54 per visit | May 2025 — Mar 2026*

Computed client-side from existing SSE data:
- Families = distinct `entity_name` values across all visits
- Countries = distinct `country` values from allocation metadata
- Health workers = distinct `username` values
- Cost/visit = total USD / approved visit count
- Date range = min/max `visit_date`

### 2. Enhanced KPI Cards (replace current KPI row)

Four cards in a row:

| KPI | Main Value | Subtitle |
|-----|-----------|----------|
| Approved Visits | 15,028 | "This week: 342 | +12% vs last week" with trend arrow |
| USD Distributed | $38,175 | "This week: $1,200" with trend arrow |
| Budget Utilization | 62% | Progress bar. Only shown if fund has `total_budget` set |
| Active FLWs | 45 of 77 | "active in last 14 days" |

Trend arrows: green up arrow if this week > last week, red down if less, gray dash if equal.

"This week" = last 7 days from max visit_date (not today, since data may lag).

### 3. Performance Comparison Table (replaces static allocations table)

Each allocation becomes a performance row:

| Column | Content | Width |
|--------|---------|-------|
| Opportunity | Name (bold) + LLO name (gray) + country | 35% |
| Visits | Approved count + sparkline (8 weeks) | 25% |
| USD Distributed | Total + cost/visit in gray | 15% |
| Active FLWs | Count active in last 14 days | 10% |
| Trend | Arrow icon + % change (this vs last week) | 7% |
| Status | Dot: green/yellow/red | 8% |

**Status logic:**
- Green: visits in last 7 days
- Yellow: last visit 8-14 days ago
- Red: no visits in 14+ days (or no visit data)

**Sparklines:** Tiny Chart.js line charts (50px wide, 20px tall) showing approved visits per week for last 8 weeks. One sparkline per allocation row.

**Sorting:** Default sort by approved visits descending. Clickable column headers to re-sort.

### 4. Remove Sections

Delete from `fund_detail.html`:
- "Linked Programs" section (lines ~150-175)
- "Delivery Types" section (lines ~177-192)

### 5. Existing Charts Unchanged

The visits-over-time bar chart, cumulative payments line chart, and GPS map remain as-is below the performance table.

## Implementation

All changes are client-side JS + template. No backend changes.

**Files:**
- Modify: `commcare_connect/static/js/funder-charts.js` — add `renderImpactHeadline()`, update `renderKPIs()`, add `renderPerformanceTable()` with sparklines
- Modify: `commcare_connect/templates/funder_dashboard/fund_detail.html` — remove Programs/Delivery Types sections, add headline container, update KPI container, replace allocations table with performance table container

**Data available from SSE response (flat arrays):**
- `visits[i]`: `{visit_date, username, entity_name, status, location, opp_id, opp_name, country, delivery_type}`
- `payments[i]`: `{status_modified_date, payment_date, usd_flw, usd_org, opp_id, opp_name, country, delivery_type}`
- `fund.total_budget`: available from Django template context (not SSE)
