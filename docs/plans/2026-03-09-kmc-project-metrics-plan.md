# KMC Project Metrics Dashboard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a new `kmc_project_metrics` workflow template that provides a program-level M&E dashboard for the KMC project, with three views: Overview KPIs, Outcomes & Outputs, and Indicators Table.

**Architecture:** Single-file template (`kmc_project_metrics.py`) following the established V2 pattern. Uses the same `connect_csv` visit-level pipeline as `kmc_longitudinal` but with additional fields for M&E metrics. React render code uses Chart.js for visualizations and links to the existing `kmc_longitudinal` workflow for child-level drill-down.

**Tech Stack:** Python (template definition), React JSX string (render code), Chart.js 4.4.0 (charts), Babel standalone (transpilation)

**Design doc:** `docs/plans/2026-03-09-kmc-project-metrics-design.md`

---

### Task 1: Create template file with DEFINITION and PIPELINE_SCHEMAS

**Files:**
- Create: `connect_labs/workflow/templates/kmc_project_metrics.py`

**Step 1: Create the template file with DEFINITION and PIPELINE_SCHEMAS**

Create `connect_labs/workflow/templates/kmc_project_metrics.py` with the module docstring, DEFINITION, and PIPELINE_SCHEMAS. The pipeline reuses the same `connect_csv` visit-level approach as `kmc_longitudinal` but adds fields needed for M&E indicators (discharge status, referral completion, danger sign assessment flag, KMC hours secondary, days since registration).

