# Bulk Image Audit Workflow Template — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Bulk Image Audit" workflow template with inline photo review and per-FLW pass/fail summary; rename the existing Weekly Audit template to "Weekly KMC Audit with AI Review".

**Architecture:** Single new file `connect_labs/workflow/templates/bulk_image_audit.py` mirrors `audit_with_ai_review.py` exactly in structure — a Python module exporting a `TEMPLATE` dict whose `render_code` string is a self-contained React component. The component calls only pre-existing audit API endpoints. State is managed in `run.data.state` and drives which of four phases (config / creating / reviewing / completed) is shown.

**Tech Stack:** Python (Django), React (JSX string in Python), Tailwind CSS, Font Awesome icons. All existing audit API endpoints at `/audit/api/...`. No new backend code.

**Design doc:** `docs/plans/2026-02-25-bulk-image-audit-design.md`

---

## Background: How Workflow Templates Work

Before touching any code, read these files once:

- `connect_labs/workflow/templates/__init__.py` — auto-discovers any `.py` file in this directory that exports `TEMPLATE`. Also has explicit re-exports at the bottom (`from . import audit_with_ai_review, ...`) that must be updated when adding a new template.
- `connect_labs/workflow/templates/audit_with_ai_review.py` — the template you will borrow heavily from. Its `RENDER_CODE` string is a React JSX component (no transpilation — the workflow `run.html` loads Babel standalone in-browser).

**Template anatomy (required keys in `TEMPLATE` dict):**
```python
TEMPLATE = {
    "key": str,           # e.g. "bulk_image_audit" — must match templateType in DEFINITION
    "name": str,          # shown in UI
    "description": str,
    "icon": str,          # Font Awesome class e.g. "fa-images"
    "color": str,         # Tailwind name e.g. "blue"
    "definition": DEFINITION,    # dict with name, description, version, templateType, statuses, config, pipeline_sources
    "render_code": RENDER_CODE,  # JSX string
    "pipeline_schema": None,     # None for this template
}
```

**Props injected into the React component:**
```javascript
function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState })
```
- `instance.state` — the run's persisted state dict (read this for initial values)
- `instance.id` — the workflow run ID
- `onUpdateState(patch)` — async PATCH to merge patch into `run.data.state`
- `actions.createAudit(payload)` — POST to `/audit/api/audit/create-async/`; returns `{success, task_id}`
- `actions.streamAuditProgress(task_id, onProgress, onComplete, onError)` — subscribes to SSE; returns cleanup fn
- `actions.cancelAudit(task_id)` — POST to cancel endpoint; returns `{success}`

**CSRF for direct fetch calls:**
```javascript
function getCsrfToken() {
    return document.cookie.split('; ')
        .find(row => row.startsWith('csrftoken='))
        ?.split('=')[1] || '';
}
```

---

## Task 1: Rename Existing Template

**Files:**
- Modify: `connect_labs/workflow/templates/audit_with_ai_review.py` (lines 13–14 and 989–991)

**Step 1: Open the file and make four string changes**

In `DEFINITION` (near top):
```python
# Before:
"name": "Weekly Audit with AI Review",
"description": "Create weekly audit sessions per FLW with optional AI image validation",

# After:
"name": "Weekly KMC Audit with AI Review",
"description": "Create weekly KMC audit sessions per FLW with optional AI image validation",
```

In `TEMPLATE` (near bottom):
```python
# Before:
"name": "Weekly Audit with AI Review",
"description": "Create weekly audit sessions per FLW with AI image validation",

# After:
"name": "Weekly KMC Audit with AI Review",
"description": "Create weekly KMC audit sessions per FLW with AI image validation",
```

**Step 2: Verify**

```bash
cd /mnt/c/Users/Mathew\ Theis/Documents/Connect/commcare-connect-labs
python manage.py shell -c "
from connect_labs.workflow.templates import get_template
t = get_template('audit_with_ai_review')
print(t['name'])
print(t['definition']['name'])
"
```
Expected output:
```
Weekly KMC Audit with AI Review
Weekly KMC Audit with AI Review
```

**Step 3: Commit**
```bash
git add connect_labs/workflow/templates/audit_with_ai_review.py
git commit -m "feat: rename Weekly Audit template to Weekly KMC Audit with AI Review"
```

---

## Task 2: Create Template Skeleton + Register It

**Files:**
- Create: `connect_labs/workflow/templates/bulk_image_audit.py`
- Modify: `connect_labs/workflow/templates/__init__.py` (last ~12 lines)

**Step 1: Create the skeleton file**

```python
"""
Bulk Image Audit Workflow Template.

Multi-opportunity image review with per-FLW pass/fail summary.
Supports Scale Photo, ORS Photo, and MUAC Photo image types.
"""

DEFINITION = {
    "name": "Bulk Image Audit",
    "description": "Review photos across multiple opportunities with per-FLW pass/fail tracking",
    "version": 1,
    "templateType": "bulk_image_audit",
    "statuses": [
        {"id": "config", "label": "Configuring", "color": "gray"},
        {"id": "creating", "label": "Creating Review", "color": "blue"},
        {"id": "reviewing", "label": "In Review", "color": "yellow"},
        {"id": "completed", "label": "Completed", "color": "green"},
        {"id": "failed", "label": "Failed", "color": "red"},
    ],
    "config": {
        "showSummaryCards": True,
    },
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    return <div className="p-6"><p className="text-gray-500">Loading...</p></div>;
}"""

TEMPLATE = {
    "key": "bulk_image_audit",
    "name": "Bulk Image Audit",
    "description": "Review photos across multiple opportunities with per-FLW pass/fail tracking",
    "icon": "fa-images",
    "color": "blue",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,
}
```

**Step 2: Update `__init__.py` re-exports** (the last ~12 lines of the file)

Change:
```python
from . import audit_with_ai_review, ocs_outreach, performance_review  # noqa: E402

__all__ = [
    "TEMPLATES",
    "get_template",
    "list_templates",
    "create_workflow_from_template",
    # Individual template modules
    "performance_review",
    "ocs_outreach",
    "audit_with_ai_review",
]
```

To:
```python
from . import audit_with_ai_review, bulk_image_audit, ocs_outreach, performance_review  # noqa: E402

__all__ = [
    "TEMPLATES",
    "get_template",
    "list_templates",
    "create_workflow_from_template",
    # Individual template modules
    "performance_review",
    "ocs_outreach",
    "audit_with_ai_review",
    "bulk_image_audit",
]
```

**Step 3: Verify auto-discovery**

```bash
python manage.py shell -c "
from connect_labs.workflow.templates import get_template, list_templates
t = get_template('bulk_image_audit')
print('key:', t['key'])
print('name:', t['name'])
all_keys = [x['key'] for x in list_templates()]
print('all templates:', all_keys)
"
```
Expected: `key: bulk_image_audit`, `name: Bulk Image Audit` and `bulk_image_audit` appears in the list.

**Step 4: Commit**
```bash
git add connect_labs/workflow/templates/bulk_image_audit.py \
        connect_labs/workflow/templates/__init__.py
git commit -m "feat: add Bulk Image Audit workflow template skeleton"
```

---

## Task 3: React — Constants, State, and Phase Router

Replace the placeholder `RENDER_CODE` string with the real component. Build it incrementally; each task adds to the same file.

**Step 1: Replace `RENDER_CODE` with the outer component structure**

