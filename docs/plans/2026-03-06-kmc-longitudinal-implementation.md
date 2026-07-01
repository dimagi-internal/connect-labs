# KMC Longitudinal Workflow Template — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a KMC longitudinal tracking workflow template with actionable dashboard, filterable child list, and interactive child timeline — all as a single workflow engine template.

**Architecture:** Single-file workflow template (`kmc_longitudinal.py`) following the `mbw_monitoring_v2.py` pattern. Pipeline schema extracts visit-level KMC data; React render_code handles three views (dashboard → child list → timeline) with Chart.js and Leaflet for visualizations.

**Tech Stack:** Python (workflow template), React JSX (render_code), Chart.js 4.x, Leaflet 1.9.x, Tailwind CSS

**Design Doc:** `docs/plans/2026-03-06-kmc-longitudinal-workflow-design.md`

---

## Task 1: Add Chart.js and Leaflet CDN to run.html

**Files:**
- Modify: `connect_labs/templates/workflow/run.html`

**Context:** The KMC timeline needs Chart.js for weight charts and Leaflet for visit maps. These are already used elsewhere in the codebase (timeline.html, coverage/map.html) but not loaded in the workflow runner template. The render_code will access them via `window.Chart` and `window.L`.

**Step 1: Add CDN script tags to run.html**

In the `{% block javascript %}` section, after the existing `workflow-runner-bundle.js` script tag, add:

```html
<!-- Chart.js for workflow visualizations -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<!-- Leaflet for map visualizations -->
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
```

Also add the Leaflet CSS. Find the appropriate block for CSS (likely `{% block css %}` or within `{% block content %}`) and add:

```html
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
```

**Step 2: Verify existing workflows still work**

Run: `python manage.py runserver`
Navigate to an existing workflow (e.g., performance_review) and confirm it loads without errors. The added scripts should be inert for workflows that don't use them.

**Step 3: Commit**

```bash
git add connect_labs/templates/workflow/run.html
git commit -m "feat: add Chart.js and Leaflet CDN to workflow run template"
```

---

## Task 2: Create KMC pipeline schema and definition

**Files:**
- Create: `connect_labs/workflow/templates/kmc_longitudinal.py`

**Context:** This task creates the template file with `DEFINITION` and `PIPELINE_SCHEMAS`. The render_code will be added in later tasks. Follow the `mbw_monitoring_v2.py` single-file pattern.

**Reference:** All form paths come from the existing `connect_labs/custom_analysis/kmc/timeline_config.py` (field extractors) and `pipeline_config.py` (linking config). The `_schema_to_config()` method in `connect_labs/workflow/data_access.py:1683-1764` converts these JSON schemas into `AnalysisPipelineConfig` objects at runtime.

**Step 1: Create the template file with DEFINITION and PIPELINE_SCHEMAS**

Create `connect_labs/workflow/templates/kmc_longitudinal.py`:

```python
"""
KMC Longitudinal Tracking Workflow Template.

Dashboard-first view for Kangaroo Mother Care programs. Tracks children
across multiple visits with actionable KPI cards, filterable child list,
and interactive per-child timeline with weight charts and maps.

All data is extracted visit-level and grouped by beneficiary_case_id
client-side in the React component.
"""

DEFINITION = {
    "name": "KMC Longitudinal Tracking",
    "description": "Track KMC children across visits with outcomes dashboard, child list, and timeline",
    "version": 1,
    "templateType": "kmc_longitudinal",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
        {"id": "discharged", "label": "Discharged", "color": "blue"},
        {"id": "lost_to_followup", "label": "Lost to Follow-up", "color": "red"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "visits",
        "name": "KMC Visit Data",
        "description": "Visit-level data for KMC beneficiaries, grouped by beneficiary_case_id",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "linking_field": "beneficiary_case_id",
            "fields": [
                # --- Identity & Linking ---
                {
                    "name": "beneficiary_case_id",
                    "paths": ["form.case.@case_id", "form.kmc_beneficiary_case_id"],
                    "aggregation": "first",
                },
                {
                    "name": "child_name",
                    "paths": ["form.child_details.child_name", "form.svn_name"],
                    "aggregation": "first",
                },
                {
                    "name": "mother_name",
                    "paths": ["form.mothers_details.mother_name", "form.kmc_beneficiary_name"],
                    "aggregation": "first",
                },
                {
                    "name": "mother_phone",
                    "paths": [
                        "form.mothers_details.mothers_phone_number",
                        "form.deduplication_block.mothers_phone_number",
                    ],
                    "aggregation": "first",
                },
                # --- Clinical Outcomes ---
                {
                    "name": "weight",
                    "paths": [
                        "form.anthropometric.child_weight_visit",
                        "form.child_details.birth_weight_reg.child_weight_reg",
                    ],
                    "aggregation": "first",
                    "transform": "kg_to_g",
                },
                {
                    "name": "birth_weight",
                    "paths": [
                        "form.child_details.birth_weight_group.child_weight_birth",
                        "form.child_weight_birth",
                    ],
                    "aggregation": "first",
                    "transform": "kg_to_g",
                },
                {
                    "name": "height",
                    "path": "form.anthropometric.child_height",
                    "aggregation": "first",
                    "transform": "float",
                },
                # --- Visit Metadata ---
                {
                    "name": "visit_date",
                    "paths": ["form.grp_kmc_visit.visit_date", "form.reg_date"],
                    "aggregation": "first",
                    "transform": "date",
                },
                {
                    "name": "visit_number",
                    "path": "form.grp_kmc_visit.visit_number",
                    "aggregation": "first",
                },
                {
                    "name": "visit_type",
                    "path": "form.grp_kmc_visit.visit_type",
                    "aggregation": "first",
                },
                {
                    "name": "form_name",
                    "path": "form.@name",
                    "aggregation": "first",
                },
                {
                    "name": "time_end",
                    "path": "form.meta.timeEnd",
                    "aggregation": "first",
                },
                # --- Location ---
                {
                    "name": "gps",
                    "paths": ["form.visit_gps_manual", "form.reg_gps", "metadata.location"],
                    "aggregation": "first",
                },
                {
                    "name": "village",
                    "path": "form.mothers_details.village",
                    "aggregation": "first",
                },
                {
                    "name": "subcounty",
                    "paths": ["form.mothers_details.subcounty", "form.subcounty"],
                    "aggregation": "first",
                },
                # --- KMC Practice ---
                {
                    "name": "kmc_hours",
                    "path": "form.KMC_24-Hour_Recall.kmc_hours",
                    "aggregation": "first",
                },
                {
                    "name": "kmc_providers",
                    "path": "form.KMC_24-Hour_Recall.kmc_providers",
                    "aggregation": "first",
                },
                {
                    "name": "baby_position",
                    "path": "form.kmc_positioning_checklist.baby_position",
                    "aggregation": "first",
                },
                # --- Feeding ---
                {
                    "name": "feeding_provided",
                    "path": "form.KMC_24-Hour_Recall.feeding_provided",
                    "aggregation": "first",
                },
                {
                    "name": "successful_feeds",
                    "path": "form.danger_signs_checklist.successful_feeds_in_last_24_hours",
                    "aggregation": "first",
                },
                # --- Vital Signs ---
                {
                    "name": "temperature",
                    "path": "form.danger_signs_checklist.svn_temperature",
                    "aggregation": "first",
                    "transform": "float",
                },
                {
                    "name": "breath_count",
                    "path": "form.danger_signs_checklist.child_breath_count",
                    "aggregation": "first",
                },
                {
                    "name": "danger_signs",
                    "path": "form.danger_signs_checklist.danger_sign_list",
                    "aggregation": "first",
                },
                # --- Status ---
                {
                    "name": "kmc_status",
                    "paths": ["form.grp_kmc_beneficiary.kmc_status", "form.kmc_status"],
                    "aggregation": "first",
                },
                {
                    "name": "visit_location",
                    "paths": ["form.visit_location", "form.reg_location"],
                    "aggregation": "first",
                },
                {
                    "name": "visit_timeliness",
                    "path": "form.grp_kmc_visit.visit_timeliness",
                    "aggregation": "first",
                },
                # --- Demographics (header) ---
                {
                    "name": "child_dob",
                    "paths": ["form.child_DOB", "form.child_details.child_DOB"],
                    "aggregation": "first",
                    "transform": "date",
                },
                {
                    "name": "child_gender",
                    "path": "form.child_details.child_gender",
                    "aggregation": "first",
                },
                {
                    "name": "mother_age",
                    "paths": ["form.mothers_details.mother_age", "form.mother_age"],
                    "aggregation": "first",
                },
                {
                    "name": "reg_date",
                    "path": "form.reg_date",
                    "aggregation": "first",
                    "transform": "date",
                },
                # --- FLW ---
                {
                    "name": "flw_username",
                    "path": "form.meta.username",
                    "aggregation": "first",
                },
            ],
        },
    },
]

# Render code placeholder — will be replaced with full React component
RENDER_CODE = """function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    return React.createElement('div', {className: 'p-4 text-gray-600'},
        'KMC Longitudinal Tracking — loading pipeline data...'
    );
}"""

TEMPLATE = {
    "key": "kmc_longitudinal",
    "name": "KMC Longitudinal Tracking",
    "description": "Track KMC children across visits with outcomes dashboard, child list, and timeline",
    "icon": "fa-baby",
    "color": "teal",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
```