```python
"""
KMC Project Metrics Dashboard Workflow Template.

Program-level M&E dashboard for KMC (Kangaroo Mother Care) projects.
Aggregates visit data across all FLWs and SVNs to show overall project
performance against M&E indicator targets.

Three views:
1. Overview — Top-line KPI cards + enrollment/visit charts
2. Outcomes & Outputs — Detailed metrics with charts by M&E category
3. Indicators Table — All computable indicators with status and trend

Uses the same visit-level pipeline as kmc_longitudinal, with client-side
aggregation in React for project-wide metrics.
"""

DEFINITION = {
    "name": "KMC Project Metrics",
    "description": "Program-level M&E dashboard showing enrollment, health outcomes, KMC practice, and visit quality indicators",
    "version": 1,
    "templateType": "kmc_project_metrics",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
        {"id": "review", "label": "Under Review", "color": "yellow"},
        {"id": "closed", "label": "Closed", "color": "gray"},
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
        "name": "KMC Project Metrics Data",
        "description": "Visit-level data for computing program-wide M&E indicators",
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
                    "paths": [
                        "form.grp_kmc_beneficiary.child_name",
                        "form.grp_beneficiary_details.child_name",
                        "form.svn_name",
                        "form.mothers_details.child_name",
                    ],
                    "aggregation": "first",
                },
                {
                    "name": "flw_username",
                    "path": "form.meta.username",
                    "aggregation": "first",
                },
                # --- Visit Metadata ---
                {
                    "name": "visit_number",
                    "path": "form.grp_kmc_visit.visit_number",
                    "aggregation": "first",
                    "transform": "int",
                },
                {
                    "name": "visit_date",
                    "paths": ["form.grp_kmc_visit.visit_date", "form.reg_date"],
                    "aggregation": "first",
                    "transform": "date",
                },
                {
                    "name": "visit_timeliness",
                    "path": "form.grp_kmc_visit.visit_timeliness",
                    "aggregation": "first",
                },
                {
                    "name": "visit_type",
                    "path": "form.grp_kmc_visit.visit_type",
                    "aggregation": "first",
                },
                {
                    "name": "first_visit_date",
                    "path": "form.grp_kmc_visit.first_visit_date",
                    "aggregation": "first",
                    "transform": "date",
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
                # --- Clinical ---
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
                # --- KMC Practice ---
                {
                    "name": "kmc_hours",
                    "path": "form.kmc_24-hour_recall.kmc_hours",
                    "aggregation": "first",
                    "transform": "float",
                },
                {
                    "name": "kmc_hours_secondary",
                    "path": "form.kmc_24-hour_recall.kmc_hours_secondary",
                    "aggregation": "first",
                    "transform": "float",
                },
                {
                    "name": "total_kmc_hours",
                    "path": "form.kmc_24-hour_recall.total_kmc_hours",
                    "aggregation": "first",
                    "transform": "float",
                },
                {
                    "name": "baby_position",
                    "path": "form.kmc_positioning_checklist.baby_position",
                    "aggregation": "first",
                },
                # --- Feeding ---
                {
                    "name": "feeding_provided",
                    "path": "form.kmc_24-hour_recall.feeding_provided",
                    "aggregation": "first",
                },
                {
                    "name": "successful_feeds",
                    "path": "form.danger_signs_checklist.successful_feeds_in_last_24_hours",
                    "aggregation": "first",
                    "transform": "int",
                },
                # --- Danger Signs & Referrals ---
                {
                    "name": "danger_sign_positive",
                    "path": "form.danger_signs_checklist.danger_sign_positive",
                    "aggregation": "first",
                },
                {
                    "name": "danger_sign_list",
                    "path": "form.danger_signs_checklist.danger_sign_list",
                    "aggregation": "first",
                },
                {
                    "name": "child_referred",
                    "path": "form.danger_signs_checklist.child_referred",
                    "aggregation": "first",
                },
                {
                    "name": "child_taken_to_hospital",
                    "path": "form.referral_check.child_taken_to_the_hospital",
                    "aggregation": "first",
                },
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
                    "transform": "int",
                },
                # --- Status & Discharge ---
                {
                    "name": "child_alive",
                    "path": "form.child_alive",
                    "aggregation": "first",
                },
                {
                    "name": "kmc_status",
                    "paths": ["form.grp_kmc_beneficiary.kmc_status", "form.kmc_status"],
                    "aggregation": "first",
                },
                {
                    "name": "kmc_status_discharged",
                    "path": "form.kmc_discontinuation.kmc_status_discharged",
                    "aggregation": "first",
                },
                # --- Registration & Timeline ---
                {
                    "name": "reg_date",
                    "paths": [
                        "form.grp_kmc_beneficiary.reg_date",
                        "form.reg_date",
                    ],
                    "aggregation": "first",
                    "transform": "date",
                },
                {
                    "name": "days_since_reg",
                    "path": "form.days_since_reg",
                    "aggregation": "first",
                    "transform": "int",
                },
                {
                    "name": "child_dob",
                    "paths": [
                        "form.mothers_details.child_DOB",
                        "form.child_DOB",
                    ],
                    "aggregation": "first",
                    "transform": "date",
                },
                # --- Location ---
                {
                    "name": "gps",
                    "paths": ["form.visit_gps_manual", "form.reg_gps", "metadata.location"],
                    "aggregation": "first",
                },
                {
                    "name": "village",
                    "paths": [
                        "form.grp_kmc_beneficiary.village",
                        "form.address_change_grp.location.village",
                        "form.village",
                    ],
                    "aggregation": "first",
                },
                {
                    "name": "subcounty",
                    "paths": ["form.sub_country", "form.subcounty"],
                    "aggregation": "first",
                },
                # --- Payment ---
                {
                    "name": "visit_pay",
                    "path": "form.grp_kmc_visit.visit_pay_yes_no",
                    "aggregation": "first",
                },
            ],
        },
    },
]
```

**Step 2: Verify the file is syntactically correct**

Run: `python -c "import ast; ast.parse(open('connect_labs/workflow/templates/kmc_project_metrics.py').read()); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_project_metrics.py
git commit -m "feat: add KMC project metrics template with definition and pipeline schema"
```

---

