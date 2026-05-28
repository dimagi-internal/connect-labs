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
    # Flag catalog — auto-applied via view.ensureAutoFlags on mount.
    # Render code re-derives presence per row; this list is the contractual
    # set so reviewers know what the report can flag. Concern is too-LOW
    # SAM/MAM rates (FLW cherry-picking easier households) rather than too-
    # high; gender skew is symmetric (either side of 40-60% triggers).
    "flags": [
        {"key": "sam_low", "label": "SAM rate < 1%", "auto": True},
        {"key": "mam_low", "label": "MAM rate < 3%", "auto": True},
        {"key": "gender_skew", "label": "Gender split outside 40-60%", "auto": True},
    ],
    # Action catalog — all available regardless of flag status. If a row
    # carries a flag, the action's menu surfaces a flag-context-aware
    # quick action that pre-fills the relevant audit filter or coaching
    # prompt.
    "actions": [
        {"key": "create_audit", "label": "Create Audit"},
        {"key": "create_task", "label": "Create Task"},
    ],
}

RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState, view }) {
    // ── Data ────────────────────────────────────────────────────
    // Row priority:
    //   1. view.pipelines — snapshot-aware helper. On a completed run this
    //      returns the rows the reviewer saw at completion; on in_progress
    //      runs it returns null.
    //   2. instance.snapshot.pipelines — raw snapshot. Per
    //      commcare_connect/workflow/views.py the framework leaves snapshot
    //      null on in_progress runs in production, so this fallback only
    //      fires when something deliberately seeded one — currently the
    //      synthetic generator, which stamps a preview snapshot onto its
    //      backdated in_progress run so the manager-flow demo doesn't open
    //      on "No data available".
    //   3. pipelines — live top-level prop. The last resort for real
    //      in_progress runs that haven't been completed yet.
    //
    // Snapshot-before-live matters for synthetic demos: opps in the
    // synthetic registry can have a fixture CSV with a different FLW set
    // than the seed wrote into the snapshot, and we always want the
    // narrative FLWs (amina_n / jumoke_n / ...) to win.
    //
    // The pipeline alias is configured per-workflow on pipeline_sources —
    // accept either "data" or "default".
    function _rowsFrom(p) {
        var d = p && (p.data || p.default);
        return (d && d.rows) || [];
    }
    var rows = _rowsFrom((view && view.pipelines) || null);
    if (!rows.length) rows = _rowsFrom(instance && instance.snapshot && instance.snapshot.pipelines);
    if (!rows.length) rows = _rowsFrom(pipelines);

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

    // ── Run / opp context ───────────────────────────────────────
    var runId = (instance && instance.id) || null;
    var oppId = (instance && instance.opportunity_id) || null;
    var runIsLive = !(view && view.isCompleted);
    var oppScope = oppId ? '?opportunity_id=' + oppId : '';

    // ── Flag catalog ────────────────────────────────────────────
    // Each entry: {key, label, predicate(row) → bool, evidence(row) → obj}.
    // The framework auto-applies these on mount via view.ensureAutoFlags
    // and dedups per (run, flw, flag_key) so it's safe to call on every
    // render. Mirrors the static catalog declared on DEFINITION.flags so
    // the contract is auditable from both ends.
    //
    // Concern semantics for SAM/MAM: too-LOW rates relative to a typical
    // catchment population imply the FLW is only visiting easy-to-reach
    // households (and thus missing the actual at-risk cases). We require a
    // floor of 10 MUAC measurements before flagging so a brand-new FLW
    // with 2 visits doesn't trip this on a small-sample fluke.
    var FLAG_CATALOG = [
        {
            key: 'sam_low',
            label: 'SAM rate < 1%',
            predicate: function(r) {
                var mc = muacCount(r);
                if (mc < 10) return false;
                return (samCount(r) / mc) * 100 < 1;
            },
            evidence: function(r) {
                var mc = muacCount(r);
                return {sam_pct: mc > 0 ? (samCount(r) / mc) * 100 : 0, n: mc};
            },
        },
        {
            key: 'mam_low',
            label: 'MAM rate < 3%',
            predicate: function(r) {
                var mc = muacCount(r);
                if (mc < 10) return false;
                return (mamCount(r) / mc) * 100 < 3;
            },
            evidence: function(r) {
                var mc = muacCount(r);
                return {mam_pct: mc > 0 ? (mamCount(r) / mc) * 100 : 0, n: mc};
            },
        },
        {
            key: 'gender_skew',
            label: 'Gender split outside 40-60%',
            predicate: function(r) {
                var g = genderPct(r);
                return g !== null && (g < 40 || g > 60);
            },
            evidence: function(r) {
                return {female_pct: genderPct(r)};
            },
        },
    ];

    function computeFlagsForRow(r) {
        return FLAG_CATALOG.filter(function(f) { return f.predicate(r); });
    }

    // ── Per-row action handlers ─────────────────────────────────
    // Each action is always available regardless of flag status (per the
    // design — actions can be initiated whether or not the system has
    // raised a concern). When a flag IS present, the action's quick-menu
    // surfaces a flag-context-aware variant that pre-fills the relevant
    // filter or coaching prompt.

    function createAudit(row, opts) {
        // Manager-flow shortcut: when the run is in_progress, hit the
        // synthetic helper that atomically writes a pass-clean audit and
        // returns a redirect URL. Once the run is completed, fall back to
        // the regular audit wizard (which doesn't carry the same context
        // pre-filling but at least opens the right page).
        if (!runIsLive || !runId || !oppId) {
            window.location.href = links.auditUrl({username: row.username, count: (opts && opts.count) || 5});
            return;
        }
        apiPost('/labs/workflow/api/run/' + runId + '/manager-audit/', {
            opportunity_id: oppId,
            flw_id: row.username,
            filter: (opts && opts.filter) || null,
        }).then(function(res) {
            if (!res.ok || !res.data) {
                var emsg = (res.data && (res.data.error || res.data.detail)) || ('HTTP ' + res.status);
                window.alert('Failed to create audit: ' + emsg);
                return;
            }
            window.location.href = res.data.redirect_url;
        });
    }

    function createTask(row, opts) {
        // Per #282: description stays short and scannable on the task UI;
        // a long-form coaching_prompt rides in extra_data.coaching_prompt
        // so the task page's "Initiate AI Assistant" modal can pre-fill
        // from it (see task_create_edit.html showAIModal).
        var name = displayName(row);
        var title = (opts && opts.title) || ('Follow-up: ' + name);
        var description = (opts && opts.description) || '';
        var body = {
            username: row.username,
            flw_name: name,
            title: title,
            description: description,
            priority: 'medium',
            workflow_run_id: runId,
        };
        if (opts && opts.coaching_prompt) {
            body.extra_data = { coaching_prompt: opts.coaching_prompt };
        }
        apiPost('/tasks/api/single-create/', body).then(function(res) {
            if (!res.ok || !res.data || !res.data.success) {
                var emsg = (res.data && (res.data.error || res.data.detail)) || ('HTTP ' + res.status);
                window.alert('Failed to create task: ' + emsg);
                return;
            }
            window.location.href = '/tasks/' + res.data.task_id + '/edit/' + oppScope;
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

    // ── Auto-apply flags on mount ─────────────────────────────
    // Computes flags from the current rows and POSTs anything not already
    // persisted. The framework dedups by (run, flw, flag_key) so this is
    // safe to call on every render — but we still gate on rows.length so
    // we don't fire while the SSE pipeline-data stream is still warming up
    // (initial render with empty rows would create zero flags anyway, but
    // explicit is better than implicit).
    React.useEffect(function() {
        if (!view || !view.ensureAutoFlags || !runIsLive || !rows.length) return;
        var computed = [];
        rows.forEach(function(r) {
            computeFlagsForRow(r).forEach(function(f) {
                computed.push({
                    flw_id: r.username,
                    flag_key: f.key,
                    flag_label: f.label,
                    evidence: f.evidence(r),
                });
            });
        });
        if (computed.length) {
            view.ensureAutoFlags(computed);
        }
    }, [rows.length, runIsLive]);

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

    // ── MenuButton (split-button dropdown) ─────────────────────
    // Trigger button + popover of quick-action items. Visual styling
    // mirrors the project's text_button_dropdown component
    // (commcare_connect/templates/components/dropdowns/text_button_dropdown.html):
    // rounded-lg shadow-lg bg-white border-gray-200, items use the
    // hover:bg-slate-100 row pattern. Each item is
    // {label, description?, onClick} — no icons. Closes on outside click
    // or Escape.
    function MenuButton(props) {
        var _open = React.useState(false);
        var open = _open[0]; var setOpen = _open[1];
        var ref = React.useRef(null);
        React.useEffect(function() {
            if (!open) return;
            function onDocClick(e) {
                if (ref.current && !ref.current.contains(e.target)) setOpen(false);
            }
            function onKey(e) { if (e.key === 'Escape') setOpen(false); }
            document.addEventListener('mousedown', onDocClick);
            document.addEventListener('keydown', onKey);
            return function() {
                document.removeEventListener('mousedown', onDocClick);
                document.removeEventListener('keydown', onKey);
            };
        }, [open]);
        // Accent palette ties the open dropdown to its trigger so the
        // panel visibly belongs to "Create Audit" (blue) vs "Create Task"
        // (purple) rather than reading as a free-floating white box.
        var ACCENTS = {
            blue: {
                panel: 'border-blue-300',
                header: 'bg-blue-50 text-blue-700 border-blue-200',
                item: 'border-blue-200 bg-white text-blue-800 hover:bg-blue-100 hover:border-blue-400 cursor-pointer',
            },
            purple: {
                panel: 'border-purple-300',
                header: 'bg-purple-50 text-purple-700 border-purple-200',
                item: 'border-purple-200 bg-white text-purple-800 hover:bg-purple-100 hover:border-purple-400 cursor-pointer',
            },
        };
        var accent = ACCENTS[props.accent] || ACCENTS.blue;
        var btnClass = 'inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium border transition-colors ' + (props.className || '');
        return React.createElement('div', {ref: ref, className: 'relative inline-block'},
            React.createElement('button', {
                type: 'button',
                onClick: function() { setOpen(!open); },
                className: btnClass,
                title: props.title || '',
            },
                React.createElement('span', null, props.label),
                React.createElement('i', {className: 'fa-solid fa-chevron-down text-[10px] opacity-70'})
            ),
            open
                ? React.createElement('div', {
                    // 2px accent-colored border + matching header band so the
                    // panel reads as an extension of the colored trigger.
                    className: 'absolute right-0 z-20 mt-1 w-64 rounded-lg bg-white shadow-xl border-2 overflow-hidden ' + accent.panel
                  },
                    // Header: repeats the trigger label so it's unambiguous
                    // which button this menu belongs to.
                    React.createElement('div', {
                        className: 'px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide border-b ' + accent.header,
                    }, props.label),
                    React.createElement('div', {className: 'py-2 px-2'},
                        props.items.map(function(item, i) {
                            // Each item renders as a visibly-outlined button in
                            // the accent color so the dropdown reads as a row of
                            // clickable buttons that clearly belong to the trigger.
                            return React.createElement('button', {
                                key: i,
                                type: 'button',
                                disabled: !!item.disabled,
                                onClick: function() { setOpen(false); item.onClick(); },
                                className: 'block w-full text-left text-sm font-medium px-3 py-2 mb-1 last:mb-0 rounded-md border transition-colors ' +
                                    (item.disabled
                                        ? 'border-gray-200 text-gray-400 cursor-not-allowed bg-gray-50'
                                        : accent.item),
                                title: item.title || item.label || '',
                            }, item.label);
                        })
                    )
                  )
                : null
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
                )
                // No bulk toolbar — auto-flags are applied on mount via
                // view.ensureAutoFlags. The efficiency win for the manager
                // is in the per-row action menus, not in a "mark everything"
                // sweep.
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
                            React.createElement('th', {className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider'}, 'Flags'),
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
                                    // Flags — pill per active flag, or em-dash when none.
                                    // Each pill shows the flag_label + a tooltip with the
                                    // evidence (the metric values that triggered it). Order
                                    // by flag_key so the same row always renders pills in
                                    // the same slot.
                                    React.createElement('td', {className: 'px-4 py-3 text-sm align-top'},
                                        (function() {
                                            var rowFlags = (view && typeof view.flagsFor === 'function') ? view.flagsFor(r.username) : [];
                                            rowFlags = rowFlags.slice().sort(function(a, b) {
                                                return (a.flag_key || '').localeCompare(b.flag_key || '');
                                            });
                                            if (!rowFlags.length) {
                                                return React.createElement('span', {className: 'text-gray-300 text-xs'}, '—');
                                            }
                                            return React.createElement('div', {className: 'flex flex-wrap gap-1'},
                                                rowFlags.map(function(f) {
                                                    var ev = f.evidence ? Object.keys(f.evidence).map(function(k) {
                                                        var v = f.evidence[k];
                                                        if (typeof v === 'number') v = v.toFixed(1);
                                                        return k + ': ' + v;
                                                    }).join(' · ') : '';
                                                    return React.createElement('span', {
                                                        key: f.id || f.flag_key,
                                                        className: 'inline-block whitespace-nowrap px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800',
                                                        title: f.flag_label + (ev ? ' (' + ev + ')' : ''),
                                                    }, f.flag_label || f.flag_key);
                                                })
                                            );
                                        })()
                                    ),
                                    // Actions — two split-button menus, always rendered. Each
                                    // menu opens to a list of quick actions. When a row carries
                                    // a flag, the relevant flag-context quick action is added
                                    // to the menu (no visual highlight — the label itself is
                                    // self-explanatory and the project's dropdown convention
                                    // doesn't decorate items with icons).
                                    React.createElement('td', {className: 'px-4 py-3 whitespace-nowrap text-sm align-top'},
                                        (function() {
                                            var rowFlags = (view && typeof view.flagsFor === 'function') ? view.flagsFor(r.username) : [];
                                            var hasAnyFlag = rowFlags.length > 0;

                                            var auditItems = [
                                                {
                                                    label: 'New Audit',
                                                    onClick: function() { createAudit(r, {count: 5}); },
                                                },
                                                {
                                                    label: 'Audit Last 7 days',
                                                    onClick: function() { createAudit(r, {count: 5, filter: 'last_7_days'}); },
                                                },
                                            ];

                                            var taskItems = [
                                                {
                                                    label: 'New Task',
                                                    onClick: function() { createTask(r, {title: 'Follow-up: ' + name}); },
                                                },
                                            ];
                                            // "Coach on Flag implications" only when there's a flag to
                                            // coach on. Prompt is composed from the row's actual flag
                                            // labels, so it stays specific regardless of which flag(s)
                                            // tripped.
                                            if (hasAnyFlag) {
                                                taskItems.push({
                                                    label: 'Coach on Flag implications',
                                                    onClick: function() {
                                                        var flagLabels = rowFlags.map(function(f) { return f.flag_label || f.flag_key; });
                                                        var flagList = flagLabels.join(', ');
                                                        // This is the INSTRUCTION handed to the OCS coaching
                                                        // assistant (the "Prompt Instructions" field), not the
                                                        // assistant's opening line. It's written as a directive
                                                        // — what to discuss and why — so the bot generates its
                                                        // own natural opener from it.
                                                        var prompt =
                                                            'Coach ' + name + ' about this week\'s nutrition screening. ' +
                                                            'The report flagged: ' + flagList + '. ' +
                                                            'A suspiciously low SAM/MAM rate usually means the worker is only visiting ' +
                                                            'easier-to-reach, better-nourished households and missing the at-risk children ' +
                                                            'who most need screening. Open by acknowledging their effort, explain in plain ' +
                                                            'language what the metric suggests, ask which households they were able to reach ' +
                                                            'this week, and agree on one concrete change for next week. Keep it supportive ' +
                                                            'and specific, never accusatory.';
                                                        createTask(r, {
                                                            title: 'Coaching: ' + flagList + ' — ' + name,
                                                            description: 'Coach ' + name + ' on the report\'s flags: ' + flagList + '.',
                                                            coaching_prompt: prompt,
                                                        });
                                                    },
                                                });
                                            }

                                            // State-aware affordance: when the row already has an
                                            // audit/task created against this run, swap the "Create"
                                            // menu for a plain "View" link. Mirrors the pre-flags
                                            // state-aware buttons — on a saved-run replay the manager
                                            // sees what they did, not what they could do. Picks the
                                            // most recent record if there's more than one.
                                            var rowAudits = (view && typeof view.auditsFor === 'function') ? view.auditsFor(r.username) : [];
                                            var rowTasks = (view && typeof view.tasksFor === 'function') ? view.tasksFor(r.username) : [];
                                            var latestAudit = rowAudits.length ? rowAudits[rowAudits.length - 1] : null;
                                            var latestTask = rowTasks.length ? rowTasks[rowTasks.length - 1] : null;
                                            var viewBtnBase = 'inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium border transition-colors no-underline ';

                                            var auditAffordance = latestAudit
                                                ? React.createElement('a', {
                                                    href: '/audit/' + latestAudit.id + '/' + oppScope,
                                                    className: viewBtnBase + 'border-blue-300 text-blue-700 bg-blue-50 hover:bg-blue-100',
                                                    title: 'Open audit #' + latestAudit.id + ' (' + (latestAudit.status || 'unknown') + ')',
                                                  }, 'View Audit')
                                                : React.createElement(MenuButton, {
                                                    label: 'Create Audit',
                                                    accent: 'blue',
                                                    className: 'border-blue-300 text-blue-700 bg-blue-50 hover:bg-blue-100',
                                                    title: 'Audit options for ' + name,
                                                    items: auditItems,
                                                  });
                                            var taskAffordance = latestTask
                                                ? React.createElement('a', {
                                                    href: '/tasks/' + latestTask.id + '/edit/' + oppScope,
                                                    className: viewBtnBase + 'border-purple-300 text-purple-700 bg-purple-50 hover:bg-purple-100',
                                                    title: 'Open task #' + latestTask.id + ' (' + (latestTask.status || 'unknown') + ')',
                                                  }, 'View Task')
                                                : React.createElement(MenuButton, {
                                                    label: 'Create Task',
                                                    accent: 'purple',
                                                    className: 'border-purple-300 text-purple-700 bg-purple-50 hover:bg-purple-100',
                                                    title: 'Task options for ' + name,
                                                    items: taskItems,
                                                  });

                                            return React.createElement('div', {className: 'flex gap-2'},
                                                auditAffordance,
                                                taskAffordance
                                            );
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
    # Flags are NOT in the snapshot — they're queried live (Flag lifecycle
    # state-of-truth lives on the Flag record itself; exposed via
    # view.flagsFor() in render code).
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": None,  # capture all pipelines on the workflow definition
        "workers": True,
        "state_keys": [],  # this template currently has no state to preserve
    },
}