**Step 2: Verify template auto-discovery**

Run a quick Python check:
```bash
python -c "from connect_labs.workflow.templates import list_templates; print([t['key'] for t in list_templates()])"
```
Expected: list includes `"kmc_longitudinal"`

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: add KMC longitudinal workflow template with pipeline schema"
```

---

## Task 3: Write the data grouping and KPI computation logic (render_code foundation)

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_longitudinal.py` (replace RENDER_CODE)

**Context:** This task builds the core data processing functions that live inside the render_code. These pure functions group flat visit rows by child and compute dashboard KPIs. They're the foundation everything else renders from.

**Step 1: Replace RENDER_CODE with the data layer + dashboard view**

Replace the placeholder `RENDER_CODE` with the full component. This step focuses on the data processing helpers and the dashboard view. The child list and timeline views will be stubs initially.

The render_code should define these key functions inside `WorkflowUI`:

```javascript
function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    // --- Data Processing ---

    function groupVisitsByChild(visitRows) {
        // Groups flat visit rows by beneficiary_case_id
        // Returns: array of child objects with visits array and computed metrics
        // Each child: { id, name, motherName, motherPhone, birthWeight, childDob,
        //   childGender, motherAge, village, subcounty, regDate,
        //   visits: [{weight, visitDate, visitNumber, ...}],
        //   currentWeight, visitCount, lastVisitDate, weightGain,
        //   isOverdue, reachedThreshold, avgWeightGainPerWeek, flwUsername }
    }

    function computeKPIs(children) {
        // Computes dashboard metrics from grouped children
        // Returns: { totalChildren, activeChildren, overdueChildren,
        //   belowAvgGain, reachedThreshold, avgVisitsPerChild,
        //   discharged, totalVisits }
    }

    function daysSince(dateStr) {
        // Returns days between dateStr and today
    }

    // --- State ---
    var [currentView, setCurrentView] = React.useState('dashboard');
    var [selectedChildId, setSelectedChildId] = React.useState(null);
    var [childListFilter, setChildListFilter] = React.useState('all');

    // --- Computed Data (memoized) ---
    var visitRows = pipelines && pipelines.visits ? (pipelines.visits.rows || []) : [];
    var children = React.useMemo(function() { return groupVisitsByChild(visitRows); }, [visitRows]);
    var kpis = React.useMemo(function() { return computeKPIs(children); }, [children]);

    // --- Loading State ---
    if (!pipelines || !pipelines.visits || !pipelines.visits.rows) {
        return /* loading spinner */;
    }

    // --- View Router ---
    if (currentView === 'timeline' && selectedChildId) {
        return /* ChildTimeline component (stub for now) */;
    }
    if (currentView === 'childList') {
        return /* ChildList component (stub for now) */;
    }
    return /* Dashboard view */;
}
```

**Important implementation details:**
- Use `var` not `const`/`let` — Babel standalone with just the `react` preset doesn't always handle block scoping well in eval'd code. Follow the V2 pattern.
- Access pipeline data via `pipelines.visits.rows` (the alias from PIPELINE_SCHEMAS).
- Visit rows have flat computed fields: `row.beneficiary_case_id`, `row.weight`, `row.visit_date`, etc. (these are the field `name` values from the schema).
- Weight is in grams (the `kg_to_g` transform handles conversion).
- Overdue threshold: 14 days since last visit.
- Weight gain threshold for "below average": children gaining less than 100g per week.
- "Reached threshold" = current weight >= 2500g.