### Task 2: Write RENDER_CODE — Data Processing & Overview View

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_project_metrics.py`

**Step 1: Add RENDER_CODE with data processing functions and View 1 (Overview)**

Append `RENDER_CODE` to the file after `PIPELINE_SCHEMAS`. This includes:
- `groupVisitsByChild()` — groups flat visit rows by `beneficiary_case_id`, computes per-child metrics
- `computeProjectMetrics()` — aggregates across all children for project-level KPIs
- `computeWeeklyData()` — enrollment and visit counts by week for charts
- Overview view with 8 KPI cards + 2 Chart.js charts (enrollment trend, visits per week)

The RENDER_CODE is a JSX string that will be transpiled by Babel standalone. Use `var` declarations, `React.useState`, `React.useEffect`, `React.useMemo`, `React.useRef`. Chart.js is available as `window.Chart`.

Key KPI cards for Overview:
1. Total SVNs Enrolled (unique beneficiary_case_ids)
2. Active SVNs (enrolled but not discharged/deceased)
3. 28-Day Retention Rate (% with visits spanning ≥28 days among those enrolled ≥28 days ago)
4. Mortality Rate (% where child_alive = 'no' within 28 days)
5. Avg KMC Hours/Day (mean of total_kmc_hours across visits)
6. Referrals Made (count of visits where child_referred = 'yes')
7. Total Visits (count of all visit rows)
8. Avg Days to First Visit (mean days from reg_date to first_visit_date)

Charts:
1. Enrollment Trend — cumulative unique children over time (line chart)
2. Visits Per Week — bar chart of visit counts by ISO week

The code should follow the exact same patterns as `kmc_longitudinal.py` for Chart.js initialization (useRef + useEffect with cleanup).

**Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('connect_labs/workflow/templates/kmc_project_metrics.py').read()); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_project_metrics.py
git commit -m "feat: add render code with data processing and overview view"
```

---

### Task 3: Write RENDER_CODE — Outcomes & Outputs View (View 2)

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_project_metrics.py`

**Step 1: Add View 2 to the RENDER_CODE**

Add the Outcomes & Outputs view with these sections:

**KMC Practice section:**
- Avg KMC hours over time (line chart with primary/secondary/8hr target)
- KMC hours distribution (text stats: mean, median, % reaching 8hrs)

**Nutrition & Feeding section:**
- Exclusive breastfeeding rate among completers (big number + donut chart of feeding types)
- Feeding type breakdown counts

**Health Outcomes section:**
- Avg weight by visit number (line chart with 2500g threshold)
- Danger sign incidence rate (% visits with danger_sign_positive)
- Referral completion rate (% referred cases where family sought care)

**Visit Quality section:**
- % visits on schedule (from visit_timeliness)
- % visits with danger signs assessed (non-null danger_sign_positive / total visits)
- Avg visits per child

Each section uses a card layout with Chart.js for charts and bold number displays for single metrics. Follow the same useRef/useEffect pattern from Task 2 for additional charts.

**Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('connect_labs/workflow/templates/kmc_project_metrics.py').read()); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_project_metrics.py
git commit -m "feat: add outcomes and outputs view with charts"
```

---

### Task 4: Write RENDER_CODE — Indicators Table View (View 3) and Navigation

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_project_metrics.py`

**Step 1: Add View 3 and tab navigation**

Add the Indicators Table view:
- Sortable table with columns: Level, Indicator, Current Value, Target, Status (green/amber/red badge), Trend (arrow)
- Rows for all computable indicators with pre-defined thresholds
- Color logic: green = meeting target, amber = within 20% of threshold, red = exceeding threshold
- Click row to highlight (future: link to detail)

Add tab navigation bar at the top (same pattern as kmc_longitudinal's dashboard/childList/timeline switcher):
- Overview | Outcomes & Outputs | Indicators Table
- State: `var [currentView, setCurrentView] = React.useState('overview');`

Add "View Individual Children" link button in Overview that constructs a URL to the kmc_longitudinal workflow list page.

Wire up the three views with conditional rendering based on `currentView`.

**Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('connect_labs/workflow/templates/kmc_project_metrics.py').read()); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_project_metrics.py
git commit -m "feat: add indicators table view and tab navigation"
```

---