```python
RENDER_CODE = """function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {

    // ── Image type map ──────────────────────────────────────────────────────
    const IMAGE_TYPES = [
        {
            id: 'scale_photo',
            label: 'Scale Photo',
            path: 'anthropometric/upload_weight_image',
            icon: 'fa-weight-scale',
        },
        {
            id: 'ors_photo',
            label: 'ORS Photo',
            path: 'service_delivery/ors_group/ors_photo',
            icon: 'fa-droplet',
        },
        {
            id: 'muac_photo',
            label: 'MUAC Photo',
            path: 'service_delivery/muac_group/muac_display_1/muac_photo',
            icon: 'fa-ruler',
        },
    ];

    // ── Phase (drives which section renders) ────────────────────────────────
    const [phase, setPhase] = React.useState(instance.state?.phase || 'config');

    // ── CSRF helper ─────────────────────────────────────────────────────────
    function getCsrfToken() {
        return document.cookie.split('; ')
            .find(row => row.startsWith('csrftoken='))
            ?.split('=')[1] || '';
    }

    // ── Phase router ────────────────────────────────────────────────────────
    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
            </div>
            {phase === 'config' && <ConfigPhase />}
            {phase === 'creating' && <CreatingPhase />}
            {phase === 'reviewing' && <ReviewPhase />}
            {phase === 'completed' && <CompletedPhase />}
        </div>
    );
}"""
```

Note: We will define `ConfigPhase`, `CreatingPhase`, `ReviewPhase`, `CompletedPhase` as inner helper components inside the outer `function WorkflowUI` block. Each task fills one of them in.

**Step 2: Verify Python parses without errors**
```bash
python -c "import connect_labs.workflow.templates.bulk_image_audit"
```
Expected: no output (clean import).

---

## Task 4: Config Phase — Opportunity Selector and Image Type

This and the next task build up the `ConfigPhase` inner component. Replace the `<ConfigPhase />` placeholder JSX and add the component definition inside `WorkflowUI`.

**All code from here goes inside `function WorkflowUI(...)` above the `return` statement.**

**Step 1: Add config state**

```javascript
// ── Config state ────────────────────────────────────────────────────────────
const [selectedOpps, setSelectedOpps] = React.useState(
    instance.state?.config?.selected_opps || []
);  // [{id, name}]
const [searchQuery, setSearchQuery] = React.useState('');
const [searchResults, setSearchResults] = React.useState([]);
const [isSearching, setIsSearching] = React.useState(false);
const [imageType, setImageType] = React.useState(
    instance.state?.config?.image_type || 'scale_photo'
);
const [auditMode, setAuditMode] = React.useState(
    instance.state?.config?.audit_mode || 'date_range'
);
const [startDate, setStartDate] = React.useState(
    instance.state?.config?.start_date || ''
);
const [endDate, setEndDate] = React.useState(
    instance.state?.config?.end_date || ''
);
const [datePreset, setDatePreset] = React.useState('last_week');
const [lastNCount, setLastNCount] = React.useState(
    instance.state?.config?.count_per_opp || 10
);
const [samplePct, setSamplePct] = React.useState(
    instance.state?.config?.sample_percentage ?? 100
);
const [threshold, setThreshold] = React.useState(
    instance.state?.config?.threshold ?? 80
);
```

**Step 2: Add opportunity search function**

```javascript
// ── Opp search ───────────────────────────────────────────────────────────────
const searchTimeout = React.useRef(null);

const handleOppSearch = (query) => {
    setSearchQuery(query);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (!query.trim()) { setSearchResults([]); return; }

    searchTimeout.current = setTimeout(() => {
        setIsSearching(true);
        fetch('/audit/api/opportunities/search/?q=' + encodeURIComponent(query))
            .then(r => r.json())
            .then(data => {
                setSearchResults(data.opportunities || []);
                setIsSearching(false);
            })
            .catch(() => setIsSearching(false));
    }, 300);
};

const addOpp = (opp) => {
    if (!selectedOpps.find(o => o.id === opp.id)) {
        setSelectedOpps(prev => [...prev, { id: opp.id, name: opp.name }]);
    }
    setSearchQuery('');
    setSearchResults([]);
};

const removeOpp = (id) => {
    setSelectedOpps(prev => prev.filter(o => o.id !== id));
};
```

**Step 3: Add `ConfigPhase` inner component** (still inside `WorkflowUI`, above the outer `return`):

```javascript
// ── Inner component: Config ──────────────────────────────────────────────────
const ConfigPhase = () => (
    <div className="bg-white rounded-lg shadow-sm p-6 space-y-6">

        {/* Opportunity selector */}
        <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">
                <i className="fa-solid fa-building mr-2 text-gray-400"></i>
                Opportunities
            </h3>
            <div className="relative">
                <input
                    type="text"
                    value={searchQuery}
                    onChange={e => handleOppSearch(e.target.value)}
                    placeholder="Search opportunities..."
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                />
                {isSearching && (
                    <div className="absolute right-3 top-2.5">
                        <i className="fa-solid fa-spinner fa-spin text-gray-400"></i>
                    </div>
                )}
                {searchResults.length > 0 && (
                    <div className={'absolute z-10 w-full bg-white border border-gray-200 ' +
                        'rounded-lg shadow-lg mt-1 max-h-48 overflow-y-auto'}>
                        {searchResults.map(opp => (
                            <button
                                key={opp.id}
                                onClick={() => addOpp(opp)}
                                className={'w-full text-left px-4 py-2 text-sm hover:bg-blue-50 ' +
                                    'flex items-center justify-between'}
                            >
                                <span className="font-medium text-gray-900">{opp.name}</span>
                                <span className="text-xs text-gray-400 ml-2">ID {opp.id}</span>
                            </button>
                        ))}
                    </div>
                )}
            </div>
            {selectedOpps.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-3">
                    {selectedOpps.map(opp => (
                        <span key={opp.id}
                            className={'inline-flex items-center gap-1.5 px-3 py-1 bg-blue-100 ' +
                                'text-blue-800 rounded-full text-sm font-medium'}>
                            {opp.name}
                            <button onClick={() => removeOpp(opp.id)}
                                className="text-blue-500 hover:text-blue-800 ml-1">
                                <i className="fa-solid fa-times text-xs"></i>
                            </button>
                        </span>
                    ))}
                </div>
            )}
        </div>

        {/* Image type */}
        <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">
                <i className="fa-solid fa-image mr-2 text-gray-400"></i>
                Image Type
            </h3>
            <div className="flex gap-2">
                {IMAGE_TYPES.map(t => (
                    <button key={t.id} onClick={() => setImageType(t.id)}
                        className={
                            'flex-1 px-4 py-3 text-sm rounded-lg border-2 transition-colors ' +
                            (imageType === t.id
                                ? 'bg-blue-50 text-blue-700 border-blue-500'
                                : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300')
                        }>
                        <i className={'fa-solid ' + t.icon + ' mr-2'}></i>
                        {t.label}
                    </button>
                ))}
            </div>
        </div>

        {/* Visit Selection — identical to audit_with_ai_review.py */}
        <VisitSelectionSection />

        {/* Sampling */}
        <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">
                <i className="fa-solid fa-percent mr-2 text-gray-400"></i>
                Sampling
            </h3>
            <div className="p-4 bg-gray-50 rounded-lg border border-gray-200 flex items-center gap-3">
                <label className="text-sm text-gray-700">Sample</label>
                <input type="number" min="1" max="100" value={samplePct}
                    onChange={e => setSamplePct(parseInt(e.target.value) || 100)}
                    className="w-20 border border-gray-300 rounded px-3 py-2 text-sm text-center" />
                <label className="text-sm text-gray-700">% of matching visits</label>
            </div>
        </div>

        {/* Passing threshold */}
        <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">
                <i className="fa-solid fa-gauge mr-2 text-gray-400"></i>
                Passing Threshold
            </h3>
            <div className="p-4 bg-gray-50 rounded-lg border border-gray-200 flex items-center gap-3">
                <label className="text-sm text-gray-700">Mark FLW as passing if</label>
                <input type="number" min="1" max="100" value={threshold}
                    onChange={e => setThreshold(parseInt(e.target.value) || 80)}
                    className="w-20 border border-gray-300 rounded px-3 py-2 text-sm text-center" />
                <label className="text-sm text-gray-700">% or more of their photos pass</label>
            </div>
        </div>

        {/* Submit */}
        <div className="pt-4 border-t border-gray-200">
            <button
                onClick={handleCreate}
                disabled={selectedOpps.length === 0}
                className={'inline-flex items-center px-6 py-3 bg-blue-600 text-white ' +
                    'rounded-lg hover:bg-blue-700 disabled:bg-gray-400 font-medium'}
            >
                <i className="fa-solid fa-play mr-2"></i>
                Create Review
            </button>
            {selectedOpps.length === 0 && (
                <p className="mt-2 text-sm text-red-600">
                    Select at least one opportunity to continue.
                </p>
            )}
        </div>
    </div>
);
```