**Dashboard KPI Cards Layout:**
- Top row: 6 cards in a responsive grid (3 cols on large, 2 on medium, 1 on small)
- Each card: icon, number, label, colored border/accent
- Clickable cards navigate to childList with appropriate filter
- Below cards: summary text showing total visits, average visits per child

**Step 2: Manually test the template creation**

```bash
python manage.py runserver
```
1. Navigate to `/labs/workflow/`
2. Click "Create Workflow" and select "KMC Longitudinal Tracking"
3. The workflow should be created successfully
4. Opening it should show the loading spinner, then the dashboard (or "no data" if no pipeline data)

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: add KMC dashboard view with data grouping and KPI computation"
```

---

## Task 4: Build the child list view

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_longitudinal.py` (extend RENDER_CODE)

**Context:** Replace the child list stub with a full filterable, sortable table. This view is reached by clicking a KPI card on the dashboard or the "All Children" nav link.

**Step 1: Implement the ChildList sub-component inside render_code**

Key features to implement:
- **Filter bar:** Dropdown filter matching KPI categories (all, active, overdue, low_gain, threshold_met, discharged). Pre-set from `childListFilter` state when arriving from dashboard card click.
- **Search:** Text input filtering by child name or mother name (case-insensitive).
- **Sortable columns:** Click column header to sort. Columns: Child Name, FLW, Visits, Current Weight, Weight Gain, Last Visit.
- **Visual indicators:** Warning icon (orange) for overdue, checkmark (green) for reached 2.5kg.
- **Row click:** Sets `selectedChildId` and navigates to timeline view.
- **Back button:** Returns to dashboard.

**Table row structure:**
```
| Child Name | FLW      | Visits | Current Wt | Wt Gain | Last Visit    |
| Baby Amara | jane_d   | 6      | 2,300g     | +500g   | 2 days ago    |
| Baby Kofi  | jane_d   | 3      | 1,800g ⚠   | +200g   | 15 days ago ⚠ |
```

**Filtering logic:**
- `all`: no filter
- `active`: `!child.isOverdue && child.kmc_status !== 'discharged'`
- `overdue`: `child.isOverdue`
- `low_gain`: `child.avgWeightGainPerWeek < 100` (grams/week)
- `threshold_met`: `child.reachedThreshold` (>= 2500g)
- `discharged`: `child.kmc_status === 'discharged'`

**Step 2: Test navigation flow**

1. Open the KMC workflow
2. Verify dashboard KPI cards show correct numbers
3. Click "Overdue Visits" card → child list opens with overdue filter
4. Verify filter dropdown shows "Overdue"
5. Click "All" in filter → shows all children
6. Type a name in search → list filters
7. Click a column header → sorts
8. Click "Back to Dashboard" → returns to dashboard

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: add filterable child list view to KMC workflow"
```

---

## Task 5: Build the child timeline header and visit history sidebar

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_longitudinal.py` (extend RENDER_CODE)

**Context:** Replace the timeline stub with the 3-column layout. This task builds the header and left column (visit history list). Chart and map come in the next task.

**Step 1: Implement the timeline header**

The header shows at-a-glance information about the selected child:

```
┌─────────────────────────────────────────────────────────────────┐
│ ← Back to list    Baby Amara              [Active] badge        │
│                                                                  │
│ Col 1: Child Info        Col 2: Weight         Col 3: Contact   │
│ DOB: 2026-01-15         Birth: 1,800g         Mother: Fatima    │
│ Gender: Female          Current: 2,300g       Phone: +256...    │
│ Visits: 6               Gain: +500g (+28%)    Village: Kampala  │
│ In program: 7 weeks     Gain/week: 71g        Subcounty: ...    │
└─────────────────────────────────────────────────────────────────┘
```

**Implementation details:**
- Find the selected child from `children` array using `selectedChildId`
- Header uses a 3-column grid with Tailwind
- Status badge: green for active, blue for discharged, red for lost to follow-up
- Weight gain shows absolute grams and percentage from birth weight
- "Back to list" preserves `childListFilter` state