### Task 5: Add TEMPLATE export and register in __init__.py

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_project_metrics.py`
- Modify: `connect_labs/workflow/templates/__init__.py`

**Step 1: Add TEMPLATE export to kmc_project_metrics.py**

Append at the end of the file:

```python
TEMPLATE = {
    "key": "kmc_project_metrics",
    "name": "KMC Project Metrics",
    "description": "Program-level M&E dashboard showing enrollment, health outcomes, KMC practice, and visit quality indicators",
    "icon": "fa-chart-line",
    "color": "indigo",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
```

**Step 2: Register in __init__.py**

In `connect_labs/workflow/templates/__init__.py`, add `kmc_project_metrics` to the import line (~line 218) and `__all__` list (~line 230):

```python
# Line 218 — add kmc_project_metrics to the import
from . import audit_with_ai_review, kmc_flw_flags, kmc_longitudinal, kmc_project_metrics, mbw_monitoring_v2, ocs_outreach, performance_review  # noqa: E402

# In __all__ — add "kmc_project_metrics"
__all__ = [
    "TEMPLATES",
    "get_template",
    "list_templates",
    "create_workflow_from_template",
    "performance_review",
    "ocs_outreach",
    "audit_with_ai_review",
    "mbw_monitoring_v2",
    "kmc_longitudinal",
    "kmc_flw_flags",
    "kmc_project_metrics",
]
```

**Step 3: Verify template loads**

Run: `python -c "from connect_labs.workflow.templates import get_template; t = get_template('kmc_project_metrics'); print(t['name'] if t else 'NOT FOUND')"`
Expected: `KMC Project Metrics`

**Step 4: Commit**

```bash
git add connect_labs/workflow/templates/kmc_project_metrics.py connect_labs/workflow/templates/__init__.py
git commit -m "feat: register kmc_project_metrics template"
```

---

### Task 6: Write E2E test

**Files:**
- Create: `connect_labs/workflow/tests/e2e/test_kmc_metrics_workflow.py`

**Step 1: Write E2E test following the kmc_longitudinal pattern**

The test follows the same structure as `test_kmc_workflow.py`:
1. Navigate to workflow list, create workflow from `kmc_project_metrics` template
2. Create a new run
3. Verify Overview renders (KPI cards visible)
4. Navigate to Outcomes & Outputs tab
5. Verify charts section renders
6. Navigate to Indicators Table tab
7. Verify table renders with indicator rows
8. Clean up

```python
"""
E2E test for the kmc_project_metrics workflow template.

Tests the full happy path:
1. Navigate to workflow list, create workflow from KMC Project Metrics template
2. Create a new run
3. Verify Overview renders (KPI cards visible)
4. Navigate to Outcomes & Outputs tab
5. Verify charts section renders
6. Navigate to Indicators Table tab
7. Verify table renders with indicator rows

Run:
    pytest connect_labs/workflow/tests/e2e/test_kmc_metrics_workflow.py \
        --ds=config.settings.local -o "addopts=" -v --opportunity-id=874
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestKMCProjectMetricsWorkflow:
    """E2E test for the kmc_project_metrics workflow template."""

    def test_full_kmc_metrics_workflow(self, auth_page, live_server_url, opportunity_id):
        """Test creating and navigating a KMC project metrics workflow end-to-end."""
        page = auth_page
        page.set_default_timeout(120_000)

        # --- Step 1: Navigate to workflow list ---
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        create_btn = page.get_by_role("button", name="Create Workflow")
        expect(create_btn).to_be_visible(timeout=10_000)
        create_btn.click()

        page.get_by_text("Choose a Template").wait_for(timeout=10_000)

        modal = page.locator(".fixed.inset-0.z-50")
        csrf_token = modal.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        response = page.request.post(
            f"{live_server_url}/labs/workflow/create/",
            form={"csrfmiddlewaretoken": csrf_token, "template": "kmc_project_metrics"},
            timeout=60_000,
        )
        assert response.ok or response.status == 302, f"create_from_template failed: {response.status}"

        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # --- Step 2: Create a new run ---
        metrics_cards = page.locator('[data-workflow-template="kmc_project_metrics"]')
        metrics_card = metrics_cards.last
        expect(metrics_card).to_be_visible(timeout=10_000)
        create_run_link = metrics_card.get_by_role("link", name="Create Run")
        expect(create_run_link).to_be_visible(timeout=10_000)
        create_run_link.click()
        page.wait_for_load_state("domcontentloaded")

        # --- Step 3: Verify Overview renders ---
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Wait for dynamic React content to render
        page.wait_for_selector('[data-testid="kpi-card"], .kpi-card, text=/SVNs Enrolled/i', timeout=60_000)

        # Verify KPI cards are visible
        kpi_text = page.locator("body").inner_text()
        assert re.search(r"SVNs Enrolled|Total Enrolled", kpi_text, re.IGNORECASE), \
            f"KPI card 'SVNs Enrolled' not found in page text"

        # --- Step 4: Navigate to Outcomes & Outputs ---
        outcomes_tab = page.get_by_role("button", name=re.compile(r"Outcomes", re.IGNORECASE))
        expect(outcomes_tab).to_be_visible(timeout=10_000)
        outcomes_tab.click()

        # Verify outcomes content
        page.wait_for_selector('text=/KMC Practice|KMC Hours/i', timeout=30_000)

        # --- Step 5: Navigate to Indicators Table ---
        indicators_tab = page.get_by_role("button", name=re.compile(r"Indicators", re.IGNORECASE))
        expect(indicators_tab).to_be_visible(timeout=10_000)
        indicators_tab.click()

        # Verify table renders
        page.wait_for_selector('table, text=/Indicator|Level/i', timeout=30_000)

        # --- Step 6: Navigate back to Overview ---
        overview_tab = page.get_by_role("button", name=re.compile(r"Overview", re.IGNORECASE))
        expect(overview_tab).to_be_visible(timeout=10_000)
        overview_tab.click()

        # Check for JS errors
        critical_errors = [e for e in console_errors if "babel" not in e.lower() and "404" not in e]
        assert len(critical_errors) == 0, f"Console errors: {critical_errors}"
```

**Step 2: Verify test file syntax**

Run: `python -c "import ast; ast.parse(open('connect_labs/workflow/tests/e2e/test_kmc_metrics_workflow.py').read()); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add connect_labs/workflow/tests/e2e/test_kmc_metrics_workflow.py
git commit -m "test: add E2E test for KMC project metrics workflow"
```

---

### Task 7: Run E2E test and fix issues

**Files:**
- May modify: `connect_labs/workflow/templates/kmc_project_metrics.py`
- May modify: `connect_labs/workflow/tests/e2e/test_kmc_metrics_workflow.py`

**Step 1: Run the E2E test**

Run: `pytest connect_labs/workflow/tests/e2e/test_kmc_metrics_workflow.py --ds=config.settings.local -o "addopts=" -v --opportunity-id=874`
Expected: PASS (but may fail on first attempt due to rendering issues)

**Step 2: If test fails, debug using console errors and fix**

Common issues:
- Babel transpilation: make sure to use `var` not `const`/`let`
- Chart.js: make sure to check `window.Chart` availability
- Pipeline data: `pipelines?.visits?.rows || []` — use optional chaining
- Empty data: make sure KPI calculations handle zero-division

**Step 3: Commit fixes if any**

```bash
git add -u
git commit -m "fix: resolve E2E test failures for KMC project metrics"
```

---

### Task 8: Run linters and full test suite

**Step 1: Run pre-commit hooks**

Run: `pre-commit run --all-files`
Expected: All checks pass

**Step 2: Run pytest for the workflow app**

Run: `pytest connect_labs/workflow/ --ds=config.settings.local -o "addopts=" -v --ignore=connect_labs/workflow/tests/e2e`
Expected: All existing tests still pass

**Step 3: Fix any lint/test issues and commit**

```bash
git add -u
git commit -m "chore: fix lint issues"
```