**Step 4: Add `VisitSelectionSection` inner component** — copy verbatim from `audit_with_ai_review.py` (lines 719–828), but wrap it as an inner component:

```javascript
const VisitSelectionSection = () => (
    <div>
        <h3 className="text-sm font-medium text-gray-700 mb-3">
            <i className="fa-solid fa-sliders mr-2 text-gray-400"></i>
            Visit Selection
        </h3>
        <div className="flex gap-2 mb-4">
            <button onClick={() => setAuditMode('date_range')}
                className={
                    'flex-1 px-4 py-3 text-sm rounded-lg border-2 transition-colors ' +
                    (auditMode === 'date_range'
                        ? 'bg-blue-50 text-blue-700 border-blue-500'
                        : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300')
                }>
                <i className="fa-solid fa-calendar mr-2"></i>Date Range
            </button>
            <button onClick={() => setAuditMode('last_n_per_opp')}
                className={
                    'flex-1 px-4 py-3 text-sm rounded-lg border-2 transition-colors ' +
                    (auditMode === 'last_n_per_opp'
                        ? 'bg-blue-50 text-blue-700 border-blue-500'
                        : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300')
                }>
                <i className="fa-solid fa-list-ol mr-2"></i>Last N Visits
            </button>
        </div>
        {auditMode === 'date_range' && (
            <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
                <div className="flex flex-wrap gap-2 mb-3">
                    {[
                        { id: 'last_week', label: 'Last Week' },
                        { id: 'last_7_days', label: 'Last 7 Days' },
                        { id: 'last_14_days', label: 'Last 14 Days' },
                        { id: 'last_30_days', label: 'Last 30 Days' },
                        { id: 'this_month', label: 'This Month' },
                        { id: 'last_month', label: 'Last Month' },
                        { id: 'custom', label: 'Custom' },
                    ].map(p => (
                        <button key={p.id} onClick={() => applyPreset(p.id)}
                            className={
                                'px-3 py-1.5 text-sm rounded-full border transition-colors ' +
                                (datePreset === p.id
                                    ? 'bg-blue-600 text-white border-blue-600'
                                    : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400')
                            }>
                            {p.label}
                        </button>
                    ))}
                </div>
                <div className="flex gap-4 items-center">
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Start</label>
                        <input type="date" value={startDate}
                            onChange={e => { setStartDate(e.target.value); setDatePreset('custom'); }}
                            className="border border-gray-300 rounded px-3 py-2 text-sm" />
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">End</label>
                        <input type="date" value={endDate}
                            onChange={e => { setEndDate(e.target.value); setDatePreset('custom'); }}
                            className="border border-gray-300 rounded px-3 py-2 text-sm" />
                    </div>
                </div>
            </div>
        )}
        {auditMode === 'last_n_per_opp' && (
            <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
                <div className="flex items-center gap-3">
                    <label className="text-sm text-gray-700">Get the last</label>
                    <input type="number" min="1" max="1000" value={lastNCount}
                        onChange={e => setLastNCount(parseInt(e.target.value) || 10)}
                        className="w-20 border border-gray-300 rounded px-3 py-2 text-sm text-center" />
                    <label className="text-sm text-gray-700">visits per opportunity</label>
                </div>
            </div>
        )}
    </div>
);
```

**Step 5: Add date helper functions** (copy from `audit_with_ai_review.py` lines 152–225):

```javascript
const calculateDateRange = (preset) => {
    const today = new Date(); today.setHours(0,0,0,0);
    let start, end;
    switch (preset) {
        case 'last_week': {
            const dow = today.getDay();
            const daysToMon = dow === 0 ? 6 : dow - 1;
            const thisMon = new Date(today); thisMon.setDate(today.getDate() - daysToMon);
            start = new Date(thisMon); start.setDate(thisMon.getDate() - 7);
            end = new Date(start); end.setDate(start.getDate() + 6);
            break;
        }
        case 'last_7_days':
            end = new Date(today); end.setDate(today.getDate() - 1);
            start = new Date(end); start.setDate(end.getDate() - 6); break;
        case 'last_14_days':
            end = new Date(today); end.setDate(today.getDate() - 1);
            start = new Date(end); start.setDate(end.getDate() - 13); break;
        case 'last_30_days':
            end = new Date(today); end.setDate(today.getDate() - 1);
            start = new Date(end); start.setDate(end.getDate() - 29); break;
        case 'this_month':
            start = new Date(today.getFullYear(), today.getMonth(), 1);
            end = new Date(today); end.setDate(today.getDate() - 1); break;
        case 'last_month':
            start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
            end = new Date(today.getFullYear(), today.getMonth(), 0); break;
        default: return null;
    }
    return { start: start.toISOString().split('T')[0], end: end.toISOString().split('T')[0] };
};

const applyPreset = (preset) => {
    setDatePreset(preset);
    if (preset !== 'custom') {
        const range = calculateDateRange(preset);
        if (range) { setStartDate(range.start); setEndDate(range.end); }
    }
};
```

**Step 6: Set default date range on mount**

```javascript
React.useEffect(() => {
    if (!startDate && !endDate) applyPreset('last_week');
}, []);
```

**Step 7: Verify**

```bash
python -c "import connect_labs.workflow.templates.bulk_image_audit"
```
Expected: clean import (no syntax errors).

**Step 8: Commit**
```bash
git add connect_labs/workflow/templates/bulk_image_audit.py
git commit -m "feat: bulk image audit config form — opp selector, image type, visit selection"
```

---

## Task 5: Config Phase — `handleCreate` and Transition to Creating

**Step 1: Add `handleCreate` function** (still inside `WorkflowUI`):

