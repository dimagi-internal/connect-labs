# KMC FLW Flag Report V2 — Bug Fixes, Logic Alignment, and UI Improvements

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix critical bugs, align flag logic with Neal's methodology, improve column headers/values readability, and add an audit configuration modal.

**Architecture:** All changes are in `kmc_flw_flags.py` (PIPELINE_SCHEMAS + RENDER_CODE) with one small fix in audit `data_access.py`. Weight pair logic, enrollment computation, and formatting all happen client-side in RENDER_CODE. The weight_series pipeline gains two fields (reg_date, discharge_date) for per-case enrollment timing.

**Tech Stack:** Python (pipeline schema), JSX/React (RENDER_CODE), Tailwind CSS

---

## Task 1: Fix weight gain calculation (×1000 bug)

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_flw_flags.py` — RENDER_CODE `computeWeightMetrics` function

**Context:** Weight is already in grams from CommCare (`child_weight_visit` type=Int, label "Weight of SVN (grams)"). The code multiplies by 1000 treating grams as kg, producing absurd values like 16239 g/day instead of ~16 g/day.

**Step 1: Fix the calculation**

In `computeWeightMetrics`, find line:
```javascript
totalDailyGain += (diff * 1000) / days;  // convert kg to g
```

Replace with:
```javascript
totalDailyGain += diff / days;  // weight already in grams
```

**Step 2: Verify mentally**

Example: weight goes from 1500g to 1650g over 10 days = 150/10 = 15 g/day. Correct.

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py
git commit -m "fix: remove erroneous ×1000 in weight gain calculation (weight already in grams)"
```

---

## Task 2: Fix audit creation — selected FLWs not filtered

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_flw_flags.py` — RENDER_CODE `handleCreateAudits`
- Modify: `connect_labs/audit/data_access.py:91-119` — `AuditCriteria.from_dict` (defensive fallback)

**Context:** Template sends `selected_usernames` but `AuditCriteria.from_dict()` reads `selected_flw_user_ids`. Fix both sides.

**Step 1: Fix the template key name**

In `handleCreateAudits`, find:
```javascript
selected_usernames: selectedUsernames
```

Replace with:
```javascript
selected_flw_user_ids: selectedUsernames
```

**Step 2: Add defensive fallback in AuditCriteria.from_dict**

In `data_access.py`, find:
```python
selected_flw_user_ids=data.get("selected_flw_user_ids", []),
```

Replace with:
```python
selected_flw_user_ids=data.get("selected_flw_user_ids") or data.get("selected_usernames", []),
```

**Step 3: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py connect_labs/audit/data_access.py
git commit -m "fix: pass selected_flw_user_ids so audits only cover selected FLWs"
```

---