**Step 2: Implement the visit history sidebar (left column)**

- Vertical list of visits sorted by date (newest first)
- Each visit entry shows: visit number, date, weight, form name
- Selected visit highlighted with blue border/background
- Click a visit → sets `selectedVisitId` state
- Default selection: most recent visit
- If visit has a photo_url, show a small thumbnail

**Step 3: Layout the 3-column grid**

```html
<div style={{display: 'grid', gridTemplateColumns: '200px 1fr 320px', gap: '16px'}}>
    <!-- Left: Visit History -->
    <!-- Center: Chart + Map (placeholder for now) -->
    <!-- Right: Detail Panel (placeholder for now) -->
</div>
```

**Step 4: Test**

1. From child list, click a child
2. Verify header shows correct data
3. Verify visit list appears with correct visit count
4. Click different visits → selected visit highlights
5. Click "Back to list" → returns to child list with filter preserved

**Step 5: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: add child timeline header and visit history sidebar"
```

---

## Task 6: Add weight progression chart (Chart.js)

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_longitudinal.py` (extend RENDER_CODE)

**Context:** Add the weight progression line chart to the center column of the timeline. Uses Chart.js loaded via CDN (Task 1). Access via `window.Chart`.

**Step 1: Implement the weight chart**

**Chart specifications:**
- Type: line chart
- X-axis: visit dates (time scale using chartjs-adapter-date-fns)
- Y-axis: weight in grams
- Data points: one per visit, connected by line
- 2.5kg threshold line: horizontal dashed line at 2500g (annotation or dataset)
- Color zones: points below 2500g colored amber, points at/above colored green
- Birth weight marker: horizontal dotted line at birth_weight (if available)
- Selected visit highlight: larger point radius and different color for selected visit
- Click handler: clicking a data point sets `selectedVisitId`

**React + Chart.js integration pattern:**
```javascript
var chartRef = React.useRef(null);
var chartInstance = React.useRef(null);

React.useEffect(function() {
    if (!chartRef.current || !window.Chart) return;
    if (chartInstance.current) chartInstance.current.destroy();

    var ctx = chartRef.current.getContext('2d');
    chartInstance.current = new window.Chart(ctx, {
        type: 'line',
        data: { /* visit dates and weights */ },
        options: {
            onClick: function(e, elements) {
                if (elements.length > 0) {
                    var idx = elements[0].index;
                    // set selectedVisitId from visit at this index
                }
            },
            scales: {
                x: { type: 'time', time: { unit: 'day' } },
                y: { title: { text: 'Weight (grams)' } }
            }
        }
    });

    return function() {
        if (chartInstance.current) chartInstance.current.destroy();
    };
}, [child.visits, selectedVisitId]);
```

**Important:** Chart.js `update()` or full re-creation on `selectedVisitId` change to update point styles.

**Step 2: Test the chart**

1. Navigate to a child timeline
2. Verify weight chart renders with correct data points
3. Verify 2.5kg threshold line is visible
4. Click a data point → visit history sidebar and detail panel update
5. Click a visit in sidebar → chart highlights corresponding point

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: add weight progression chart with threshold line to KMC timeline"
```

---

## Task 7: Add visit location map (Leaflet)

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_longitudinal.py` (extend RENDER_CODE)

**Context:** Add the Leaflet map below the weight chart in the center column. Shows visit GPS locations as markers. Access Leaflet via `window.L`.

**Step 1: Implement the map**

**Map specifications:**
- Tile layer: OpenStreetMap
- Markers: one per visit that has GPS data (skip visits without GPS)
- Marker colors: blue for registration visit, green for regular visits, red for discharge
- Selected visit marker: larger, with different styling (pulsing or different icon)
- Click handler: clicking a marker sets `selectedVisitId`
- Auto-fit bounds to show all markers
- Popup on marker: visit number and date

**GPS parsing:** The `gps` field from CommCare contains space-separated values: `"lat lng altitude accuracy"`. Parse with:
```javascript
function parseGPS(gpsStr) {
    if (!gpsStr) return null;
    var parts = String(gpsStr).trim().split(/\s+/);
    if (parts.length >= 2) {
        var lat = parseFloat(parts[0]);
        var lng = parseFloat(parts[1]);
        if (!isNaN(lat) && !isNaN(lng) && lat !== 0 && lng !== 0) {
            return [lat, lng];
        }
    }
    return null;
}
```