```javascript
// ── Execution state (shared between creating/reviewing phases) ───────────────
const [isRunning, setIsRunning] = React.useState(false);
const [isCancelling, setIsCancelling] = React.useState(false);
const [progress, setProgress] = React.useState(null);
const [taskId, setTaskId] = React.useState(null);
const [sessionId, setSessionId] = React.useState(instance.state?.session_id || null);
const cleanupRef = React.useRef(null);

React.useEffect(() => {
    return () => { if (cleanupRef.current) cleanupRef.current(); };
}, []);

// Reconnect to running job on page load (matches audit_with_ai_review.py pattern)
React.useEffect(() => {
    try {
        const activeJob = instance.state?.active_job;
        if (!actions?.streamAuditProgress) return;
        if (activeJob?.status === 'running' && activeJob?.job_id) {
            setIsRunning(true);
            setTaskId(activeJob.job_id);
            setProgress({ status: 'running', stage_name: activeJob.stage_name || 'Processing',
                processed: activeJob.processed || 0, total: activeJob.total || 0 });
            const cleanup = actions.streamAuditProgress(
                activeJob.job_id,
                (p) => setProgress(p),
                (final) => {
                    setIsRunning(false);
                    setProgress({ status: 'completed', ...final });
                    const sid = final?.session_id || final?.sessions?.[0]?.id;
                    if (sid) {
                        setSessionId(sid);
                        setPhase('reviewing');
                        onUpdateState({ phase: 'reviewing', session_id: sid,
                            active_job: { job_id: activeJob.job_id, status: 'completed',
                                completed_at: new Date().toISOString() } }).catch(() => {});
                    }
                },
                (err) => {
                    setIsRunning(false);
                    setProgress({ status: 'failed', error: err });
                    onUpdateState({ active_job: { job_id: activeJob.job_id, status: 'failed', error: err } }).catch(() => {});
                }
            );
            cleanupRef.current = cleanup;
            return () => { if (cleanup) cleanup(); };
        }
    } catch (err) {
        console.error('[BulkImageAudit] Reconnect error:', err);
    }
}, []);

const handleCreate = async () => {
    if (selectedOpps.length === 0) return;

    const imageTypeObj = IMAGE_TYPES.find(t => t.id === imageType);
    const config = {
        selected_opps: selectedOpps,
        image_type: imageType,
        image_path: imageTypeObj.path,
        audit_mode: auditMode,
        start_date: auditMode === 'date_range' ? startDate : null,
        end_date: auditMode === 'date_range' ? endDate : null,
        count_per_opp: auditMode === 'last_n_per_opp' ? lastNCount : null,
        sample_percentage: samplePct,
        threshold: threshold,
    };

    setIsRunning(true);
    setProgress({ status: 'starting', message: 'Initializing...' });
    setPhase('creating');

    await onUpdateState({ phase: 'creating', config });

    const criteria = {
        audit_type: auditMode,
        start_date: auditMode === 'date_range' ? startDate : null,
        end_date: auditMode === 'date_range' ? endDate : null,
        count_per_opp: auditMode === 'last_n_per_opp' ? lastNCount : null,
        sample_percentage: samplePct,
        related_fields: [{ image_path: imageTypeObj.path, filter_by_image: true }],
    };

    try {
        const result = await actions.createAudit({
            opportunities: selectedOpps,
            criteria,
            workflow_run_id: instance.id,
        });

        if (result.success && result.task_id) {
            setTaskId(result.task_id);
            onUpdateState({ active_job: { job_id: result.task_id, status: 'running',
                started_at: new Date().toISOString() } }).catch(() => {});

            const cleanup = actions.streamAuditProgress(
                result.task_id,
                (p) => setProgress(p),
                (final) => {
                    setIsRunning(false);
                    setProgress({ status: 'completed', ...final });
                    // The audit creation returns sessions; grab the first session id
                    const sid = final?.session_id || final?.sessions?.[0]?.id;
                    if (sid) {
                        setSessionId(sid);
                        setPhase('reviewing');
                        onUpdateState({ phase: 'reviewing', session_id: sid,
                            active_job: { job_id: result.task_id, status: 'completed',
                                completed_at: new Date().toISOString() } }).catch(() => {});
                    } else {
                        setPhase('config');
                        onUpdateState({ phase: 'config' }).catch(() => {});
                    }
                },
                (err) => {
                    setIsRunning(false);
                    setProgress({ status: 'failed', error: err });
                    setPhase('config');
                    onUpdateState({ phase: 'config',
                        active_job: { job_id: result.task_id, status: 'failed', error: err } }).catch(() => {});
                }
            );
            cleanupRef.current = cleanup;
        } else {
            setIsRunning(false);
            setProgress({ status: 'failed', error: result.error || 'Failed to start' });
            setPhase('config');
            onUpdateState({ phase: 'config' }).catch(() => {});
        }
    } catch (err) {
        setIsRunning(false);
        setProgress({ status: 'failed', error: err.message });
        setPhase('config');
        onUpdateState({ phase: 'config' }).catch(() => {});
    }
};
```

**Step 2: Add cancel handler**

```javascript
const handleCancel = async () => {
    if (!taskId || isCancelling) return;
    setIsCancelling(true);
    if (cleanupRef.current) { cleanupRef.current(); cleanupRef.current = null; }
    setIsRunning(false);
    try {
        await actions.cancelAudit(taskId);
    } catch (e) { /* ignore */ }
    setProgress({ status: 'cancelled', message: 'Audit creation cancelled' });
    setPhase('config');
    await onUpdateState({ phase: 'config' });
    setIsCancelling(false);
};
```

**Step 3: Add `CreatingPhase` inner component** (copy progress bar from `audit_with_ai_review.py` lines 926–981):

```javascript
const CreatingPhase = () => (
    <div className="space-y-4">
        {isRunning && progress && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                        <i className="fa-solid fa-spinner fa-spin text-blue-600"></i>
                        <span className="font-medium text-blue-800">
                            {progress.stage_name || 'Processing...'}
                        </span>
                    </div>
                    <div className="flex items-center gap-3">
                        {progress.current_stage && progress.total_stages && (
                            <span className="text-sm text-blue-600">
                                Stage {progress.current_stage}/{progress.total_stages}
                            </span>
                        )}
                        <button onClick={handleCancel} disabled={isCancelling}
                            className={'px-3 py-1 text-sm text-red-600 hover:text-red-800 ' +
                                'hover:bg-red-100 rounded transition-colors disabled:opacity-50'}>
                            <i className="fa-solid fa-times mr-1"></i>Cancel
                        </button>
                    </div>
                </div>
                {progress.total > 0 && (
                    <div className="w-full bg-blue-200 rounded-full h-2">
                        <div className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                            style={{ width: (progress.processed / progress.total * 100) + '%' }}>
                        </div>
                    </div>
                )}
                <div className="mt-2 text-sm text-blue-700">{progress.message}</div>
            </div>
        )}
        {progress?.status === 'failed' && !isRunning && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                <div className="flex items-center gap-2 text-red-800">
                    <i className="fa-solid fa-circle-exclamation"></i>
                    <span className="font-medium">Error: {progress.error}</span>
                </div>
                <button onClick={() => setPhase('config')}
                    className="mt-3 text-sm text-blue-600 hover:underline">
                    ← Back to configuration
                </button>
            </div>
        )}
        {progress?.status === 'cancelled' && !isRunning && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                <div className="flex items-center gap-2 text-amber-800">
                    <i className="fa-solid fa-ban"></i>
                    <span className="font-medium">Audit creation was cancelled</span>
                </div>
                <button onClick={() => setPhase('config')}
                    className="mt-3 text-sm text-blue-600 hover:underline">
                    ← Back to configuration
                </button>
            </div>
        )}
    </div>
);
```