## Task 3: Fix flag logic to match Neal's methodology

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_flw_flags.py` — RENDER_CODE data processing section

**Context:** Multiple formula corrections needed per Neal's doc.

**Step 1: Fix flag_visits denominator (closed non-mortality cases)**

Find:
```javascript
var avgVisits = closedCases > 0 ? totalVisits / closedCases : null;
```

Replace with:
```javascript
var nonMortClosed = closedCases - deaths;
var avgVisits = nonMortClosed > 0 ? totalVisits / nonMortClosed : null;
```

**Step 2: Fix mortality rate denominator (total cases, not closed)**

Find:
```javascript
var mortRate = closedCases > 0 ? deaths / closedCases : null;
```

Replace with:
```javascript
var mortRate = totalCases > 0 ? deaths / totalCases : null;
```

**Step 3: Add weight pair validation criteria (500g-5000g, 1-30 days apart)**

In `computeWeightMetrics`, after parsing weights, add validation. Find:
```javascript
var w1 = parseFloat(prev.weight);
var w2 = parseFloat(curr.weight);
if (isNaN(w1) || isNaN(w2) || w1 <= 0) continue;
```

Replace with:
```javascript
var w1 = parseFloat(prev.weight);
var w2 = parseFloat(curr.weight);
if (isNaN(w1) || isNaN(w2)) continue;
// Neal: weights must be 500g-5000g
if (w1 < 500 || w1 > 5000 || w2 < 500 || w2 > 5000) continue;
// Neal: visits must be 1-30 days apart
var d1 = new Date(prev.visit_date);
var d2 = new Date(curr.visit_date);
var daysBetween = (d2 - d1) / (1000 * 60 * 60 * 24);
if (daysBetween < 1 || daysBetween > 30) continue;
```

Then update the daily gain calculation below to reuse `daysBetween` instead of recomputing `days`:
```javascript
if (daysBetween > 0) {
    totalDailyGain += diff / daysBetween;  // weight already in grams
    gainPairCount++;
}
```

And remove the duplicate date computation that was there before.

**Step 4: Fix flag_wt_gain threshold direction**

Neal says "mean daily weight gain exceeds 60 g/day" — flag for HIGH gain, not low. Currently our code has:
```javascript
flags.low_wt_gain = wm.weight_pairs >= MIN_CASES.weight && wm.mean_daily_gain !== null && wm.mean_daily_gain < THRESHOLDS.wt_gain;
```

But the threshold is 60 and the flag name in Neal's doc is `flag_wt_gain` (high). Fix:
```javascript
flags.high_wt_gain = wm.weight_pairs >= MIN_CASES.weight && wm.mean_daily_gain !== null && wm.mean_daily_gain > THRESHOLDS.wt_gain;
```

Also update the flag reference in the table rendering from `low_wt_gain` to `high_wt_gain`.

**Step 5: Fix minimum case requirements for mortality flags**

Neal says minimum is "20 closed cases" for mortality. Currently we check `closedCases >= MIN_CASES.mort`. But the MIN_CASES.mort is already 20, so this is fine. However, for flag_mort_low we should also check `totalCases >= MIN_CASES.mort`:
```javascript
flags.high_mort = totalCases >= MIN_CASES.mort && mortRate !== null && mortRate > THRESHOLDS.mort_high;
flags.low_mort = totalCases >= MIN_CASES.mort && mortRate !== null && mortRate < THRESHOLDS.mort_low;
```

**Step 6: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py
git commit -m "fix: align flag computations with Neal's methodology

- flag_visits: divide by closed non-mortality cases
- mortality: use total cases as denominator
- weight pairs: filter 500-5000g range, 1-30 day window
- flag_wt_gain: flag HIGH gain (>60g/day), not low
- mortality minimums: check totalCases not closedCases"
```

---

## Task 4: Add enrollment timing to weight_series pipeline + client-side computation

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_flw_flags.py` — PIPELINE_SCHEMAS and RENDER_CODE

**Context:** Neal's flag_enroll needs ">35% of cases enrolled 8+ days post-discharge". This requires per-case data. We add reg_date and discharge_date to the weight_series pipeline, then compute per-case enrollment lateness client-side.

**Step 1: Add fields to weight_series pipeline**

In PIPELINE_SCHEMAS, add two fields to the `weight_series` schema `fields` array:
```python
{
    "name": "reg_date",
    "paths": ["form.reg_date", "form.grp_kmc_beneficiary.reg_date"],
    "aggregation": "first",
    "transform": "date",
},
{
    "name": "discharge_date",
    "path": "form.hosp_lbl.date_hospital_discharge",
    "aggregation": "first",
    "transform": "date",
},
```

**Step 2: Replace enrollment computation in RENDER_CODE**

Remove the old single-value enrollment logic (the `enrollLate` boolean computed from first reg_date/discharge_date in processedData).

Replace with a function that computes per-case enrollment lateness from weight_series data:

```javascript
var computeEnrollmentMetrics = function(username, weightRows) {
    var myRows = (weightRows || []).filter(function(r) { return r.username === username; });
    if (myRows.length === 0) return { pctLateEnroll: null, casesWithDates: 0 };

    // Group by case, take first reg_date and discharge_date per case
    var byCase = {};
    myRows.forEach(function(r) {
        var cid = r.beneficiary_case_id;
        if (!cid) return;
        if (!byCase[cid]) byCase[cid] = { reg_date: null, discharge_date: null };
        if (r.reg_date && !byCase[cid].reg_date) byCase[cid].reg_date = r.reg_date;
        if (r.discharge_date && !byCase[cid].discharge_date) byCase[cid].discharge_date = r.discharge_date;
    });

    var casesWithDates = 0;
    var lateCases = 0;
    Object.keys(byCase).forEach(function(cid) {
        var c = byCase[cid];
        if (c.reg_date && c.discharge_date) {
            casesWithDates++;
            var rd = new Date(c.reg_date);
            var dd = new Date(c.discharge_date);
            var daysDiff = (rd - dd) / (1000 * 60 * 60 * 24);
            if (daysDiff > 8) lateCases++;
        }
    });

    return {
        pctLateEnroll: casesWithDates >= 10 ? lateCases / casesWithDates : null,
        casesWithDates: casesWithDates
    };
};
```

**Step 3: Update processedData to use new enrollment metrics**

Replace the `enrollLate` boolean logic with:
```javascript
var em = computeEnrollmentMetrics(u, weightRows);
```

And in the return object:
```javascript
pctLateEnroll: em.pctLateEnroll,
casesWithDates: em.casesWithDates,
```

**Step 4: Update flag computation**

Replace:
```javascript
flags.late_enroll = closedCases >= MIN_CASES.enroll && enrollLate === true;
```

With:
```javascript
flags.late_enroll = em.casesWithDates >= MIN_CASES.enroll && em.pctLateEnroll !== null && em.pctLateEnroll > THRESHOLDS.enroll;
```

**Step 5: Remove old enrollment fields from flw_flags pipeline**

Remove `discharge_date` and `reg_date` from the `flw_flags` pipeline since enrollment is now computed from weight_series. (These fields only made sense as aggregated "first" values, which was the wrong approach.)

**Step 6: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py
git commit -m "fix: compute enrollment flag as per-case percentage from weight_series data

Per Neal's methodology: >35% of cases enrolled 8+ days post-discharge.
Previously used a single boolean from first reg/discharge dates."
```