**React + Leaflet integration:**
```javascript
var mapRef = React.useRef(null);
var mapInstance = React.useRef(null);
var markersRef = React.useRef({});

React.useEffect(function() {
    if (!mapRef.current || !window.L) return;
    if (mapInstance.current) return; // Only init once

    mapInstance.current = window.L.map(mapRef.current).setView([0, 0], 13);
    window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap'
    }).addTo(mapInstance.current);
}, []);

// Separate effect for markers (re-run when visits or selection changes)
React.useEffect(function() {
    if (!mapInstance.current) return;
    // Clear old markers, add new ones, fit bounds
    // Highlight selected visit marker
}, [child.visits, selectedVisitId]);
```

**Step 2: Test the map**

1. Navigate to a child timeline with GPS data
2. Verify map renders with markers
3. Click a marker → visit selects in sidebar and chart
4. Select a visit in sidebar → map marker highlights
5. Verify map auto-fits to show all markers

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: add visit location map to KMC timeline"
```

---

## Task 8: Add clinical detail panel (right column)

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_longitudinal.py` (extend RENDER_CODE)

**Context:** The right column shows clinical details for the selected visit, organized in collapsible sections matching the existing KMC detail_panel widget configuration.

**Step 1: Implement the detail panel**

**Section layout (from timeline_config.py):**
1. **Anthropometric:** weight, height, birth_weight
2. **KMC Practice:** kmc_hours, kmc_providers, baby_position
3. **Feeding:** feeding_provided, successful_feeds
4. **Vital Signs:** temperature, breath_count, danger_signs
5. **Visit Info:** visit_location, visit_timeliness, kmc_status

**Implementation:**
- Shows data for `selectedVisitId` (default: most recent visit)
- Each section has a title bar and key-value rows
- Values that are null/undefined show "—"
- Weight displayed with "g" suffix, temperature with "°C"
- Danger signs highlighted in red if present
- Sections are collapsible (toggle with section header click)

**Step 2: Add photo strip at bottom**

Below the 3-column grid, add a horizontal photo strip:
- Shows photo_url thumbnails for the selected visit
- If no photos, section is hidden
- Photos are from the pipeline's `photo_url` field
- Note: photos may need the image proxy URL pattern. For now, display whatever URL the pipeline returns. If images don't load, this can be addressed as a follow-up.

**Step 3: Test**

1. Select different visits → detail panel updates
2. Verify all sections show correct data
3. Collapse/expand sections
4. Verify photo strip shows when photos exist

**Step 4: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: add clinical detail panel and photo strip to KMC timeline"
```

---

## Task 9: Add dashboard trend charts

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_longitudinal.py` (extend RENDER_CODE)

**Context:** Below the KPI cards on the dashboard, add two small trend charts: enrollment over time and visits per week. These give the "program health at a glance" story.

**Step 1: Implement enrollment trend chart**

- Type: line chart
- X-axis: weeks
- Y-axis: cumulative children enrolled (based on first visit date per child)
- Shows growth of the program over time

**Step 2: Implement visits per week bar chart**

- Type: bar chart
- X-axis: weeks
- Y-axis: number of visits that week
- Shows service delivery volume trends

**Implementation:** Both charts use Chart.js, computed from the `children` array:
```javascript
var enrollmentByWeek = React.useMemo(function() {
    // Group children by week of their first visit
    // Return cumulative enrollment per week
}, [children]);

var visitsByWeek = React.useMemo(function() {
    // Group all visits across all children by week
    // Return count per week
}, [children]);
```

**Layout:** Two charts side-by-side in a 2-column grid below the KPI cards.

**Step 3: Test**

1. Verify enrollment chart shows cumulative growth
2. Verify visits chart shows weekly volumes
3. Both charts render correctly with real data

**Step 4: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: add enrollment and visit trend charts to KMC dashboard"
```

---

## Task 10: Polish and cross-view navigation

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_longitudinal.py` (extend RENDER_CODE)

**Context:** Final polish pass — ensure smooth navigation between views, consistent styling, and edge case handling.

**Step 1: Add view navigation tabs**