**Step 4: Verify**

```bash
python -c "import connect_labs.workflow.templates.bulk_image_audit"
```

**Step 5: Commit**
```bash
git add connect_labs/workflow/templates/bulk_image_audit.py
git commit -m "feat: bulk image audit config submit + creating phase with progress"
```

---

## Task 6: Review Phase — Fetch Assessments, Stats Bar, Photo List

**Step 1: Add review state** (inside `WorkflowUI`):

```javascript
// ── Review phase state ───────────────────────────────────────────────────────
const [assessments, setAssessments] = React.useState([]);
const [loadingReview, setLoadingReview] = React.useState(false);
const [reviewError, setReviewError] = React.useState(null);
const [isSaving, setIsSaving] = React.useState(false);
const [aiRunning, setAiRunning] = React.useState({});  // {blob_id: true}

// Load assessment data when entering review phase
React.useEffect(() => {
    if (phase !== 'reviewing' || !sessionId) return;
    setLoadingReview(true);
    setReviewError(null);
    fetch('/audit/api/' + sessionId + '/bulk-data/')
        .then(r => r.json())
        .then(data => {
            setAssessments(data.assessments || []);
            setLoadingReview(false);
        })
        .catch(err => {
            setReviewError(err.message);
            setLoadingReview(false);
        });
}, [phase, sessionId]);

// Update a single assessment field locally
const updateAssessment = (id, patch) => {
    setAssessments(prev => prev.map(a => a.id === id ? { ...a, ...patch } : a));
};

// Save progress to backend
const saveProgress = async (updatedAssessments) => {
    if (!sessionId) return;
    const visitResults = buildVisitResults(updatedAssessments || assessments);
    const fd = new FormData();
    fd.append('visit_results', JSON.stringify(visitResults));
    setIsSaving(true);
    try {
        await fetch('/audit/api/' + sessionId + '/save/', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCsrfToken() },
            body: fd,
        });
    } finally {
        setIsSaving(false);
    }
};

// Build visit_results structure expected by backend
const buildVisitResults = (asmnts) => {
    const vr = {};
    for (const a of asmnts) {
        const vid = String(a.visit_id);
        if (!vr[vid]) vr[vid] = { assessments: {} };
        vr[vid].assessments[a.blob_id] = {
            question_id: a.question_id,
            result: a.result || '',
            notes: a.notes || '',
            ai_result: a.ai_result || '',
            ai_notes: a.ai_notes || '',
        };
    }
    return vr;
};

// Handle pass/fail button click
const handleAssessResult = async (id, result) => {
    const updated = assessments.map(a =>
        a.id === id ? { ...a, result, status: result } : a
    );
    setAssessments(updated);
    await saveProgress(updated);
};

// Handle notes change (debounced save)
const notesTimeout = React.useRef(null);
const handleNotesChange = (id, notes) => {
    updateAssessment(id, { notes });
    if (notesTimeout.current) clearTimeout(notesTimeout.current);
    notesTimeout.current = setTimeout(() => saveProgress(), 800);
};
```

**Step 2: Add `ReviewPhase` inner component — stats bar + photo list**:

```javascript
const ReviewPhase = () => {
    const config = instance.state?.config || {};
    const threshold = config.threshold ?? 80;
    const imageType = config.image_type || 'scale_photo';
    const isScalePhoto = imageType === 'scale_photo';

    const total = assessments.length;
    const passed = assessments.filter(a => a.status === 'pass').length;
    const failed = assessments.filter(a => a.status === 'fail').length;
    const pending = total - passed - failed;

    if (loadingReview) return (
        <div className="bg-white rounded-lg shadow-sm p-12 text-center">
            <i className="fa-solid fa-spinner fa-spin text-gray-400 text-3xl mb-3"></i>
            <p className="text-gray-500">Loading photos...</p>
        </div>
    );
    if (reviewError) return (
        <div className="bg-red-50 border border-red-200 rounded-lg p-6">
            <p className="text-red-700">Error loading review data: {reviewError}</p>
        </div>
    );

    return (
        <div className="space-y-6">
            {/* Stats bar */}
            <div className="grid grid-cols-4 gap-4">
                {[
                    { label: 'Total', value: total, color: 'blue', icon: 'fa-images' },
                    { label: 'Pending', value: pending, color: 'yellow', icon: 'fa-clock' },
                    { label: 'Passed', value: passed, color: 'green', icon: 'fa-check' },
                    { label: 'Failed', value: failed, color: 'red', icon: 'fa-times' },
                ].map(card => (
                    <div key={card.label}
                        className={'bg-white rounded-lg shadow-sm p-4 border-l-4 ' +
                            `border-${card.color}-500`}>
                        <div className={`text-2xl font-bold text-${card.color}-600`}>
                            {card.value}
                        </div>
                        <div className="text-sm text-gray-500 flex items-center gap-1 mt-1">
                            <i className={`fa-solid ${card.icon} text-xs`}></i>
                            {card.label}
                        </div>
                    </div>
                ))}
            </div>

            {/* Save indicator */}
            {isSaving && (
                <div className="text-sm text-gray-400 flex items-center gap-1">
                    <i className="fa-solid fa-spinner fa-spin text-xs"></i> Saving...
                </div>
            )}

            {/* Photo list */}
            <div className="space-y-3">
                {assessments.map(a => (
                    <PhotoCard key={a.id} assessment={a} isScalePhoto={isScalePhoto} />
                ))}
                {assessments.length === 0 && (
                    <div className="bg-white rounded-lg shadow-sm p-8 text-center text-gray-500">
                        No photos found for the selected criteria.
                    </div>
                )}
            </div>

            {/* FLW Summary Table */}
            <FlwSummaryTable assessments={assessments} threshold={threshold}
                config={config} readOnly={false} />

            {/* Complete Image Review */}
            <CompleteSection assessments={assessments} />
        </div>
    );
};
```

**Step 3: Add `PhotoCard` inner component**:

```javascript
const PhotoCard = ({ assessment: a, isScalePhoto }) => {
    const [expanded, setExpanded] = React.useState(false);
    const isAiRunning = aiRunning[a.blob_id];

    const handleAiReview = async () => {
        if (!isScalePhoto || !sessionId) return;
        setAiRunning(prev => ({ ...prev, [a.blob_id]: true }));
        try {
            const fd = new FormData();
            fd.append('blob_id', a.blob_id);
            fd.append('visit_id', a.visit_id);
            const res = await fetch('/audit/api/' + sessionId + '/ai-review/', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCsrfToken() },
                body: fd,
            });
            const data = await res.json();
            if (data.success) {
                updateAssessment(a.id, {
                    ai_result: data.result || '',
                    ai_notes: data.notes || '',
                });
            }
        } finally {
            setAiRunning(prev => ({ ...prev, [a.blob_id]: false }));
        }
    };

    return (
        <div className={'bg-white rounded-lg shadow-sm border ' +
            (a.status === 'pass' ? 'border-green-200' :
             a.status === 'fail' ? 'border-red-200' : 'border-gray-200')}>
            <div className="p-4 flex gap-4">
                {/* Thumbnail */}
                <div className="flex-shrink-0 w-24 h-24 bg-gray-100 rounded overflow-hidden cursor-pointer"
                    onClick={() => setExpanded(!expanded)}>
                    <img src={a.image_url} alt="assessment"
                        className="w-full h-full object-cover"
                        loading="lazy" />
                </div>
                {/* Meta */}
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-medium text-gray-900 truncate">
                            {a.entity_name || 'Unknown'}
                        </span>
                        <span className="text-xs text-gray-400">{a.visit_date}</span>
                    </div>
                    <div className="text-xs text-gray-500 font-mono mb-2">{a.username}</div>

                    {/* Pass / Fail buttons */}
                    <div className="flex items-center gap-2">
                        <button onClick={() => handleAssessResult(a.id, 'pass')}
                            className={'px-3 py-1 text-sm rounded border transition-colors ' +
                                (a.status === 'pass'
                                    ? 'bg-green-600 text-white border-green-600'
                                    : 'bg-white text-green-700 border-green-300 hover:bg-green-50')}>
                            <i className="fa-solid fa-check mr-1"></i>Pass
                        </button>
                        <button onClick={() => handleAssessResult(a.id, 'fail')}
                            className={'px-3 py-1 text-sm rounded border transition-colors ' +
                                (a.status === 'fail'
                                    ? 'bg-red-600 text-white border-red-600'
                                    : 'bg-white text-red-700 border-red-300 hover:bg-red-50')}>
                            <i className="fa-solid fa-times mr-1"></i>Fail
                        </button>

                        {/* AI Review button — enabled for scale only */}
                        <button
                            onClick={handleAiReview}
                            disabled={!isScalePhoto || isAiRunning}
                            title={!isScalePhoto ? 'AI review is only available for Scale Photos' : 'Run AI review'}
                            className={'px-3 py-1 text-sm rounded border transition-colors ' +
                                (isScalePhoto
                                    ? 'bg-purple-50 text-purple-700 border-purple-300 hover:bg-purple-100'
                                    : 'bg-gray-50 text-gray-400 border-gray-200 cursor-not-allowed opacity-50')}>
                            {isAiRunning
                                ? <i className="fa-solid fa-spinner fa-spin mr-1"></i>
                                : <i className="fa-solid fa-robot mr-1"></i>}
                            AI Review
                        </button>
                    </div>

                    {/* AI result badge */}
                    {a.ai_result && (
                        <div className={'mt-2 text-xs px-2 py-1 rounded inline-flex items-center gap-1 ' +
                            (a.ai_result === 'match'
                                ? 'bg-green-50 text-green-700'
                                : a.ai_result === 'no_match'
                                ? 'bg-red-50 text-red-700'
                                : 'bg-yellow-50 text-yellow-700')}>
                            <i className="fa-solid fa-robot"></i>
                            AI: {a.ai_result} {a.ai_notes ? '— ' + a.ai_notes : ''}
                        </div>
                    )}

                    {/* Notes */}
                    <input
                        type="text"
                        value={a.notes || ''}
                        onChange={e => handleNotesChange(a.id, e.target.value)}
                        placeholder="Add note..."
                        className="mt-2 w-full text-sm border border-gray-200 rounded px-2 py-1 focus:ring-1 focus:ring-blue-500"
                    />
                </div>
            </div>

            {/* Expanded full image */}
            {expanded && (
                <div className="border-t border-gray-100 p-4">
                    <img src={a.image_url} alt="full assessment"
                        className="max-w-full rounded shadow-sm" />
                </div>
            )}
        </div>
    );
};
```

**Step 4: Verify**
```bash
python -c "import connect_labs.workflow.templates.bulk_image_audit"
```

**Step 5: Commit**
```bash
git add connect_labs/workflow/templates/bulk_image_audit.py
git commit -m "feat: bulk image audit review phase — stats, photo list, AI review button"
```

---

## Task 7: Review Phase — FLW Summary Table

**Step 1: Add sort/filter state** (inside `WorkflowUI`):

```javascript
// ── FLW table state ──────────────────────────────────────────────────────────
const [flwSort, setFlwSort] = React.useState({ col: 'flw_name', dir: 'asc' });
const [flwFilter, setFlwFilter] = React.useState('');
const [oppFilter, setOppFilter] = React.useState('');
const [resultFilter, setResultFilter] = React.useState('all'); // 'all'|'pass'|'fail'

const sortFlwTable = (col) => {
    setFlwSort(prev => ({
        col,
        dir: prev.col === col && prev.dir === 'asc' ? 'desc' : 'asc'
    }));
};
```

**Step 2: Add `buildFlwRows` helper** (inside `WorkflowUI`):

```javascript
const buildFlwRows = (asmnts, config) => {
    const byUser = {};
    for (const a of asmnts) {
        const key = a.username;
        if (!byUser[key]) {
            byUser[key] = {
                flw_name: a.username,
                opp_name: config?.selected_opps?.find(o => o.id === a.opportunity_id)?.name
                    || config?.selected_opps?.[0]?.name || '—',
                passed: 0,
                total: 0,
            };
        }
        byUser[key].total += 1;
        if (a.status === 'pass') byUser[key].passed += 1;
    }
    return Object.values(byUser).map(row => ({
        ...row,
        pct: row.total > 0 ? Math.round(row.passed / row.total * 100) : 0,
    }));
};
```

**Step 3: Add `FlwSummaryTable` inner component**:

```javascript
const FlwSummaryTable = ({ assessments, threshold, config, readOnly }) => {
    const rows = buildFlwRows(assessments, config);

    // Filter
    const filtered = rows.filter(r => {
        const flwMatch = r.flw_name.toLowerCase().includes(flwFilter.toLowerCase());
        const oppMatch = r.opp_name.toLowerCase().includes(oppFilter.toLowerCase());
        const passing = r.pct >= threshold;
        const resultMatch = resultFilter === 'all'
            || (resultFilter === 'pass' && passing)
            || (resultFilter === 'fail' && !passing);
        return flwMatch && oppMatch && resultMatch;
    });

    // Sort
    const sorted = [...filtered].sort((a, b) => {
        const dir = flwSort.dir === 'asc' ? 1 : -1;
        const col = flwSort.col;
        if (col === 'pct' || col === 'passed' || col === 'total') {
            return (a[col] - b[col]) * dir;
        }
        return a[col].localeCompare(b[col]) * dir;
    });

    const SortIcon = ({ col }) => (
        <i className={'fa-solid ml-1 text-xs ' + (flwSort.col === col
            ? (flwSort.dir === 'asc' ? 'fa-sort-up' : 'fa-sort-down')
            : 'fa-sort text-gray-300')}></i>
    );

    return (
        <div className="bg-white rounded-lg shadow-sm overflow-hidden">
            <div className="px-6 py-4 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
                <h2 className="text-base font-semibold text-gray-800">
                    <i className="fa-solid fa-users mr-2 text-gray-400"></i>
                    FLW Summary
                </h2>
                <span className="text-sm text-gray-500">
                    Threshold: {threshold}%
                </span>
            </div>

            {/* Filters */}
            {!readOnly && (
                <div className="px-4 py-3 border-b border-gray-100 flex gap-3 flex-wrap">
                    <input type="text" placeholder="Filter FLW name..." value={flwFilter}
                        onChange={e => setFlwFilter(e.target.value)}
                        className="text-sm border border-gray-300 rounded px-2 py-1.5 w-40" />
                    <input type="text" placeholder="Filter opp name..." value={oppFilter}
                        onChange={e => setOppFilter(e.target.value)}
                        className="text-sm border border-gray-300 rounded px-2 py-1.5 w-40" />
                    <select value={resultFilter} onChange={e => setResultFilter(e.target.value)}
                        className="text-sm border border-gray-300 rounded px-2 py-1.5">
                        <option value="all">All Results</option>
                        <option value="pass">Pass Only</option>
                        <option value="fail">Fail Only</option>
                    </select>
                </div>
            )}

            <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            {[
                                { id: 'opp_name', label: 'Opp Name' },
                                { id: 'flw_name', label: 'FLW Name' },
                                { id: 'passed', label: 'Passed / Assessments' },
                                { id: 'pct', label: '% Passed' },
                                { id: 'result', label: 'Result' },
                            ].map(col => (
                                <th key={col.id}
                                    onClick={() => col.id !== 'result' && sortFlwTable(col.id)}
                                    className={'px-4 py-3 text-left text-xs font-medium text-gray-500 ' +
                                        'uppercase tracking-wider ' +
                                        (col.id !== 'result' ? 'cursor-pointer hover:text-gray-700' : '')}>
                                    {col.label}
                                    {col.id !== 'result' && <SortIcon col={col.id} />}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {sorted.map((row, idx) => {
                            const passing = row.pct >= threshold;
                            return (
                                <tr key={idx} className="hover:bg-gray-50">
                                    <td className="px-4 py-3 text-sm text-gray-700">{row.opp_name}</td>
                                    <td className="px-4 py-3 text-sm font-medium text-gray-900">
                                        {row.flw_name}
                                    </td>
                                    <td className="px-4 py-3 text-sm text-gray-700">
                                        <span className="text-green-600 font-medium">{row.passed}</span>
                                        <span className="text-gray-400 mx-1">/</span>
                                        <span className="text-gray-700">{row.total}</span>
                                    </td>
                                    <td className="px-4 py-3 text-sm font-medium">
                                        <span className={passing ? 'text-green-700' : 'text-red-700'}>
                                            {row.pct}%
                                        </span>
                                    </td>
                                    <td className="px-4 py-3 text-center">
                                        {passing
                                            ? <i className="fa-solid fa-circle-check text-green-500 text-lg"
                                                title="Passing"></i>
                                            : <i className="fa-solid fa-circle-xmark text-red-500 text-lg"
                                                title="Failing"></i>}
                                    </td>
                                </tr>
                            );
                        })}
                        {sorted.length === 0 && (
                            <tr>
                                <td colSpan="5" className="px-4 py-8 text-center text-gray-400 text-sm">
                                    No FLWs match the current filters
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
};
```

**Step 4: Verify**
```bash
python -c "import connect_labs.workflow.templates.bulk_image_audit"
```

**Step 5: Commit**
```bash
git add connect_labs/workflow/templates/bulk_image_audit.py
git commit -m "feat: bulk image audit FLW summary table with sort and filter"
```

---

## Task 8: Review Phase — Complete Image Review

**Step 1: Add completion state** (inside `WorkflowUI`):

```javascript
// ── Completion state ─────────────────────────────────────────────────────────
const [showCompleteForm, setShowCompleteForm] = React.useState(false);
const [completionNotes, setCompletionNotes] = React.useState('');
const [isCompleting, setIsCompleting] = React.useState(false);
const [completeError, setCompleteError] = React.useState(null);

const handleComplete = async () => {
    const config = instance.state?.config || {};
    const threshold = config.threshold ?? 80;

    const flwRows = buildFlwRows(assessments, config);
    const allPassing = flwRows.every(r => r.pct >= threshold);
    const overallResult = allPassing ? 'pass' : 'fail';

    const visitResults = buildVisitResults(assessments);

    const fd = new FormData();
    fd.append('overall_result', overallResult);
    fd.append('notes', completionNotes);
    fd.append('kpi_notes', '');
    fd.append('visit_results', JSON.stringify(visitResults));

    setIsCompleting(true);
    setCompleteError(null);
    try {
        const res = await fetch('/audit/api/' + sessionId + '/complete/', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCsrfToken() },
            body: fd,
        });
        const data = await res.json();
        if (data.success) {
            const completedAt = new Date().toISOString();
            setPhase('completed');
            await onUpdateState({
                phase: 'completed',
                completion: { notes: completionNotes, completed_at: completedAt, overall_result: overallResult },
            });
        } else {
            setCompleteError(data.error || 'Failed to complete review');
        }
    } catch (err) {
        setCompleteError(err.message);
    } finally {
        setIsCompleting(false);
    }
};
```

**Step 2: Add `CompleteSection` inner component**:

```javascript
const CompleteSection = ({ assessments }) => {
    const pendingCount = assessments.filter(a => a.status === 'pending').length;
    const hasPending = pendingCount > 0;

    return (
        <div className="bg-white rounded-lg shadow-sm p-6">
            <h3 className="text-base font-semibold text-gray-800 mb-4">
                <i className="fa-solid fa-flag-checkered mr-2 text-gray-400"></i>
                Complete Image Review
            </h3>

            {hasPending && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-4">
                    <div className="flex items-center gap-2 text-amber-800">
                        <i className="fa-solid fa-triangle-exclamation"></i>
                        <span className="font-medium">
                            {pendingCount} photo{pendingCount !== 1 ? 's' : ''} still pending
                            review. All photos must be reviewed before completing.
                        </span>
                    </div>
                </div>
            )}

            {!hasPending && !showCompleteForm && (
                <button onClick={() => setShowCompleteForm(true)}
                    className={'inline-flex items-center px-5 py-2.5 bg-green-600 text-white ' +
                        'rounded-lg hover:bg-green-700 font-medium'}>
                    <i className="fa-solid fa-check mr-2"></i>
                    Complete Image Review
                </button>
            )}

            {showCompleteForm && !hasPending && (
                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                            Notes
                        </label>
                        <textarea
                            value={completionNotes}
                            onChange={e => setCompletionNotes(e.target.value)}
                            rows={3}
                            placeholder="Add any notes about this review..."
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-1 focus:ring-blue-500"
                        />
                    </div>
                    {completeError && (
                        <div className="text-red-600 text-sm">{completeError}</div>
                    )}
                    <div className="flex gap-3">
                        <button onClick={handleComplete} disabled={isCompleting}
                            className={'inline-flex items-center px-5 py-2.5 bg-green-600 text-white ' +
                                'rounded-lg hover:bg-green-700 disabled:bg-gray-400 font-medium'}>
                            {isCompleting
                                ? <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                                : <i className="fa-solid fa-check mr-2"></i>}
                            Save
                        </button>
                        <button onClick={() => setShowCompleteForm(false)}
                            className="px-5 py-2.5 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">
                            Cancel
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};
```

**Step 3: Verify**
```bash
python -c "import connect_labs.workflow.templates.bulk_image_audit"
```

**Step 4: Commit**
```bash
git add connect_labs/workflow/templates/bulk_image_audit.py
git commit -m "feat: bulk image audit complete image review with pending guard"
```

---

## Task 9: Completed Phase — Read-Only Summary

**Step 1: Add `CompletedPhase` inner component**:

```javascript
const CompletedPhase = () => {
    const completion = instance.state?.completion || {};
    const config = instance.state?.config || {};
    const threshold = config.threshold ?? 80;
    const overallResult = completion.overall_result || 'pass';
    const completedAt = completion.completed_at
        ? new Date(completion.completed_at).toLocaleDateString('en-US',
            { month: 'short', day: 'numeric', year: 'numeric' })
        : '';

    return (
        <div className="space-y-6">
            {/* Completion banner */}
            <div className="bg-green-50 border border-green-200 rounded-lg p-6">
                <div className="flex items-start justify-between">
                    <div>
                        <h2 className="text-lg font-semibold text-green-800 flex items-center gap-2">
                            <i className="fa-solid fa-circle-check text-green-600"></i>
                            Image Review Completed
                        </h2>
                        {completedAt && (
                            <p className="text-sm text-green-600 mt-1">
                                Completed on {completedAt}
                            </p>
                        )}
                        {completion.notes && (
                            <p className="text-sm text-gray-700 mt-3 bg-white/60 rounded p-3 border border-green-100">
                                {completion.notes}
                            </p>
                        )}
                    </div>
                    <span className={'inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium ' +
                        (overallResult === 'pass'
                            ? 'bg-green-100 text-green-800'
                            : 'bg-red-100 text-red-800')}>
                        {overallResult === 'pass'
                            ? <><i className="fa-solid fa-circle-check"></i> Overall Pass</>
                            : <><i className="fa-solid fa-circle-xmark"></i> Overall Fail</>}
                    </span>
                </div>
            </div>

            {/* Read-only FLW summary — loads from stored assessments */}
            <CompletedFlwTable config={config} threshold={threshold} />
        </div>
    );
};
```

**Step 2: Add `CompletedFlwTable`** — loads bulk-data on mount, then renders read-only table:

```javascript
const CompletedFlwTable = ({ config, threshold }) => {
    const sid = instance.state?.session_id;
    const [rows, setRows] = React.useState([]);
    const [loading, setLoading] = React.useState(true);

    React.useEffect(() => {
        if (!sid) { setLoading(false); return; }
        fetch('/audit/api/' + sid + '/bulk-data/')
            .then(r => r.json())
            .then(data => {
                setRows(buildFlwRows(data.assessments || [], config));
                setLoading(false);
            })
            .catch(() => setLoading(false));
    }, [sid]);

    if (loading) return (
        <div className="bg-white rounded-lg shadow-sm p-8 text-center">
            <i className="fa-solid fa-spinner fa-spin text-gray-400"></i>
        </div>
    );

    return (
        <FlwSummaryTable assessments={[]}
            threshold={threshold} config={config} readOnly={true}
            overrideRows={rows} />
    );
};
```

**Note:** Update `FlwSummaryTable` to accept an optional `overrideRows` prop so `CompletedFlwTable` can pass pre-built rows:

In `FlwSummaryTable`, change the first line of the component body from:
```javascript
const rows = buildFlwRows(assessments, config);
```
to:
```javascript
const rows = overrideRows !== undefined ? overrideRows : buildFlwRows(assessments, config);
```
And add `overrideRows` to the destructured props: `{ assessments, threshold, config, readOnly, overrideRows }`.

**Step 3: Verify**
```bash
python -c "import connect_labs.workflow.templates.bulk_image_audit"
```

**Step 4: Commit**
```bash
git add connect_labs/workflow/templates/bulk_image_audit.py
git commit -m "feat: bulk image audit completed phase with read-only summary"
```

---

## Task 10: Session ID Discovery

The audit creation API creates one session per group (usually one combined session for all opps/FLWs). The SSE completion `finalResult` dict needs to be inspected to reliably extract the session ID.

**Step 1: Check what the completion payload looks like**

```bash
# Search the tasks.py and views.py to understand the SSE completion structure
grep -n "sessions\|session_id\|task_complete\|is_complete" \
  "/mnt/c/Users/Mathew Theis/Documents/Connect/commcare-connect-labs/connect_labs/audit/tasks.py" \
  | head -30
```

**Step 2: Update `handleCreate`'s `onComplete` callback if needed**

Look at what `final` contains in the `streamAuditProgress` completion callback. If the session ID isn't in `final.session_id` or `final.sessions[0].id`, fetch it via the workflow sessions API:

```javascript
// Fallback: fetch session from workflow sessions API
const sessRes = await fetch('/audit/api/workflow/' + instance.id + '/sessions/');
const sessData = await sessRes.json();
const sid = sessData.sessions?.[0]?.id;
```

Update both the `handleCreate` completion handler and the reconnect handler to use this fallback.

**Step 3: Commit any fixes**
```bash
git add connect_labs/workflow/templates/bulk_image_audit.py
git commit -m "fix: reliable session ID discovery after audit creation"
```

---

## Task 11: End-to-End Manual Verification

All previous tasks added code. This task verifies the full flow in the browser.

**Prerequisites:** Django dev server running (`python manage.py runserver`), Celery worker running (`celery -A config.celery_app worker -l info`), JS built (`inv build-js`).

**Step 1: Create the "Bulk Image Audit" workflow**

1. Navigate to `/workflow/`
2. Click "New Workflow" → select "Bulk Image Audit" template
3. Confirm the name shows "Bulk Image Audit" (not the KMC one)
4. Also confirm the existing template now shows "Weekly KMC Audit with AI Review"

**Step 2: Create Run — Config phase**

1. Click the new workflow → "Create Run"
2. Verify config form shows: opp search, three image type buttons, visit selection (date presets + Last N), sampling %, threshold %
3. Search for an opportunity — confirm results appear, click one to add as a pill
4. Select "ORS Photo"
5. Set "Last 7 Days", 100% sampling, 80% threshold
6. Click "Create Review"

**Step 3: Creating phase**

1. Verify progress bar appears with stage name
2. Verify Cancel button is present
3. Wait for completion — phase should auto-transition to Review

**Step 4: Review phase**

1. Verify stats bar (Total / Pending / Passed / Failed)
2. Verify photo cards load thumbnails
3. Verify pass/fail buttons toggle correctly
4. Verify "Run AI Agent" button is **greyed out and disabled** for ORS Photo
5. Verify notes field saves (check network tab for POST to `/save/`)
6. Check FLW summary table: columns, sort (click headers), filters
7. Try "Complete Image Review" with pending photos → expect warning message
8. Mark all photos pass/fail → "Complete Image Review" form appears → add note → Save

**Step 5: Completed phase**

1. Verify completion banner shows date + notes
2. Verify FLW table is read-only (no filter inputs)
3. Navigate back to `/workflow/` → click the completed run → confirm it loads in Completed phase

**Step 6: Scale Photo AI review test**

1. Create another run with "Scale Photo" selected
2. Review phase → verify "Run AI Agent" button is **enabled** (purple, clickable)
3. Click AI Review on a photo → verify result appears

---

## Task 12: Final Cleanup and Summary Commit

**Step 1: Run Python linter**
```bash
cd /mnt/c/Users/Mathew\ Theis/Documents/Connect/commcare-connect-labs
pre-commit run --files connect_labs/workflow/templates/bulk_image_audit.py \
    connect_labs/workflow/templates/audit_with_ai_review.py \
    connect_labs/workflow/templates/__init__.py
```
Fix any flake8/ruff issues reported.

**Step 2: Final commit**
```bash
git add -u
git commit -m "feat: complete Bulk Image Audit workflow template

- Rename Weekly Audit to Weekly KMC Audit with AI Review
- Add Bulk Image Audit template with 4-phase inline review
- Multi-opp selector, image type selection (Scale/ORS/MUAC)
- Per-FLW pass/fail summary table with configurable threshold
- AI review button enabled for Scale Photo only
- Complete Image Review with pending guard and notes"
```