---

## Task 5: Improve column headers, values, and table UX

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_flw_flags.py` — RENDER_CODE table section

**Context:** Headers are cryptic ("AVG VIS", "MORT%", "8+ DAYS", "GAIN"). Values lack context. "NE" not explained.

**Step 1: Replace column headers with descriptive text + tooltips**

Replace the column header `<th>` elements with descriptive names. Add a `title` attribute for tooltips with Neal's definitions. New headers:

| Old | New Header | Tooltip (abbreviated) |
|-----|-----------|----------------------|
| Cases | Cases | Total distinct beneficiary cases |
| Avg Vis | Visits/Case | Avg visits per closed non-mortality case. Flag: <3.0 |
| Mort% | Mortality | Deaths as % of total cases. Flag: <2% or >20% |
| 8+ Days | Late Enroll | % of cases enrolled 8+ days post-discharge. Flag: >35% |
| Danger | Danger Signs | % of visits with danger sign positive. Flag: >30% or 0% |
| Wt Loss | Wt Loss | % of visit pairs showing weight decrease. Flag: >15% |
| Gain | Gain (g/d) | Mean daily weight gain in grams. Flag: >60 g/day |
| Wt Zero | Wt Zero | % of visit pairs with exactly zero weight change. Flag: >30% |

**Step 2: Fix value formatting**

- Enrollment: show percentage (e.g., "42.1%") instead of Yes/No
- Gain: show with units, e.g., "15.2 g/d"
- Add "NE" with title tooltip explaining "Not Eligible — insufficient data"

Update the `fmt` function:
```javascript
var fmt = function(val, type) {
    if (val === null || val === undefined) return null;  // return null, handle NE in rendering
    if (type === 'pct') return (val * 100).toFixed(1) + '%';
    if (type === 'dec') return val.toFixed(1);
    if (type === 'gain') return val.toFixed(1) + ' g/d';
    return String(val);
};
```

For table cells, render NE with a tooltip:
```javascript
var renderCell = function(val, type, flagKey, flags) {
    var formatted = fmt(val, type);
    var flagged = flagKey && flags[flagKey];
    var cellClass = 'px-3 py-3 text-sm text-center ' + (flagged ? 'bg-red-50 text-red-800 font-semibold' : '');
    if (formatted === null) {
        return React.createElement('td', { className: cellClass, title: 'Not Eligible — insufficient data for this metric' },
            React.createElement('span', { className: 'text-gray-400 italic' }, 'NE')
        );
    }
    return React.createElement('td', { className: cellClass }, formatted);
};
```

**Step 3: Add flag breakdown on hover/expand for flag count column**

Show which specific flags are triggered when hovering the flag count badge:
```javascript
var flagLabels = {
    low_visits: 'Low Visits',
    high_mort: 'High Mortality',
    low_mort: 'Low Mortality',
    late_enroll: 'Late Enrollment',
    high_danger: 'High Danger Signs',
    zero_danger: 'Zero Danger Signs',
    high_wt_loss: 'Weight Loss',
    high_wt_gain: 'High Weight Gain',
    high_wt_zero: 'Zero Weight Change'
};
```

In the flag count cell, add a title attribute listing triggered flags:
```javascript
var activeFlags = Object.keys(d.flags).filter(function(k) { return d.flags[k]; });
var flagTitle = activeFlags.map(function(k) { return flagLabels[k] || k; }).join(', ');
```

**Step 4: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py
git commit -m "improve: descriptive column headers, value formatting, NE tooltips, flag breakdown"
```

