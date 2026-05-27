"""
CHC Nutrition Analysis Workflow Template.

FLW-level nutrition and health metrics dashboard. Displays per-FLW aggregated
stats with inline MUAC distribution sparklines, SAM/MAM rates, gender split,
and one-click audit creation.

Single aggregated pipeline with a MUAC histogram. SAM and MAM counts are
derived from histogram bins in render code rather than filter_path (which
doesn't support range comparisons).

Form path variants:
  - Opp 814: form.case.update.*, form.additional_case_info.*
  - Opp 822: form.subcase_0.case.update.*, form.child_registration.*
The primary paths target opp 814. When instantiating for opp 822, edit the
pipeline schema to use the alternate paths (documented in comments).
"""

PIPELINE_SCHEMA = {
    "name": "CHC Nutrition Metrics",
    "description": "Per-FLW aggregated nutrition, MUAC, and health metrics",
    "version": 1,
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        {
            "name": "commcare_userid",
            "path": "form.meta.userID",
            "aggregation": "first",
            "description": "CommCare user ID from form metadata",
        },
        {
            "name": "male_count",
            "path": "form.additional_case_info.childs_gender",
            # Alt paths: "form.child_registration.childs_gender",
            #            "form.subcase_0.case.update.childs_gender"
            "aggregation": "count",
            "filter_path": "form.additional_case_info.childs_gender",
            "filter_value": "male",
            "description": "Number of male children visited",
        },
        {
            "name": "female_count",
            "path": "form.additional_case_info.childs_gender",
            "aggregation": "count",
            "filter_path": "form.additional_case_info.childs_gender",
            "filter_value": "female",
            "description": "Number of female children visited",
        },
        {
            "name": "muac_consent_count",
            "paths": [
                "form.case.update.muac_consent",
                "form.subcase_0.case.update.muac_consent",
                "form.service_delivery.muac_group.muac_consent_group.muac_consent",
            ],
            "aggregation": "count",
            "filter_path": "form.case.update.muac_consent",
            "filter_value": "yes",
            "description": "Number of MUAC consents obtained",
        },
        {
            "name": "muac_measurements_count",
            "paths": [
                "form.case.update.soliciter_muac_cm",
                "form.subcase_0.case.update.soliciter_muac",
                "form.service_delivery.muac_group.soliciter_muac",
            ],
            "aggregation": "count",
            "transform": "float",
            "description": "Number of valid MUAC measurements",
        },
        {
            "name": "avg_muac_cm",
            "paths": [
                "form.case.update.soliciter_muac_cm",
                "form.subcase_0.case.update.soliciter_muac",
                "form.service_delivery.muac_group.soliciter_muac",
            ],
            "aggregation": "avg",
            "transform": "float",
            "description": "Average MUAC measurement in cm",
        },
        {
            "name": "children_unwell_count",
            "paths": [
                "form.case.update.va_child_unwell_today",
                "form.subcase_0.case.update.va_child_unwell_today",
            ],
            "aggregation": "count",
            "filter_path": "form.case.update.va_child_unwell_today",
            "filter_value": "yes",
            "description": "Number of visits where child was unwell",
        },
        {
            "name": "under_malnutrition_treatment_count",
            "paths": [
                "form.case.update.under_treatment_for_mal",
                "form.subcase_0.case.update.under_treatment_for_mal",
                "form.service_delivery.muac_group.muac_display_group_1.under_treatment_for_mal",
            ],
            "aggregation": "count",
            "filter_path": "form.case.update.under_treatment_for_mal",
            "filter_value": "yes",
            "description": "Number of children under malnutrition treatment",
        },
        {
            "name": "malnutrition_diagnosed_count",
            "paths": [
                "form.case.update.diagnosed_with_mal_past_3_months",
                "form.subcase_0.case.update.diagnosed_with_mal_past_3_months",
                "form.service_delivery.muac_group.muac_display_group_1.diagnosed_with_mal_past_3_months",
            ],
            "aggregation": "count",
            "filter_path": "form.case.update.diagnosed_with_mal_past_3_months",
            "filter_value": "yes",
            "description": "Children diagnosed with malnutrition in past 3 months",
        },
        {
            "name": "received_va_dose_before_count",
            "paths": [
                "form.case.update.received_va_dose_before",
                "form.subcase_0.case.update.received_va_dose_before",
            ],
            "aggregation": "count",
            "filter_path": "form.case.update.received_va_dose_before",
            "filter_value": "yes",
            "description": "Children who received VA dose before",
        },
        {
            "name": "va_confirm_shared_knowledge_count",
            "paths": [
                "form.case.update.va_confirm_shared_knowledge",
                "form.subcase_0.case.update.va_confirm_shared_knowledge",
            ],
            "aggregation": "count",
            "description": "Times VA knowledge was shared and confirmed",
        },
        {
            "name": "ors_child_recovered_count",
            "paths": [
                "form.ors_group.did_the_child_recover",
                "form.service_delivery.ors_group.did_the_child_recover",
            ],
            "aggregation": "count",
            "filter_path": "form.ors_group.did_the_child_recover",
            "filter_value": "yes",
            "description": "Children who recovered with ORS",
        },
        {
            "name": "ors_still_facing_symptoms_count",
            "paths": [
                "form.ors_group.still_facing_symptoms",
                "form.service_delivery.ors_group.still_facing_symptoms",
            ],
            "aggregation": "count",
            "filter_path": "form.ors_group.still_facing_symptoms",
            "filter_value": "yes",
            "description": "Children still facing symptoms after ORS",
        },
        {
            "name": "received_any_vaccine_count",
            "paths": [
                "form.pictures.received_any_vaccine",
                "form.service_delivery.pictures.received_any_vaccine",
            ],
            "aggregation": "count",
            "filter_path": "form.pictures.received_any_vaccine",
            "filter_value": "yes",
            "description": "Children who received any vaccine",
        },
    ],
    "histograms": [
        {
            "name": "muac_distribution",
            "paths": [
                "form.case.update.soliciter_muac_cm",
                "form.subcase_0.case.update.soliciter_muac",
                "form.service_delivery.muac_group.soliciter_muac",
            ],
            "lower_bound": 9.5,
            "upper_bound": 21.5,
            "num_bins": 12,
            "bin_name_prefix": "muac",
            "transform": "float",
            "description": "MUAC measurement distribution (9.5-21.5 cm, 1cm bins)",
        },
    ],
    "filters": {},
}