At the top of the component (below any header), add tab-style navigation:
```
[Dashboard]  [All Children (42)]  [Child: Baby Amara]
```
- Dashboard and All Children always visible
- Child name tab only visible when viewing a timeline
- Active tab highlighted with bottom border
- Clicking tabs navigates between views

**Step 2: Handle edge cases**

- **No pipeline data yet:** Show loading spinner with "Loading visit data..." message
- **No visits found:** Show "No KMC visit data found for this opportunity" message
- **Child with only 1 visit:** Chart shows single point, map shows single marker
- **Missing GPS on all visits:** Hide map widget, expand chart to full width
- **Missing weight data:** Show "—" in chart tooltip, skip point on chart
- **Very large datasets:** Ensure `useMemo` prevents re-computation on every render

**Step 3: Responsive styling**

- Dashboard cards: 3 cols → 2 cols → 1 col on smaller screens
- Child list table: horizontal scroll on small screens
- Timeline: stack columns vertically on small screens (3-col → single column)
- Use Tailwind responsive classes throughout

**Step 4: Final manual testing**

Walk through the complete flow:
1. Create KMC workflow from template
2. Dashboard loads with KPI cards and trend charts
3. Click "Overdue Visits" → filtered child list
4. Click a child → timeline with header, visit list, chart, map, details
5. Click different visits → all widgets update in sync
6. Click "Back to list" → child list with filter preserved
7. Click "Dashboard" tab → back to dashboard
8. Test with edge cases (no GPS, single visit, etc.)

**Step 5: Commit**

```bash
git add connect_labs/workflow/templates/kmc_longitudinal.py
git commit -m "feat: polish KMC workflow navigation, edge cases, and responsive layout"
```

---

## Task 11: Update design doc status and final commit

**Files:**
- Modify: `docs/plans/2026-03-06-kmc-longitudinal-workflow-design.md`

**Step 1: Update design doc**

Change status from "Approved" to "Implemented" and add implementation notes.

**Step 2: Final commit**

```bash
git add docs/plans/2026-03-06-kmc-longitudinal-workflow-design.md
git commit -m "docs: mark KMC longitudinal design as implemented"
```

---

## Dependency Graph

```
Task 1 (CDN in run.html) ──┐
                            ├── Task 3 (data layer + dashboard)
Task 2 (schema + definition)┘       │
                                     ├── Task 4 (child list)
                                     │       │
                                     │       ├── Task 5 (timeline header + visit sidebar)
                                     │       │       │
                                     │       │       ├── Task 6 (weight chart)
                                     │       │       ├── Task 7 (map)
                                     │       │       ├── Task 8 (detail panel)
                                     │       │       │
                                     │       │       └── Task 10 (polish + navigation)
                                     │       │
                                     └── Task 9 (dashboard trend charts)
                                                        │
                                                    Task 11 (docs)
```

Tasks 1 and 2 are independent and can run in parallel.
Tasks 6, 7, and 8 are independent of each other (all depend on Task 5).
Task 9 depends only on Task 3 (dashboard exists).
Task 10 depends on all view tasks being complete.

## Key Reference Files

| File | Why you need it |
|------|----------------|
| `connect_labs/workflow/templates/mbw_monitoring_v2.py` | **Primary pattern reference** — single-file template with PIPELINE_SCHEMAS and RENDER_CODE |
| `connect_labs/workflow/templates/performance_review.py` | Simpler template reference for RENDER_CODE React patterns |
| `connect_labs/custom_analysis/kmc/timeline_config.py` | All KMC form paths, field extractors, widget configs |
| `connect_labs/custom_analysis/kmc/pipeline_config.py` | Existing pipeline config with form paths and transforms |
| `connect_labs/workflow/data_access.py:1683-1764` | `_schema_to_config()` — how JSON schemas become pipeline configs |
| `connect_labs/templates/workflow/run.html` | Where CDN scripts are added |
| `connect_labs/static/js/workflow-runner.tsx` | How React components are mounted, props passed |
| `components/workflow/DynamicWorkflow.tsx` | How render_code JSX is transpiled and executed |
| `connect_labs/templates/labs/configurable_ui/timeline.html` | Existing Chart.js/Leaflet patterns (Alpine.js, but useful for Chart/Map config) |