---

## Task 6: Add audit configuration modal

**Files:**
- Modify: `connect_labs/workflow/templates/kmc_flw_flags.py` — RENDER_CODE

**Context:** Currently clicking "Create Audits" immediately fires with hardcoded last-week dates. Need a modal that lets the user configure: date range, visits per FLW, AI agent, and title before creating.

**Step 1: Add modal state**

```javascript
var _modal = React.useState(false);
var showModal = _modal[0]; var setShowModal = _modal[1];
var _auditConfig = React.useState({
    datePreset: 'last_week',
    startDate: '',
    endDate: '',
    countPerFlw: 10,
    aiAgent: 'scale_validation',
    title: ''
});
var auditConfig = _auditConfig[0]; var setAuditConfig = _auditConfig[1];
```

**Step 2: Initialize date defaults**

Add a useEffect that computes last week dates and sets them in auditConfig, matching the existing pattern from audit_with_ai_review.py.

**Step 3: Replace the direct "Create Audits" button with modal opener**

Change the sticky action bar button from calling `handleCreateAudits` directly to `setShowModal(true)`.

**Step 4: Build the modal component**

Modal with:
- **Title** text input (auto-generated default: "FLW Flag Audit {startDate} to {endDate}")
- **Date preset** buttons: "Last Week", "Last 2 Weeks", "Last Month", "Custom"
- **Custom date range** inputs (shown when preset is "custom")
- **Visits per FLW** number input (default 10)
- **AI Agent** selector (default "scale_validation", option for "none")
- **Selected FLW count** summary (read-only)
- **Create** and **Cancel** buttons

Modal renders as a fixed overlay with backdrop:
```javascript
{showModal && React.createElement('div', { className: 'fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50' },
    React.createElement('div', { className: 'bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 p-6' },
        // Modal content...
    )
)}
```

**Step 5: Update handleCreateAudits to use modal config**

Replace hardcoded date computation with values from `auditConfig` state. Close modal on submit.

**Step 6: Commit**

```bash
git add connect_labs/workflow/templates/kmc_flw_flags.py
git commit -m "feat: add audit configuration modal with date range, visits per FLW, and AI agent options"
```

---

## Task 7: Manual verification

**Step 1: Start the server and test**

```bash
inv up && python manage.py runserver
```

Navigate to the KMC FLW Flag Report workflow for opportunity 874. Verify:

1. **Weight gain** column shows reasonable values (10-30 g/day range, not 16000+)
2. **Column headers** are descriptive with tooltips
3. **NE** values have italic styling and tooltip
4. **Enrollment** column shows percentages, not Yes/No
5. **Flag count** hover shows which flags are triggered
6. **Select 2 FLWs** → click Create Audits → modal appears
7. **Configure dates** and click Create → only selected FLWs get audits
8. **Mortality rate** uses total cases denominator

**Step 2: Run E2E test**

```bash
pytest connect_labs/workflow/tests/e2e/test_flw_flags_workflow.py -v --ds=config.settings.local -o "addopts=" --opportunity-id=874
```

**Step 3: Final commit if any adjustments needed**