DEFINITION = {
    "name": "CHC Nutrition Analysis",
    "description": "FLW-level nutrition and health metrics with MUAC distribution, SAM/MAM rates, and gender split.",
    "version": 1,
    "templateType": "chc_nutrition_analysis",
    "statuses": [
        {"id": "pending", "label": "Pending Review", "color": "gray"},
        {"id": "reviewed", "label": "Reviewed", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],
}

RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState, view }) {
    // ── Data ────────────────────────────────────────────────────
    // Prefer the snapshot-aware view.pipelines on completed runs so the
    // saved-run replay shows the same rows the reviewer saw at completion.
    // Falls back to the live top-level `pipelines` prop for in-progress
    // runs. The pipeline alias is configured per-workflow on
    // pipeline_sources — accept either "data" or "default".
    //
    // For synthetic in-progress runs (no real CSV behind the pipeline), the
    // BE stashes a preview snapshot on `instance.snapshot.pipelines`. Use
    // that as a third fallback so the table renders before the manager has
    // clicked "Complete Review" — without this the demo's first scene would
    // be a "No data available" placeholder.
    function _rowsFrom(p) {
        var d = p && (p.data || p.default);
        return (d && d.rows) || [];
    }
    var rows = _rowsFrom((view && view.pipelines) || null);
    if (!rows.length) rows = _rowsFrom(pipelines);
    if (!rows.length) rows = _rowsFrom(instance && instance.snapshot && instance.snapshot.pipelines);

    // ── Histogram bin names ─────────────────────────────────────
    var BINS = [
        'muac_9_5_10_5_visits',  'muac_10_5_11_5_visits',
        'muac_11_5_12_5_visits', 'muac_12_5_13_5_visits',
        'muac_13_5_14_5_visits', 'muac_14_5_15_5_visits',
        'muac_15_5_16_5_visits', 'muac_16_5_17_5_visits',
        'muac_17_5_18_5_visits', 'muac_18_5_19_5_visits',
        'muac_19_5_20_5_visits', 'muac_20_5_21_5_visits'
    ];
    // Red (SAM < 11.5), yellow (MAM 11.5-12.5), green (healthy ≥ 12.5)
    var BIN_COLORS = [
        '#ef4444', '#ef4444',
        '#facc15',
        '#22c55e', '#22c55e', '#22c55e', '#22c55e', '#22c55e',
        '#22c55e', '#22c55e', '#22c55e', '#22c55e'
    ];

    // ── Per-row derived fields ──────────────────────────────────
    function samCount(r) { return (r.muac_9_5_10_5_visits || 0) + (r.muac_10_5_11_5_visits || 0); }
    function mamCount(r) { return r.muac_11_5_12_5_visits || 0; }
    function muacCount(r) { return r.muac_distribution_count || r.muac_measurements_count || 0; }
    function genderPct(r) {
        var m = r.male_count || 0;
        var f = r.female_count || 0;
        var t = m + f;
        return t > 0 ? Math.round(f / t * 1000) / 10 : null;
    }

    // ── Summary stats ───────────────────────────────────────────
    var totalFlws = rows.length;
    var totalVisits = rows.reduce(function(s, r) { return s + (r.total_visits || 0); }, 0);
    var totalApproved = rows.reduce(function(s, r) { return s + (r.approved_visits || 0); }, 0);
    var avgVisitsPerFlw = totalFlws > 0 ? Math.round(totalVisits / totalFlws * 10) / 10 : 0;
    var totalMuac = rows.reduce(function(s, r) { return s + muacCount(r); }, 0);
    var totalSam = rows.reduce(function(s, r) { return s + samCount(r); }, 0);
    var totalMam = rows.reduce(function(s, r) { return s + mamCount(r); }, 0);
    var samRate = totalMuac > 0 ? Math.round(totalSam / totalMuac * 1000) / 10 : 0;
    var mamRate = totalMuac > 0 ? Math.round(totalMam / totalMuac * 1000) / 10 : 0;
    var totalUnwell = rows.reduce(function(s, r) { return s + (r.children_unwell_count || 0); }, 0);
    var totalTreatment = rows.reduce(function(s, r) { return s + (r.under_malnutrition_treatment_count || 0); }, 0);

    // ── KPI failure check ───────────────────────────────────────
    // Used by Actions cell (block "Mark No Issue" for failing FLWs) AND
    // by the column-header bulk action (skip failing FLWs).
    function rowIsFailingKPI(r) {
        var mc = muacCount(r);
        var sPct = mc > 0 ? (samCount(r) / mc) * 100 : 0;
        var mPct = mc > 0 ? (mamCount(r) / mc) * 100 : 0;
        var gPct = genderPct(r);
        return (sPct > 5) || (mPct > 15) || (gPct !== null && (gPct < 40 || gPct > 60));
    }

    // ── apiPost helper ──────────────────────────────────────────
    // Render-code-local fetch wrapper. Grabs CSRF off the workflow-root
    // dataset (where workflow-runner.tsx sets it on page mount) and posts
    // JSON. Use full URLs so the call sites self-document which endpoint
    // they hit — no JS rebuild needed for new endpoints.
    function apiPost(url, body) {
        var root = document.getElementById('workflow-root');
        var csrf = (root && root.dataset && root.dataset.csrfToken) || '';
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf,
            },
            body: JSON.stringify(body || {}),
        }).then(function(r) {
            return r.json().then(function(data) { return {ok: r.ok, status: r.status, data: data}; },
                                 function() { return {ok: r.ok, status: r.status, data: null}; });
        });
    }

    // ── Bulk decision helpers ───────────────────────────────────
    var runId = (instance && instance.id) || null;
    var oppId = (instance && instance.opportunity_id) || null;
    var runIsLive = !(view && view.isCompleted);
    var decisionsBase = runId ? ('/labs/workflow/api/run/' + runId + '/decisions/') : null;

    function postNoIssueDecision(row, kpiSnapshot) {
        // POST one no_issues decision. Returns the fetch promise so callers
        // can chain refresh logic. Surfaces server-side errors via alert().
        return apiPost(decisionsBase, {
            opportunity_id: oppId,
            flw_id: row.username,
            decision_type: 'no_issues',
            kpi_snapshot: kpiSnapshot || null,
        }).then(function(res) {
            if (!res.ok) {
                var msg = (res.data && (res.data.error || res.data.detail)) || ('HTTP ' + res.status);
                window.alert('Failed to mark ' + row.username + ' as no issue: ' + msg);
            }
            return res;
        });
    }

    function markAllNoIssue() {
        // Header action — bulk-mark every FLW that has no existing decision
        // AND isn't failing any KPI (KPI failures need a real audit first).
        if (!decisionsBase) {
            window.alert('Cannot record decisions: run id is unknown.');
            return;
        }
        var eligible = rows.filter(function(r) {
            var d = (view && typeof view.decisionsFor === 'function') ? view.decisionsFor(r.username) : null;
            return !d && !rowIsFailingKPI(r);
        });
        if (!eligible.length) {
            window.alert('No FLWs eligible for bulk "Mark No Issue" (everyone is either flagged or already decided).');
            return;
        }
        if (!window.confirm('Mark ' + eligible.length + ' FLWs as having no issues? Flagged FLWs are skipped.')) {
            return;
        }
        Promise.all(eligible.map(function(r) {
            return postNoIssueDecision(r, {sam_pct: muacCount(r) > 0 ? (samCount(r) / muacCount(r)) * 100 : 0,
                                          mam_pct: muacCount(r) > 0 ? (mamCount(r) / muacCount(r)) * 100 : 0,
                                          gender_pct: genderPct(r)});
        })).then(function() { window.location.reload(); });
    }

    function createTaskWithCoaching(row) {
        // Two-step flow:
        //   1) POST /tasks/api/single-create/ — create the task with a coaching
        //      prompt as description. The Django view reads opportunity_id
        //      from request.labs_context (set by the labs middleware off the
        //      URL's opportunity_id query param), so the page must carry
        //      ?opportunity_id=.
        //   2) POST /labs/workflow/api/run/<id>/decisions/ — record the
        //      decision linking the new task back to the FLW so the row
        //      shows the "View task" button on reload. We carry forward
        //      audit_session_ids if a prior manager-audit decision exists.
        // Navigate the manager to the task page so they can fire the OCS
        // chat via the existing "Initiate AI Assistant" modal (which uses
        // the description as the pre-filled prompt). No synthetic conversation
        // is auto-attached here; that's the manager's call.
        var name = displayName(row);
        var prompt =
            'Hi ' + name + ', your visit photos this week looked good — well-framed and the children appeared properly positioned. ' +
            'But the MUAC distribution shows something unusual: more measurements are clustered toward the low end than we\'d expect ' +
            'for a healthy population. This is the kind of pattern that often points at a measurement-technique issue rather than ' +
            'malpractice. Can we walk through how you\'re applying the MUAC tape — where on the arm, whether it\'s snug but not tight, ' +
            'and whether the arm is fully relaxed when you measure?';

        return apiPost('/tasks/api/single-create/', {
            username: row.username,
            flw_name: name,
            title: 'MUAC technique coaching: ' + name,
            description: prompt,
            priority: 'medium',
            workflow_run_id: runId,
        }).then(function(taskRes) {
            if (!taskRes.ok || !taskRes.data || !taskRes.data.success) {
                var emsg = (taskRes.data && (taskRes.data.error || taskRes.data.detail)) || ('HTTP ' + taskRes.status);
                window.alert('Failed to create task: ' + emsg);
                return null;
            }
            var taskId = taskRes.data.task_id;
            // Carry forward the audit id from the prior manager-audit click
            // (if any). decisionsFor() returns the most recent decision; we
            // expect that to be the action_taken/audit one.
            var priorDecision = (view && typeof view.decisionsFor === 'function')
                ? view.decisionsFor(row.username) : null;
            var audit_ids = (priorDecision && priorDecision.audit_session_ids) || null;
            return apiPost(decisionsBase, {
                opportunity_id: oppId,
                flw_id: row.username,
                decision_type: 'action_taken',
                reason_key: 'bad_muac_distribution',
                reason_label: 'Bad MUAC distribution',
                audit_session_ids: audit_ids,
                task_ids: [taskId],
            }).then(function(decRes) {
                if (!decRes.ok) {
                    console.warn('decision create failed for task ' + taskId + ':', decRes);
                }
                // Navigate the manager straight to the task page so they can
                // open the Initiate AI Assistant modal next.
                var oppScope = oppId ? '?opportunity_id=' + oppId : '';
                window.location.href = '/tasks/' + taskId + '/edit/' + oppScope;
            });
        });
    }

    // ── State ───────────────────────────────────────────────────
    var _sort = React.useState('total_visits');
    var sortKey = _sort[0]; var setSortKey = _sort[1];
    var _dir = React.useState(true);
    var sortDesc = _dir[0]; var setSortDesc = _dir[1];
    var _search = React.useState('');
    var search = _search[0]; var setSearch = _search[1];

    function toggleSort(key) {
        if (sortKey === key) { setSortDesc(!sortDesc); }
        else { setSortKey(key); setSortDesc(true); }
    }

    // ── Worker name lookup ──────────────────────────────────────
    var workerMap = React.useMemo(function() {
        var m = {};
        (workers || []).forEach(function(w) { m[w.username] = w.name || w.username; });
        return m;
    }, [workers]);
    function displayName(r) { return workerMap[r.username] || r.username; }

    // ── Sort + filter ───────────────────────────────────────────
    var sortedRows = React.useMemo(function() {
        var filtered = rows;
        if (search) {
            var q = search.toLowerCase();
            filtered = rows.filter(function(r) {
                return displayName(r).toLowerCase().indexOf(q) >= 0 ||
                       r.username.toLowerCase().indexOf(q) >= 0;
            });
        }
        var sorted = filtered.slice().sort(function(a, b) {
            var va, vb;
            if (sortKey === 'name') { va = displayName(a).toLowerCase(); vb = displayName(b).toLowerCase(); }
            else if (sortKey === 'sam') { va = muacCount(a) > 0 ? samCount(a) / muacCount(a) : -1; vb = muacCount(b) > 0 ? samCount(b) / muacCount(b) : -1; }
            else if (sortKey === 'mam') { va = muacCount(a) > 0 ? mamCount(a) / muacCount(a) : -1; vb = muacCount(b) > 0 ? mamCount(b) / muacCount(b) : -1; }
            else if (sortKey === 'gender') { va = genderPct(a) !== null ? genderPct(a) : -1; vb = genderPct(b) !== null ? genderPct(b) : -1; }
            else { va = a[sortKey] || 0; vb = b[sortKey] || 0; }
            if (va < vb) return sortDesc ? 1 : -1;
            if (va > vb) return sortDesc ? -1 : 1;
            return 0;
        });
        return sorted;
    }, [rows, sortKey, sortDesc, search, workerMap]);

    // ── Sparkline component ─────────────────────────────────────
    function MuacSparkline(props) {
        var r = props.row;
        var count = muacCount(r);
        if (!count) return React.createElement('span', {className: 'text-gray-400'}, '-');
        var vals = BINS.map(function(b) { return r[b] || 0; });
        var maxBin = Math.max.apply(null, vals.concat([1]));
        return React.createElement('div', {
            className: 'inline-flex items-end gap-px overflow-hidden cursor-help',
            style: {height: '28px'},
            title: 'MUAC: ' + (r.muac_distribution_mean || 0).toFixed(1) + ' cm avg (n=' + count + ')'
        }, vals.map(function(v, i) {
            return React.createElement('span', {
                key: i,
                className: 'rounded-sm',
                style: {
                    width: '4px',
                    backgroundColor: BIN_COLORS[i],
                    height: Math.max(1, v / maxBin * 28) + 'px'
                }
            });
        }));
    }

    // ── Sort header helper ──────────────────────────────────────
    function SortTh(props) {
        var arrow = sortKey === props.skey ? (sortDesc ? ' ↓' : ' ↑') : '';
        return React.createElement('th', {
            className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700',
            onClick: function() { toggleSort(props.skey); },
            title: props.title || ''
        }, props.label + arrow);
    }

    // ── Gender badge ────────────────────────────────────────────
    function GenderBadge(props) {
        var r = props.row;
        var pct = genderPct(r);
        if (pct === null) return React.createElement('span', {className: 'text-gray-400'}, '-');
        var color = (pct >= 45 && pct <= 55) ? 'text-green-600' :
                    (pct < 40 || pct > 60) ? 'text-red-600' : 'text-yellow-600';
        return React.createElement('span', null,
            React.createElement('span', {className: color + ' font-medium'}, pct + '%'),
            React.createElement('span', {className: 'text-gray-500 text-xs ml-1'},
                '(' + (r.female_count || 0) + 'F/' + (r.male_count || 0) + 'M)')
        );
    }

    // ── Render ──────────────────────────────────────────────────
    return React.createElement('div', {className: 'space-y-6'},

        // Title
        React.createElement('div', null,
            React.createElement('h1', {className: 'text-2xl font-bold text-gray-900'}, definition.name),
            React.createElement('p', {className: 'text-gray-600 mt-1'}, definition.description)
        ),

        // Summary cards
        React.createElement('div', {className: 'bg-white border border-gray-200 rounded-lg p-6 shadow-sm'},
            React.createElement('h2', {className: 'text-lg font-semibold text-gray-900 mb-4'}, 'Summary'),
            React.createElement('div', {className: 'grid grid-cols-2 md:grid-cols-4 gap-4'},
                // Row 1: general
                React.createElement('div', {className: 'border-l-4 border-blue-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Total FLWs'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalFlws)
                ),
                React.createElement('div', {className: 'border-l-4 border-blue-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Total Visits'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalVisits.toLocaleString())
                ),
                React.createElement('div', {className: 'border-l-4 border-blue-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Avg Visits/FLW'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, avgVisitsPerFlw)
                ),
                React.createElement('div', {className: 'border-l-4 border-blue-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Total Approved'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalApproved.toLocaleString())
                ),
                // Row 2: nutrition
                React.createElement('div', {className: 'border-l-4 border-red-600 pl-3', title: 'Severe Acute Malnutrition (MUAC < 11.5 cm)'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'SAM Rate'),
                    React.createElement('div', {className: 'text-2xl font-bold text-red-600'},
                        samRate + '%',
                        React.createElement('span', {className: 'text-base text-gray-500 ml-1'}, '(' + totalSam + ')')
                    )
                ),
                React.createElement('div', {className: 'border-l-4 border-yellow-500 pl-3', title: 'Moderate Acute Malnutrition (MUAC 11.5-12.5 cm)'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'MAM Rate'),
                    React.createElement('div', {className: 'text-2xl font-bold text-yellow-600'},
                        mamRate + '%',
                        React.createElement('span', {className: 'text-base text-gray-500 ml-1'}, '(' + totalMam + ')')
                    )
                ),
                React.createElement('div', {className: 'border-l-4 border-orange-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Children Unwell'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalUnwell.toLocaleString())
                ),
                React.createElement('div', {className: 'border-l-4 border-purple-500 pl-3'},
                    React.createElement('div', {className: 'text-sm text-gray-600'}, 'Under Treatment'),
                    React.createElement('div', {className: 'text-2xl font-bold text-gray-900'}, totalTreatment.toLocaleString())
                )
            )
        ),

        // Search
        React.createElement('div', {className: 'flex items-center gap-4'},
            React.createElement('input', {
                type: 'text',
                placeholder: 'Search FLWs...',
                value: search,
                onChange: function(e) { setSearch(e.target.value); },
                className: 'px-3 py-2 border border-gray-300 rounded-md text-sm w-64 focus:outline-none focus:ring-2 focus:ring-blue-500'
            }),
            React.createElement('span', {className: 'text-sm text-gray-500'},
                sortedRows.length + ' of ' + rows.length + ' FLWs')
        ),

        // FLW table
        React.createElement('div', {className: 'bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden'},
            React.createElement('div', {className: 'px-6 py-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between gap-4'},
                React.createElement('h2', {className: 'text-lg font-semibold text-gray-900'},
                    'FLW-Level Analysis',
                    React.createElement('span', {className: 'text-sm text-gray-600 font-normal ml-2'},
                        '(' + rows.length + ' FLWs)')
                ),
                // Bulk-mark toolbar action. Lives in the table title row so
                // it reads as a real action button (rather than a tiny pill
                // shoved into the column header). Hidden once the run is
                // completed since decisions are read-only.
                runIsLive && decisionsBase
                    ? React.createElement('button', {
                        type: 'button',
                        onClick: markAllNoIssue,
                        className: 'inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium text-green-700 bg-white border border-green-300 hover:bg-green-50 transition-colors',
                        title: 'Mark every non-flagged FLW (no decision yet) as having no issues',
                      },
                        React.createElement('i', {className: 'fa-solid fa-check-double'}),
                        React.createElement('span', null, 'Mark all non-flagged FLWs as No Issue')
                      )
                    : null
            ),
            React.createElement('div', {className: 'overflow-x-auto'},
                React.createElement('table', {className: 'min-w-full divide-y divide-gray-200'},
                    React.createElement('thead', {className: 'bg-gray-50'},
                        React.createElement('tr', null,
                            React.createElement(SortTh, {skey: 'name', label: 'FLW Name'}),
                            React.createElement(SortTh, {skey: 'total_visits', label: 'Total Visits'}),
                            React.createElement(SortTh, {skey: 'approved_visits', label: 'Approved'}),
                            React.createElement(SortTh, {skey: 'days_active', label: 'Days Active'}),
                            React.createElement(SortTh, {skey: 'muac_measurements_count', label: 'MUAC Count'}),
                            React.createElement(SortTh, {skey: 'avg_muac_cm', label: 'Avg MUAC (cm)'}),
                            React.createElement(SortTh, {skey: 'sam', label: 'SAM', title: 'Severe Acute Malnutrition (MUAC < 11.5 cm)'}),
                            React.createElement(SortTh, {skey: 'mam', label: 'MAM', title: 'Moderate Acute Malnutrition (MUAC 11.5-12.5 cm)'}),
                            React.createElement('th', {className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider'}, 'MUAC Distribution'),
                            React.createElement(SortTh, {skey: 'gender', label: 'Gender Split', title: 'Female % of (Male + Female)'}),
                            React.createElement('th', {className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider'}, 'Decision'),
                            React.createElement('th', {className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider'}, 'Actions')
                        )
                    ),
                    React.createElement('tbody', {className: 'bg-white divide-y divide-gray-200'},
                        sortedRows.length === 0
                            ? React.createElement('tr', null,
                                React.createElement('td', {colSpan: 12, className: 'px-4 py-8 text-center text-sm text-gray-500'}, 'No data available'))
                            : sortedRows.map(function(r) {
                                var mc = muacCount(r);
                                var sc = samCount(r);
                                var mmc = mamCount(r);
                                var samPct = mc > 0 ? Math.round(sc / mc * 100) : null;
                                var mamPct = mc > 0 ? Math.round(mmc / mc * 100) : null;
                                var name = displayName(r);

                                return React.createElement('tr', {key: r.username, className: 'hover:bg-gray-50'},
                                    // FLW Name
                                    React.createElement('td', {className: 'px-4 py-3 text-sm'},
                                        React.createElement('div', {className: 'font-medium text-gray-900'}, name),
                                        name !== r.username
                                            ? React.createElement('div', {className: 'text-xs text-gray-500'}, r.username)
                                            : null
                                    ),
                                    // Total Visits
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'}, r.total_visits || 0),
                                    // Approved
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'},
                                        r.total_visits > 0
                                            ? (Math.round((r.approved_visits || 0) / r.total_visits * 1000) / 10) + '% (' + (r.approved_visits || 0) + ')'
                                            : '-'
                                    ),
                                    // Days Active
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'}, r.days_active || 0),
                                    // MUAC Count
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'}, mc),
                                    // Avg MUAC
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm text-gray-900'},
                                        r.avg_muac_cm ? r.avg_muac_cm.toFixed(1) : '-'),
                                    // SAM
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        samPct !== null
                                            ? React.createElement('span', null,
                                                React.createElement('span', {className: 'text-red-600 font-medium'}, samPct + '%'),
                                                React.createElement('span', {className: 'text-gray-500 ml-1'}, '(' + sc + ')'))
                                            : React.createElement('span', {className: 'text-gray-400'}, '-')
                                    ),
                                    // MAM
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        mamPct !== null
                                            ? React.createElement('span', null,
                                                React.createElement('span', {className: 'text-yellow-600 font-medium'}, mamPct + '%'),
                                                React.createElement('span', {className: 'text-gray-500 ml-1'}, '(' + mmc + ')'))
                                            : React.createElement('span', {className: 'text-gray-400'}, '-')
                                    ),
                                    // MUAC Distribution sparkline
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        React.createElement(MuacSparkline, {row: r})),
                                    // Gender Split
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        React.createElement(GenderBadge, {row: r})),
                                    // Decision — fill the cell with the right pill: green "No Issues"
                                    // for no_issues decisions, red warning for action_taken. The
                                    // Actions column does NOT repeat this signal for no_issues
                                    // (it's empty for those rows); for action_taken it shows
                                    // the View Audit / View Task buttons.
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        (function() {
                                            if (!view || typeof view.decisionsFor !== 'function') return null;
                                            var d = view.decisionsFor(r.username);
                                            if (!d) return null;
                                            if (d.decision_type === 'no_issues') {
                                                return React.createElement('span', {
                                                    className: 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800',
                                                    title: d.decided_at ? 'Decided ' + d.decided_at : ''
                                                },
                                                    React.createElement('i', {className: 'fa-solid fa-check'}),
                                                    'No Issues'
                                                );
                                            }
                                            var label = d.reason_label || d.reason_key || 'Action';
                                            return React.createElement('span', {
                                                className: 'inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800',
                                                title: d.decided_at ? 'Decided ' + d.decided_at : ''
                                            }, '⚠ ' + label);
                                        })()
                                    ),
                                    // Actions — state-aware per-FLW. Buttons reflect:
                                    //   - the existing decision + linked audit/task (with their
                                    //     outcome rendered inline so the reviewer can scan the
                                    //     column without opening each one)
                                    //   - whether the FLW is failing any KPI (SAM > 5%, MAM > 15%,
                                    //     or gender split outside 40-60%) — failing FLWs can't
                                    //     be marked "no issue" without first reviewing the data
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm'},
                                        (function() {
                                            var d = (view && typeof view.decisionsFor === 'function') ? view.decisionsFor(r.username) : null;
                                            var oppScope = (instance && instance.opportunity_id) ? '?opportunity_id=' + instance.opportunity_id : '';
                                            // Failing-KPI check: any of SAM > 5%, MAM > 15%, gender out of 40-60% band.
                                            var sPct = muacCount(r) > 0 ? (samCount(r) / muacCount(r)) * 100 : 0;
                                            var mPct = muacCount(r) > 0 ? (mamCount(r) / muacCount(r)) * 100 : 0;
                                            var gPct = genderPct(r);
                                            var isFailing = (sPct > 5) || (mPct > 15) || (gPct !== null && (gPct < 40 || gPct > 60));

                                            function auditLabel(a) {
                                                if (!a) return 'View audit #' + d.audit_session_ids[0];
                                                if (a.status === 'in_review' || a.status === 'in_progress') {
                                                    return 'View audit #' + a.id + ' (in review)';
                                                }
                                                if (a.overall_result === 'pass') {
                                                    return 'View audit #' + a.id + ' (pass)';
                                                }
                                                if (a.overall_result === 'fail') {
                                                    var total = (a.pass_count || 0) + (a.fail_count || 0) + (a.pending_count || 0);
                                                    return 'View audit #' + a.id + ' (fail · ' + (a.fail_count || 0) + '/' + total + ')';
                                                }
                                                return 'View audit #' + a.id;
                                            }
                                            function taskLabel(t) {
                                                if (!t) return 'View task #' + d.task_ids[0];
                                                if (t.status === 'closed') {
                                                    var action = t.official_action || 'closed';
                                                    return 'View task #' + t.id + ' (closed · ' + action + ')';
                                                }
                                                return 'View task #' + t.id + ' (' + (t.status || '').replace(/_/g, ' ') + ')';
                                            }

                                            var buttons = [];
                                            if (d && d.audit_session_ids && d.audit_session_ids.length) {
                                                var firstAudit = (d.audit_outcomes && d.audit_outcomes[0]) || null;
                                                buttons.push(React.createElement('a', {
                                                    key: 'audit',
                                                    href: '/audit/' + d.audit_session_ids[0] + '/' + oppScope,
                                                    className: 'inline-flex items-center px-3 py-1 border border-indigo-300 rounded-md text-xs font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 transition-colors',
                                                    title: 'View audit session for ' + name,
                                                }, auditLabel(firstAudit)));
                                            }
                                            if (d && d.task_ids && d.task_ids.length) {
                                                var firstTask = (d.task_outcomes && d.task_outcomes[0]) || null;
                                                buttons.push(React.createElement('a', {
                                                    key: 'task',
                                                    href: '/tasks/' + d.task_ids[0] + '/edit/' + oppScope,
                                                    className: 'inline-flex items-center px-3 py-1 border border-indigo-300 rounded-md text-xs font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 transition-colors ml-2',
                                                    title: 'View follow-up task for ' + name,
                                                }, taskLabel(firstTask)));
                                            }
                                            // no_issues decisions render an empty Actions cell —
                                            // the Decision column carries the "No Issues" pill,
                                            // and there's nothing to act on once the row is decided.
                                            if (!d) {
                                                // Only allow "Mark no issues" when KPIs are within thresholds.
                                                // FLWs failing any KPI need a real review (audit) before any sign-off.
                                                if (!isFailing && runIsLive) {
                                                    buttons.push(React.createElement('button', {
                                                        key: 'mark',
                                                        type: 'button',
                                                        onClick: function() {
                                                            postNoIssueDecision(r, {sam_pct: sPct, mam_pct: mPct, gender_pct: gPct})
                                                                .then(function(res) { if (res.ok) window.location.reload(); });
                                                        },
                                                        className: 'inline-flex items-center px-3 py-1 rounded-md text-xs font-medium text-green-700 bg-green-50 border border-green-200 hover:bg-green-100',
                                                        title: 'Mark this FLW as no issues',
                                                    }, 'Mark No Issue'));
                                                }
                                                // Manager-flow shortcut: when the run is in_progress, "Create
                                                // Audit" hits the synthetic helper that atomically writes a
                                                // pass-clean audit + linking decision and returns a redirect
                                                // URL to the audit detail page. Once the run is completed,
                                                // fall back to the regular audit wizard.
                                                if (runIsLive && runId && oppId) {
                                                    buttons.push(React.createElement('button', {
                                                        key: 'create-audit',
                                                        type: 'button',
                                                        onClick: function() {
                                                            apiPost('/labs/workflow/api/run/' + runId + '/manager-audit/', {
                                                                opportunity_id: oppId,
                                                                flw_id: r.username,
                                                            }).then(function(res) {
                                                                if (!res.ok || !res.data) {
                                                                    var emsg = (res.data && (res.data.error || res.data.detail)) || ('HTTP ' + res.status);
                                                                    window.alert('Failed to create audit: ' + emsg);
                                                                    return;
                                                                }
                                                                window.location.href = res.data.redirect_url;
                                                            });
                                                        },
                                                        className: 'inline-flex items-center px-3 py-1 border border-blue-300 rounded-md text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 transition-colors' + ((!isFailing && runIsLive) ? ' ml-2' : ''),
                                                        title: 'Create audit for ' + name,
                                                    }, 'Create Audit'));
                                                } else {
                                                    buttons.push(React.createElement('a', {
                                                        key: 'create-audit',
                                                        href: links.auditUrl({username: r.username, count: 5}),
                                                        className: 'inline-flex items-center px-3 py-1 border border-blue-300 rounded-md text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 transition-colors' + ((!isFailing && runIsLive) ? ' ml-2' : ''),
                                                        title: 'Create audit for ' + name,
                                                    }, 'Create Audit'));
                                                }
                                            }
                                            // If a decision exists with an audit (passed) but no task yet,
                                            // expose a "Create Task with Coaching" button. This is the
                                            // manager-flow second-step: audit passed → still need coaching
                                            // task because the underlying KPI is bad (e.g. bad MUAC dist).
                                            if (runIsLive && d && d.audit_session_ids && d.audit_session_ids.length &&
                                                (!d.task_ids || !d.task_ids.length)) {
                                                buttons.push(React.createElement('button', {
                                                    key: 'create-task-ocs',
                                                    type: 'button',
                                                    onClick: function() { createTaskWithCoaching(r); },
                                                    className: 'inline-flex items-center px-3 py-1 border border-purple-300 rounded-md text-xs font-medium text-purple-700 bg-purple-50 hover:bg-purple-100 transition-colors ml-2',
                                                    title: 'Create a follow-up task and start an OCS coaching conversation for ' + name,
                                                }, 'Create Task with Coaching'));
                                            }
                                            return React.createElement('div', {className: 'flex flex-wrap items-center gap-y-1'}, buttons);
                                        })()
                                    )
                                );
                            })
                    )
                )
            )
        )
    );
}"""

TEMPLATE = {
    "key": "chc_nutrition_analysis",
    "name": "CHC Nutrition Analysis",
    "description": "FLW-level nutrition and health metrics with MUAC distribution, SAM/MAM rates, and gender split.",
    "icon": "fa-heartbeat",
    "color": "red",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
    # Saved-runs opt-in (spec §4.1). Freezes a snapshot at run completion so
    # re-opening the run shows what was true at the moment of completion.
    # Decisions are NOT in the snapshot — they're queried live (spec §3.3,
    # exposed via view.decisionsFor() in render code).
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": None,  # capture all pipelines on the workflow definition
        "workers": True,
        "state_keys": [],  # this template currently has no state to preserve
    },
}
