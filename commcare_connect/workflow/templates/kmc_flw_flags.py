"""
KMC FLW Flag Report Workflow Template.

Identifies FLWs with concerning performance patterns across case management,
danger signs, and weight tracking. Computes 8 binary flags per FLW from
aggregated pipeline data, displays a sortable flag table, and enables
one-click audit creation with AI review for selected flagged workers.

Two pipelines:
- flw_flags: Per-FLW aggregated metrics (GROUP BY username)
- weight_series: Visit-level weight measurements for weight pair analysis
"""

PIPELINE_SCHEMAS = [
    {
        "alias": "flw_flags",
        "name": "FLW Flag Metrics",
        "description": "Per-FLW aggregated metrics for flag computation",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [
                {
                    "name": "total_cases",
                    "paths": ["form.kmc_beneficiary_case_id", "form.case.@case_id"],
                    "aggregation": "count_distinct",
                },
                {
                    "name": "deaths",
                    "paths": ["form.kmc_beneficiary_case_id", "form.case.@case_id"],
                    "aggregation": "count_distinct",
                    "filter_path": "form.child_alive",
                    "filter_value": "no",
                },
                {
                    "name": "total_visits",
                    "path": "form.grp_kmc_visit.visit_number",
                    "aggregation": "count",
                },
                {
                    "name": "danger_visit_count",
                    "paths": [
                        "form.danger_signs_checklist.danger_sign_positive",
                        "form.child_details.Danger_Signs_Checklist.danger_sign_positive",
                    ],
                    "aggregation": "count",
                },
                {
                    "name": "danger_positive_count",
                    "paths": [
                        "form.danger_signs_checklist.danger_sign_positive",
                        "form.child_details.Danger_Signs_Checklist.danger_sign_positive",
                    ],
                    "aggregation": "count",
                    "filter_path": "form.danger_signs_checklist.danger_sign_positive",
                    "filter_value": "yes",
                },
            ],
        },
    },
    {
        "alias": "weight_series",
        "name": "Weight Series",
        "description": "Per-visit weight measurements for weight pair analysis",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "linking_field": "beneficiary_case_id",
            "fields": [
                {
                    "name": "beneficiary_case_id",
                    "paths": ["form.kmc_beneficiary_case_id", "form.case.@case_id"],
                    "aggregation": "first",
                },
                {
                    # Form-reported visit date — namespaced to avoid shadowing the
                    # base `visit_date` column on labs_raw_visit_cache (which is the
                    # typed date the cache layer ships). The form-extracted value is
                    # exposed under `form_visit_date` so the render reads it explicitly.
                    "name": "form_visit_date",
                    "paths": ["form.grp_kmc_visit.visit_date"],
                    "aggregation": "first",
                    "transform": "date",
                },
                {
                    "name": "weight",
                    "paths": [
                        "form.anthropometric.child_weight_visit",
                        "form.child_details.birth_weight_reg.child_weight_reg",
                    ],
                    "aggregation": "first",
                    "transform": "float",
                },
                {
                    "name": "visit_number",
                    "paths": ["form.grp_kmc_visit.visit_number"],
                    "aggregation": "first",
                    "transform": "int",
                },
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
                {
                    "name": "kmc_status",
                    "paths": ["form.grp_kmc_beneficiary.kmc_status", "form.kmc_status"],
                    "aggregation": "first",
                },
            ],
        },
    },
]

DEFINITION = {
    "name": "KMC FLW Flag Report",
    "description": (
        "Identifies FLWs with concerning performance patterns across "
        "case management, danger signs, and weight tracking."
    ),
    "version": 1,
    "templateType": "kmc_flw_flags",
    "statuses": [
        {"id": "pending", "label": "Pending Review", "color": "gray"},
        {"id": "audits_created", "label": "Audits Created", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],
}

RENDER_CODE = r"""function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    // =========================================================================
    // Constants
    // =========================================================================
    var THRESHOLDS = {
        visits: 3.0,
        mort_low: 0.02,
        mort_high: 0.20,
        enroll: 0.35,
        danger_high: 0.30,
        danger_zero: 0,
        wt_loss: 0.15,
        wt_gain: 60,
        wt_zero: 0.30
    };
    var MIN_CASES = {
        visits: 10,
        mort: 20,
        enroll: 10,
        danger_high: 20,
        danger_zero: 30,
        weight: 10,
        exclude: 20
    };

    // =========================================================================
    // State
    // =========================================================================
    var _sel = React.useState({});
    var selectedWorkers = _sel[0]; var setSelectedWorkers = _sel[1];
    var _sa = React.useState(false);
    var selectAll = _sa[0]; var setSelectAll = _sa[1];
    var _filt = React.useState('all');
    var filter = _filt[0]; var setFilter = _filt[1];
    var _sort = React.useState('flags');
    var sortKey = _sort[0]; var setSortKey = _sort[1];
    var _sortDir = React.useState(false);
    var sortAsc = _sortDir[0]; var setSortAsc = _sortDir[1];
    var _search = React.useState('');
    var search = _search[0]; var setSearch = _search[1];
    var _run = React.useState(false);
    var isRunning = _run[0]; var setIsRunning = _run[1];
    var _prog = React.useState(null);
    var progress = _prog[0]; var setProgress = _prog[1];
    var _ls = React.useState([]);
    var linkedSessions = _ls[0]; var setLinkedSessions = _ls[1];
    var _lsLoading = React.useState(true);
    var loadingSessions = _lsLoading[0]; var setLoadingSessions = _lsLoading[1];
    var _taskId = React.useState(null);
    var taskId = _taskId[0]; var setTaskId = _taskId[1];
    var cleanupRef = React.useRef(null);

    var _showModal = React.useState(false);
    var showModal = _showModal[0]; var setShowModal = _showModal[1];

    // Date computation helpers
    var computeDateRange = function(preset) {
        var now = new Date();
        var dayOfWeek = now.getDay();
        var fmtDate = function(d) {
            return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        };
        if (preset === 'last_week') {
            var mon = new Date(now); mon.setDate(now.getDate() - dayOfWeek - 6);
            var sun = new Date(mon); sun.setDate(mon.getDate() + 6);
            return { start: fmtDate(mon), end: fmtDate(sun) };
        }
        if (preset === 'last_2_weeks') {
            var mon = new Date(now); mon.setDate(now.getDate() - dayOfWeek - 13);
            var sun = new Date(now); sun.setDate(now.getDate() - dayOfWeek);
            return { start: fmtDate(mon), end: fmtDate(sun) };
        }
        if (preset === 'last_month') {
            var firstOfThisMonth = new Date(now.getFullYear(), now.getMonth(), 1);
            var lastOfPrevMonth = new Date(firstOfThisMonth); lastOfPrevMonth.setDate(0);
            var firstOfPrevMonth = new Date(lastOfPrevMonth.getFullYear(), lastOfPrevMonth.getMonth(), 1);
            return { start: fmtDate(firstOfPrevMonth), end: fmtDate(lastOfPrevMonth) };
        }
        return { start: '', end: '' };
    };

    var _datePreset = React.useState('last_week');
    var datePreset = _datePreset[0]; var setDatePreset = _datePreset[1];
    var defaultDates = computeDateRange('last_week');
    var _auditStartDate = React.useState(defaultDates.start);
    var auditStartDate = _auditStartDate[0]; var setAuditStartDate = _auditStartDate[1];
    var _auditEndDate = React.useState(defaultDates.end);
    var auditEndDate = _auditEndDate[0]; var setAuditEndDate = _auditEndDate[1];
    var _countPerFlw = React.useState(10);
    var countPerFlw = _countPerFlw[0]; var setCountPerFlw = _countPerFlw[1];
    var _aiAgent = React.useState('none');
    var aiAgent = _aiAgent[0]; var setAiAgent = _aiAgent[1];
    var _auditTitle = React.useState('');
    var auditTitle = _auditTitle[0]; var setAuditTitle = _auditTitle[1];

    var handlePresetChange = function(preset) {
        setDatePreset(preset);
        if (preset !== 'custom') {
            var range = computeDateRange(preset);
            setAuditStartDate(range.start);
            setAuditEndDate(range.end);
        }
    };

    // =========================================================================
    // Helpers
    // =========================================================================
    var fmt = function(val, type) {
        if (val === null || val === undefined) return null;
        if (type === 'pct') return (val * 100).toFixed(1) + '%';
        if (type === 'dec') return val.toFixed(1);
        if (type === 'gain') return val.toFixed(1) + ' g/d';
        return String(val);
    };

    var FLAG_LABELS = {
        low_visits: 'Low Visits/Case',
        high_mort: 'High Mortality',
        low_mort: 'Low Mortality',
        late_enroll: 'Late Enrollment',
        high_danger: 'High Danger Signs',
        zero_danger: 'Zero Danger Signs',
        high_wt_loss: 'High Weight Loss',
        high_wt_gain: 'High Weight Gain',
        high_wt_zero: 'Zero Weight Change'
    };

    var renderCell = function(val, type, flagKeys, flags) {
        var formatted = fmt(val, type);
        var flagged = false;
        if (flagKeys) {
            (Array.isArray(flagKeys) ? flagKeys : [flagKeys]).forEach(function(k) {
                if (flags[k]) flagged = true;
            });
        }
        var cellClass = 'px-3 py-3 text-sm text-center ' + (flagged ? 'bg-red-50 text-red-800 font-semibold' : '');
        if (formatted === null) {
            return <td className={cellClass} title="Not Eligible — insufficient data for this metric"><span className="text-gray-400 italic">NE</span></td>;
        }
        return <td className={cellClass}>{formatted}</td>;
    };

    // =========================================================================
    // Weight metrics computation
    // =========================================================================
    var computeWeightMetrics = function(username, weightRows) {
        var myRows = (weightRows || []).filter(function(r) { return r.username === username; });
        if (myRows.length === 0) return { pct_wt_loss: null, mean_daily_gain: null, pct_wt_zero: null, weight_pairs: 0 };

        // Group by child
        var byChild = {};
        myRows.forEach(function(r) {
            var cid = r.beneficiary_case_id;
            if (!cid) return;
            if (!byChild[cid]) byChild[cid] = [];
            byChild[cid].push(r);
        });

        var totalPairs = 0;
        var lossPairs = 0;
        var zeroPairs = 0;
        var totalDailyGain = 0;
        var gainPairCount = 0;

        Object.keys(byChild).forEach(function(cid) {
            var visits = byChild[cid]
                .filter(function(v) { return v.weight != null && v.form_visit_date; })
                .sort(function(a, b) {
                    return (a.form_visit_date || '').localeCompare(b.form_visit_date || '');
                });
            for (var i = 1; i < visits.length; i++) {
                var prev = visits[i - 1];
                var curr = visits[i];
                var w1 = parseFloat(prev.weight);
                var w2 = parseFloat(curr.weight);
                if (isNaN(w1) || isNaN(w2)) continue;
                if (w1 < 500 || w1 > 5000 || w2 < 500 || w2 > 5000) continue;
                var d1 = new Date(prev.form_visit_date);
                var d2 = new Date(curr.form_visit_date);
                var daysBetween = (d2 - d1) / (1000 * 60 * 60 * 24);
                if (daysBetween < 1 || daysBetween > 30) continue;
                totalPairs++;
                var diff = w2 - w1;
                if (diff < 0) lossPairs++;
                if (Math.abs(diff) < 0.001) zeroPairs++;
                if (daysBetween > 0) {
                    totalDailyGain += diff / daysBetween;
                    gainPairCount++;
                }
            }
        });

        return {
            pct_wt_loss: totalPairs > 0 ? lossPairs / totalPairs : null,
            mean_daily_gain: gainPairCount > 0 ? totalDailyGain / gainPairCount : null,
            pct_wt_zero: totalPairs > 0 ? zeroPairs / totalPairs : null,
            weight_pairs: totalPairs
        };
    };

    var computeEnrollmentMetrics = function(username, weightRows) {
        var myRows = (weightRows || []).filter(function(r) { return r.username === username; });
        if (myRows.length === 0) return { pctLateEnroll: null, casesWithDates: 0 };

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

    var computeCaseMetrics = function(username, weightRows) {
        var myRows = (weightRows || []).filter(function(r) { return r.username === username; });
        if (myRows.length === 0) return { closedCases: 0, nonMortClosed: 0 };

        // Group by case, get last kmc_status and check child_alive
        var byCase = {};
        myRows.forEach(function(r) {
            var cid = r.beneficiary_case_id;
            if (!cid) return;
            if (!byCase[cid]) byCase[cid] = { kmc_status: null, form_visit_date: null };
            // Keep latest status by visit date
            if (!byCase[cid].form_visit_date
                || (r.form_visit_date && r.form_visit_date > byCase[cid].form_visit_date)) {
                if (r.kmc_status) {
                    byCase[cid].kmc_status = r.kmc_status;
                    byCase[cid].form_visit_date = r.form_visit_date;
                }
            }
        });

        var closedCases = 0;
        var nonMortClosed = 0;
        Object.keys(byCase).forEach(function(cid) {
            var status = byCase[cid].kmc_status;
            if (status === 'discharged' || status === 'lost_to_followup' || status === 'deceased') {
                closedCases++;
                if (status !== 'deceased') {
                    nonMortClosed++;
                }
            }
        });

        return { closedCases: closedCases, nonMortClosed: nonMortClosed };
    };

    // =========================================================================
    // Data processing
    // =========================================================================
    var flagRows = (pipelines && pipelines.flw_flags && pipelines.flw_flags.rows) || [];
    var weightRows = (pipelines && pipelines.weight_series && pipelines.weight_series.rows) || [];

    var processedData = React.useMemo(function() {
        return flagRows.map(function(row) {
            var u = row.username;
            var totalCases = parseInt(row.total_cases) || 0;
            var deaths = parseInt(row.deaths) || 0;
            var totalVisits = parseInt(row.total_visits) || 0;
            var dangerVisitCount = parseInt(row.danger_visit_count) || 0;
            var dangerPositiveCount = parseInt(row.danger_positive_count) || 0;

            // Weight metrics
            var wm = computeWeightMetrics(u, weightRows);
            var em = computeEnrollmentMetrics(u, weightRows);
            var cm = computeCaseMetrics(u, weightRows);

            var closedCases = cm.closedCases;
            var nonMortClosed = cm.nonMortClosed;
            var avgVisits = nonMortClosed > 0 ? totalVisits / nonMortClosed : null;
            var mortRate = totalCases > 0 ? deaths / totalCases : null;
            var dangerRate = dangerVisitCount > 0 ? dangerPositiveCount / dangerVisitCount : null;

            // Compute flags
            var excluded = totalCases < MIN_CASES.exclude;
            var flags = {};
            if (!excluded) {
                flags.low_visits = closedCases >= MIN_CASES.visits && avgVisits !== null && avgVisits < THRESHOLDS.visits;
                flags.high_mort = totalCases >= MIN_CASES.mort && mortRate !== null && mortRate > THRESHOLDS.mort_high;
                flags.low_mort = totalCases >= MIN_CASES.mort && mortRate !== null && mortRate < THRESHOLDS.mort_low;
                flags.late_enroll = em.casesWithDates >= MIN_CASES.enroll && em.pctLateEnroll !== null && em.pctLateEnroll > THRESHOLDS.enroll;
                flags.high_danger = dangerVisitCount >= MIN_CASES.danger_high && dangerRate !== null && dangerRate > THRESHOLDS.danger_high;
                flags.zero_danger = dangerVisitCount >= MIN_CASES.danger_zero && dangerRate !== null && dangerRate === THRESHOLDS.danger_zero;
                flags.high_wt_loss = wm.weight_pairs >= MIN_CASES.weight && wm.pct_wt_loss !== null && wm.pct_wt_loss > THRESHOLDS.wt_loss;
                flags.high_wt_gain = wm.weight_pairs >= MIN_CASES.weight && wm.mean_daily_gain !== null && wm.mean_daily_gain > THRESHOLDS.wt_gain;
                flags.high_wt_zero = wm.weight_pairs >= MIN_CASES.weight && wm.pct_wt_zero !== null && wm.pct_wt_zero > THRESHOLDS.wt_zero;
            }
            var flagCount = Object.values(flags).filter(Boolean).length;

            var workerInfo = (workers || []).find(function(w) { return w.username === u; });
            return {
                username: u,
                name: (workerInfo && workerInfo.name) || row.display_name || u,
                totalCases: totalCases,
                closedCases: closedCases,
                avgVisits: avgVisits,
                mortRate: mortRate,
                pctLateEnroll: em.pctLateEnroll,
                casesWithDates: em.casesWithDates,
                dangerRate: dangerRate,
                pctWtLoss: wm.pct_wt_loss,
                meanDailyGain: wm.mean_daily_gain,
                pctWtZero: wm.pct_wt_zero,
                weightPairs: wm.weight_pairs,
                flags: flags,
                flagCount: flagCount,
                excluded: excluded
            };
        });
    }, [flagRows, weightRows, workers]);

    // Filter and sort
    var filteredData = React.useMemo(function() {
        var data = processedData.filter(function(d) { return !d.excluded; });

        // Search filter
        if (search.trim()) {
            var q = search.toLowerCase();
            data = data.filter(function(d) {
                return (d.username && d.username.toLowerCase().indexOf(q) >= 0) ||
                       (d.name && d.name.toLowerCase().indexOf(q) >= 0);
            });
        }

        // Flag filter
        if (filter === 'any_flag') {
            data = data.filter(function(d) { return d.flagCount > 0; });
        } else if (filter === 'two_plus') {
            data = data.filter(function(d) { return d.flagCount >= 2; });
        }

        // Sort
        data.sort(function(a, b) {
            var va, vb;
            switch (sortKey) {
                case 'name': va = a.name || ''; vb = b.name || ''; break;
                case 'cases': va = a.totalCases; vb = b.totalCases; break;
                case 'avg_visits': va = a.avgVisits || 0; vb = b.avgVisits || 0; break;
                case 'mort': va = a.mortRate || 0; vb = b.mortRate || 0; break;
                case 'enroll': va = a.pctLateEnroll || 0; vb = b.pctLateEnroll || 0; break;
                case 'danger': va = a.dangerRate || 0; vb = b.dangerRate || 0; break;
                case 'wt_loss': va = a.pctWtLoss || 0; vb = b.pctWtLoss || 0; break;
                case 'wt_gain': va = a.meanDailyGain || 0; vb = b.meanDailyGain || 0; break;
                case 'wt_zero': va = a.pctWtZero || 0; vb = b.pctWtZero || 0; break;
                default: va = a.flagCount; vb = b.flagCount;
            }
            if (typeof va === 'string') {
                var cmp = va.localeCompare(vb);
                return sortAsc ? cmp : -cmp;
            }
            return sortAsc ? va - vb : vb - va;
        });

        return data;
    }, [processedData, filter, search, sortKey, sortAsc]);

    // KPI calculations
    var excludedCount = processedData.filter(function(d) { return d.excluded; }).length;
    var analyzedCount = processedData.filter(function(d) { return !d.excluded; }).length;
    var twoFlagCount = processedData.filter(function(d) { return !d.excluded && d.flagCount >= 2; }).length;
    var totalCasesAll = processedData.reduce(function(s, d) { return s + d.totalCases; }, 0);

    // Selection helpers
    var selectedCount = Object.values(selectedWorkers).filter(Boolean).length;

    var toggleWorker = function(username) {
        setSelectedWorkers(function(prev) {
            var next = Object.assign({}, prev);
            next[username] = !prev[username];
            return next;
        });
    };

    var handleSelectAll = function() {
        var newState = !selectAll;
        setSelectAll(newState);
        var newSelected = {};
        filteredData.forEach(function(d) { newSelected[d.username] = newState; });
        setSelectedWorkers(newSelected);
    };

    var handleSort = function(key) {
        if (sortKey === key) {
            setSortAsc(!sortAsc);
        } else {
            setSortKey(key);
            setSortAsc(false);
        }
    };

    // =========================================================================
    // Fetch linked sessions on mount
    // =========================================================================
    React.useEffect(function() {
        if (!instance.id) {
            setLoadingSessions(false);
            return;
        }
        setLoadingSessions(true);
        fetch('/audit/api/workflow/' + instance.id + '/sessions/')
            .then(function(res) { return res.json(); })
            .then(function(data) {
                if (data.success && data.sessions) {
                    setLinkedSessions(data.sessions);
                }
                setLoadingSessions(false);
            })
            .catch(function(err) {
                console.error('Failed to load linked sessions:', err);
                setLoadingSessions(false);
            });
    }, [instance.id]);

    var hasLinkedSessions = linkedSessions.length > 0;

    // =========================================================================
    // Audit creation handler
    // =========================================================================
    var handleCreateAudits = function() {
        if (isRunning || selectedCount === 0) return;
        setShowModal(false);
        setIsRunning(true);
        setProgress({ status: 'starting', message: 'Preparing audit creation...' });

        var selectedUsernames = Object.keys(selectedWorkers).filter(function(k) {
            return selectedWorkers[k];
        });

        var urlParams = new URLSearchParams(window.location.search);
        var opportunityId = parseInt(urlParams.get('opportunity_id')) || (instance && instance.opportunity_id);
        var opportunityName = (instance.state && instance.state.opportunity_name) || '';

        var relatedFields = [
            {
                imagePath: 'anthropometric/upload_weight_image',
                fieldPath: 'child_weight_visit',
                label: 'Weight Reading',
                filter_by_image: false,
                filter_by_field: false
            }
        ];

        var title = auditTitle || ('FLW Flag Audit ' + auditStartDate + ' to ' + auditEndDate);

        var criteria = {
            audit_type: 'date_range',
            granularity: 'per_flw',
            title: title,
            start_date: auditStartDate,
            end_date: auditEndDate,
            count_per_flw: countPerFlw,
            related_fields: relatedFields,
            selected_flw_user_ids: selectedUsernames
        };

        actions.createAudit({
            opportunities: [{ id: opportunityId, name: opportunityName || 'KMC' }],
            criteria: criteria,
            workflow_run_id: instance.id,
            ai_agent_id: aiAgent === 'none' ? undefined : aiAgent
        }).then(function(result) {
            if (result.success && result.task_id) {
                setTaskId(result.task_id);

                onUpdateState({
                    active_job: {
                        job_id: result.task_id,
                        status: 'running',
                        started_at: new Date().toISOString()
                    }
                }).catch(function(err) { console.warn('Failed to persist active_job:', err); });

                var cleanup = actions.streamAuditProgress(
                    result.task_id,
                    function(progressData) {
                        setProgress(progressData);
                    },
                    function(finalResult) {
                        setIsRunning(false);
                        setProgress({ status: 'completed', message: 'Audits created successfully' });

                        onUpdateState({
                            status: 'audits_created',
                            active_job: {
                                job_id: result.task_id,
                                status: 'completed',
                                completed_at: new Date().toISOString()
                            }
                        }).catch(function(err) { console.warn('Failed to update state:', err); });

                        fetch('/audit/api/workflow/' + instance.id + '/sessions/')
                            .then(function(res) { return res.json(); })
                            .then(function(data) {
                                if (data.success && data.sessions) {
                                    setLinkedSessions(data.sessions);
                                }
                            })
                            .catch(function(err) { console.error('Failed to refresh sessions:', err); });
                    },
                    function(error) {
                        setIsRunning(false);
                        setProgress({ status: 'failed', error: error });
                        onUpdateState({
                            active_job: {
                                job_id: result.task_id,
                                status: 'failed',
                                error: error
                            }
                        }).catch(function(err) { console.warn('Failed to update state:', err); });
                    }
                );
                cleanupRef.current = cleanup;
            } else {
                setIsRunning(false);
                setProgress({ status: 'failed', error: result.error || 'Failed to start audit creation' });
            }
        }).catch(function(err) {
            setIsRunning(false);
            setProgress({ status: 'failed', error: err.message || 'Unknown error' });
        });
    };

    // =========================================================================
    // Sort indicator
    // =========================================================================
    var SortArrow = function(key) {
        if (sortKey !== key) return null;
        return React.createElement('span', { className: 'ml-1 text-xs' }, sortAsc ? '\u25B2' : '\u25BC');
    };

    var thClass = 'px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 select-none whitespace-nowrap';

    // =========================================================================
    // Render
    // =========================================================================

    // No pipeline data yet
    if (flagRows.length === 0) {
        return (
            <div className="space-y-6">
                <div className="bg-white rounded-lg shadow-sm p-6">
                    <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                    <p className="text-gray-600 mt-1">{definition.description}</p>
                </div>
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-8 text-center">
                    <i className="fa-solid fa-chart-bar text-gray-300 text-4xl mb-3"></i>
                    <p className="text-gray-500 text-lg font-medium">No pipeline data available</p>
                    <p className="text-gray-400 text-sm mt-1">
                        Run the pipeline to generate FLW flag metrics.
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6 pb-24">
            {/* Header */}
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">
                    <i className="fa-solid fa-flag text-red-500 mr-2"></i>
                    {definition.name}
                </h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
            </div>

            {/* KPI Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="bg-white rounded-lg shadow-sm p-5 border-l-4 border-blue-500">
                    <div className="text-3xl font-bold text-gray-900">{analyzedCount}</div>
                    <div className="text-sm text-gray-600 mt-1">FLWs Analyzed</div>
                </div>
                <div className="bg-white rounded-lg shadow-sm p-5 border-l-4 border-red-500">
                    <div className="text-3xl font-bold text-gray-900">{twoFlagCount}</div>
                    <div className="text-sm text-gray-600 mt-1">With 2+ Flags</div>
                </div>
                <div className="bg-white rounded-lg shadow-sm p-5 border-l-4 border-gray-400">
                    <div className="text-3xl font-bold text-gray-900">{excludedCount}</div>
                    <div className="text-sm text-gray-600 mt-1">{'Excluded (<20 cases)'}</div>
                </div>
                <div className="bg-white rounded-lg shadow-sm p-5 border-l-4 border-green-500">
                    <div className="text-3xl font-bold text-gray-900">{totalCasesAll.toLocaleString()}</div>
                    <div className="text-sm text-gray-600 mt-1">Total Cases</div>
                </div>
            </div>

            {/* Filter Bar */}
            <div className="bg-white rounded-lg shadow-sm p-4">
                <div className="flex flex-wrap items-center gap-3">
                    <div className="flex gap-2">
                        {[
                            { id: 'all', label: 'All FLWs', count: analyzedCount },
                            { id: 'any_flag', label: 'Any Flag', count: processedData.filter(function(d) { return !d.excluded && d.flagCount > 0; }).length },
                            { id: 'two_plus', label: '2+ Flags', count: twoFlagCount }
                        ].map(function(f) {
                            return (
                                <button
                                    key={f.id}
                                    onClick={function() { setFilter(f.id); }}
                                    className={
                                        'px-3 py-1.5 text-sm rounded-full border transition-colors ' +
                                        (filter === f.id
                                            ? 'bg-blue-600 text-white border-blue-600'
                                            : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400')
                                    }
                                >
                                    {f.label} ({f.count})
                                </button>
                            );
                        })}
                    </div>
                    <div className="flex-1 min-w-48">
                        <input
                            type="text"
                            placeholder="Search FLWs..."
                            value={search}
                            onChange={function(e) { setSearch(e.target.value); }}
                            className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
                        />
                    </div>
                </div>
            </div>

            {/* Linked Sessions (if any) */}
            {!loadingSessions && hasLinkedSessions && (
                <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                    <div className="px-6 py-4 bg-green-50 border-b border-green-100">
                        <h2 className="text-lg font-semibold text-green-800 flex items-center gap-2">
                            <i className="fa-solid fa-check-circle"></i>
                            Audit Sessions Created
                        </h2>
                        <p className="text-sm text-green-600 mt-1">
                            {linkedSessions.length} session{linkedSessions.length !== 1 ? 's' : ''} linked to this workflow run
                        </p>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="min-w-full divide-y divide-gray-200">
                            <thead className="bg-gray-50">
                                <tr>
                                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">FLW</th>
                                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Visits</th>
                                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                                    <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                                        <span className="text-green-600">Pass</span>{' / '}<span className="text-red-600">Fail</span>
                                    </th>
                                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
                                </tr>
                            </thead>
                            <tbody className="bg-white divide-y divide-gray-200">
                                {linkedSessions.map(function(session) {
                                    var stats = session.assessment_stats || {};
                                    return (
                                        <tr key={session.id} className="hover:bg-gray-50">
                                            <td className="px-4 py-4">
                                                <div className="text-sm font-medium text-gray-900">
                                                    {session.flw_display_name || session.flw_username || 'Unknown'}
                                                </div>
                                            </td>
                                            <td className="px-4 py-4 text-center">
                                                <span className="text-sm font-medium text-blue-600">{session.visit_count || 0}</span>
                                            </td>
                                            <td className="px-4 py-4 text-center">
                                                <span className={
                                                    'px-2 py-1 text-xs font-medium rounded ' +
                                                    (session.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700')
                                                }>
                                                    {session.status === 'completed' ? 'Completed' : 'In Progress'}
                                                </span>
                                            </td>
                                            <td className="px-4 py-4 text-center">
                                                <div className="flex items-center justify-center gap-2 text-sm">
                                                    <span className="text-green-600 font-medium">{stats.pass || 0}</span>
                                                    <span className="text-gray-400">/</span>
                                                    <span className="text-red-600 font-medium">{stats.fail || 0}</span>
                                                </div>
                                            </td>
                                            <td className="px-4 py-4 text-right">
                                                <a
                                                    href={'/audit/' + session.id + '/bulk/?opportunity_id=' + session.opportunity_id}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className={'inline-flex items-center px-3 py-1.5 text-sm ' +
                                                        'bg-blue-50 text-blue-700 rounded hover:bg-blue-100 ' +
                                                        'border border-blue-200 transition-colors'}
                                                >
                                                    <i className="fa-solid fa-arrow-up-right-from-square mr-1.5"></i>
                                                    Review
                                                </a>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* Flag Table */}
            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
                    <h2 className="text-lg font-semibold text-gray-900">
                        FLW Flag Analysis
                    </h2>
                    <span className="text-sm text-gray-500">
                        {filteredData.length} FLW{filteredData.length !== 1 ? 's' : ''} shown
                    </span>
                </div>
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-3 py-3 text-left w-10">
                                    <input
                                        type="checkbox"
                                        checked={selectAll}
                                        onChange={handleSelectAll}
                                        disabled={isRunning}
                                        className="rounded border-gray-300"
                                    />
                                </th>
                                <th className={thClass} onClick={function() { handleSort('name'); }} title="FLW username or display name">
                                    FLW{SortArrow('name')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('cases'); }} title="Total distinct beneficiary cases">
                                    Cases{SortArrow('cases')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('avg_visits'); }} title="Avg visits per closed non-mortality case. Flag: < 3.0">
                                    Visits/Case{SortArrow('avg_visits')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('mort'); }} title="Deaths as % of total cases. Flag: < 2% or > 20%">
                                    Mortality{SortArrow('mort')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('enroll'); }} title="% of cases enrolled 8+ days after hospital discharge. Flag: > 35%">
                                    Late Enroll{SortArrow('enroll')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('danger'); }} title="% of visits with danger sign positive. Flag: > 30% or 0% across 30+ visits">
                                    Danger Signs{SortArrow('danger')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('wt_loss'); }} title="% of successive visit weight pairs showing decrease. Flag: > 15%">
                                    Wt Loss %{SortArrow('wt_loss')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('wt_gain'); }} title="Mean daily weight gain in grams/day. Flag: > 60 g/day">
                                    Gain (g/d){SortArrow('wt_gain')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('wt_zero'); }} title="% of visit pairs with exactly zero weight change. Flag: > 30%">
                                    Wt Zero %{SortArrow('wt_zero')}
                                </th>
                                <th className={thClass} onClick={function() { handleSort('flags'); }} title="Total number of flags triggered">
                                    Flags{SortArrow('flags')}
                                </th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {filteredData.map(function(d) {
                                var borderColor = d.flagCount >= 2 ? 'border-l-4 border-red-500' :
                                    d.flagCount === 1 ? 'border-l-4 border-orange-400' : '';
                                return (
                                    <tr key={d.username} className={(selectedWorkers[d.username] ? 'bg-blue-50 ' : 'hover:bg-gray-50 ') + borderColor}>
                                        <td className="px-3 py-3">
                                            <input
                                                type="checkbox"
                                                checked={!!selectedWorkers[d.username]}
                                                onChange={function() { toggleWorker(d.username); }}
                                                disabled={isRunning}
                                                className="rounded border-gray-300"
                                            />
                                        </td>
                                        <td className="px-3 py-3 text-sm">
                                            <div className="font-medium text-gray-900">{d.name}</div>
                                            {d.name !== d.username && (
                                                <div className="text-xs text-gray-400 font-mono">{d.username}</div>
                                            )}
                                        </td>
                                        <td className="px-3 py-3 text-sm text-center">{d.totalCases}</td>
                                        {renderCell(d.avgVisits, 'dec', 'low_visits', d.flags)}
                                        {renderCell(d.mortRate, 'pct', ['high_mort', 'low_mort'], d.flags)}
                                        {renderCell(d.pctLateEnroll, 'pct', 'late_enroll', d.flags)}
                                        <td className={'px-3 py-3 text-sm text-center ' + (d.flags.high_danger ? 'bg-red-50 text-red-800 font-semibold' : d.flags.zero_danger ? 'bg-amber-50 text-amber-800 font-semibold' : '')}>
                                            {fmt(d.dangerRate, 'pct') === null ? <span className="text-gray-400 italic" title="Not Eligible — insufficient data for this metric">NE</span> : fmt(d.dangerRate, 'pct')}
                                            {d.flags.zero_danger && !d.flags.high_danger && <span className="ml-1 text-xs" title="Zero danger signs across 30+ visits">&#9888;</span>}
                                        </td>
                                        {renderCell(d.pctWtLoss, 'pct', 'high_wt_loss', d.flags)}
                                        {renderCell(d.meanDailyGain, 'gain', 'high_wt_gain', d.flags)}
                                        {renderCell(d.pctWtZero, 'pct', 'high_wt_zero', d.flags)}
                                        <td className="px-3 py-3 text-sm text-center">
                                            {d.flagCount > 0 ? (
                                                <span
                                                    className={
                                                        'inline-flex items-center justify-center w-7 h-7 rounded-full text-white text-xs font-bold cursor-help ' +
                                                        (d.flagCount >= 2 ? 'bg-red-500' : 'bg-orange-400')
                                                    }
                                                    title={Object.keys(d.flags).filter(function(k) { return d.flags[k]; }).map(function(k) { return FLAG_LABELS[k] || k; }).join(', ')}
                                                >
                                                    {d.flagCount}
                                                </span>
                                            ) : (
                                                <span className="text-gray-300">0</span>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Sticky Action Bar */}
            <div className="sticky bottom-0 bg-white border-t border-gray-200 shadow-lg p-4 -mx-4 sm:-mx-6 lg:-mx-8">
                <div className="max-w-7xl mx-auto flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <span className="text-sm text-gray-600">
                            {selectedCount} FLW{selectedCount !== 1 ? 's' : ''} selected
                        </span>
                        {progress && (
                            <div className="flex items-center gap-2 text-sm">
                                {progress.status === 'starting' && (
                                    <span className="text-blue-600">
                                        <i className="fa-solid fa-spinner fa-spin mr-1"></i>
                                        {progress.message}
                                    </span>
                                )}
                                {progress.status === 'running' && (
                                    <span className="text-blue-600">
                                        <i className="fa-solid fa-spinner fa-spin mr-1"></i>
                                        {progress.message || 'Creating audits...'}
                                    </span>
                                )}
                                {progress.status === 'completed' && (
                                    <div className="flex items-center gap-3 flex-wrap">
                                        <span className="text-green-600 flex items-center">
                                            <i className="fa-solid fa-check mr-1"></i>
                                            Audits created
                                        </span>
                                        {linkedSessions.map(function(session) {
                                            return (
                                                <a
                                                    key={session.id}
                                                    href={'/audit/' + session.id + '/bulk/?opportunity_id=' + session.opportunity_id}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="inline-flex items-center px-3 py-1 text-sm bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100 border border-blue-200 transition-colors"
                                                >
                                                    <i className="fa-solid fa-arrow-up-right-from-square mr-1.5 text-xs"></i>
                                                    {session.flw_display_name || session.flw_username || 'Audit ' + session.id}
                                                </a>
                                            );
                                        })}
                                    </div>
                                )}
                                {progress.status === 'failed' && (
                                    <span className="text-red-600">
                                        <i className="fa-solid fa-exclamation-triangle mr-1"></i>
                                        {progress.error || 'Failed'}
                                    </span>
                                )}
                            </div>
                        )}
                    </div>
                    <button
                        onClick={function() { setShowModal(true); }}
                        disabled={selectedCount === 0 || isRunning || instance.status === 'completed'}
                        title={instance.status === 'completed' ? 'Run is completed; cannot create new audits.' : ''}
                        className={
                            'px-5 py-2.5 rounded-lg text-sm font-medium transition-colors ' +
                            (selectedCount === 0 || isRunning || instance.status === 'completed'
                                ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                                : 'bg-red-600 text-white hover:bg-red-700')
                        }
                    >
                        {isRunning ? (
                            <span>
                                <i className="fa-solid fa-spinner fa-spin mr-2"></i>
                                Creating Audits...
                            </span>
                        ) : (
                            <span>
                                <i className="fa-solid fa-plus mr-2"></i>
                                Create Audits with AI Review ({selectedCount})
                            </span>
                        )}
                    </button>
                </div>
            </div>

            {/* Audit Configuration Modal */}
            {showModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50" onClick={function(e) { if (e.target === e.currentTarget) setShowModal(false); }}>
                    <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
                        <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
                            <h3 className="text-lg font-semibold text-gray-900">
                                <i className="fa-solid fa-cog mr-2 text-gray-500"></i>
                                Configure Audit
                            </h3>
                            <p className="text-sm text-gray-500 mt-1">
                                Creating audits for {selectedCount} selected FLW{selectedCount !== 1 ? 's' : ''}
                            </p>
                        </div>
                        <div className="px-6 py-5 space-y-5">
                            {/* Date Range */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">Date Range</label>
                                <div className="flex flex-wrap gap-2 mb-3">
                                    {[
                                        { id: 'last_week', label: 'Last Week' },
                                        { id: 'last_2_weeks', label: 'Last 2 Weeks' },
                                        { id: 'last_month', label: 'Last Month' },
                                        { id: 'custom', label: 'Custom' }
                                    ].map(function(p) {
                                        return (
                                            <button
                                                key={p.id}
                                                onClick={function() { handlePresetChange(p.id); }}
                                                className={
                                                    'px-3 py-1.5 text-sm rounded-lg border transition-colors ' +
                                                    (datePreset === p.id
                                                        ? 'bg-blue-600 text-white border-blue-600'
                                                        : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400')
                                                }
                                            >
                                                {p.label}
                                            </button>
                                        );
                                    })}
                                </div>
                                <div className="flex gap-3">
                                    <div className="flex-1">
                                        <label className="block text-xs text-gray-500 mb-1">Start</label>
                                        <input
                                            type="date"
                                            value={auditStartDate}
                                            onChange={function(e) { setAuditStartDate(e.target.value); setDatePreset('custom'); }}
                                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                        />
                                    </div>
                                    <div className="flex-1">
                                        <label className="block text-xs text-gray-500 mb-1">End</label>
                                        <input
                                            type="date"
                                            value={auditEndDate}
                                            onChange={function(e) { setAuditEndDate(e.target.value); setDatePreset('custom'); }}
                                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                        />
                                    </div>
                                </div>
                            </div>

                            {/* Visits per FLW */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">Visits to Review per FLW</label>
                                <input
                                    type="number"
                                    min="1"
                                    max="50"
                                    value={countPerFlw}
                                    onChange={function(e) { setCountPerFlw(parseInt(e.target.value) || 10); }}
                                    className="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                />
                                <span className="text-xs text-gray-500 ml-2">visits per FLW in the date range</span>
                            </div>

                            {/* AI Agent */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">AI Review Agent</label>
                                <select
                                    value={aiAgent}
                                    onChange={function(e) { setAiAgent(e.target.value); }}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                >
                                    <option value="scale_validation">Scale Validation (weight image review)</option>
                                    <option value="none">No AI Review</option>
                                </select>
                            </div>

                            {/* Title */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">Audit Title (optional)</label>
                                <input
                                    type="text"
                                    value={auditTitle}
                                    onChange={function(e) { setAuditTitle(e.target.value); }}
                                    placeholder={'FLW Flag Audit ' + auditStartDate + ' to ' + auditEndDate}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                />
                            </div>
                        </div>
                        <div className="px-6 py-4 bg-gray-50 border-t border-gray-200 flex justify-end gap-3">
                            <button
                                onClick={function() { setShowModal(false); }}
                                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleCreateAudits}
                                disabled={!auditStartDate || !auditEndDate}
                                className={
                                    'px-5 py-2 text-sm font-medium rounded-lg transition-colors ' +
                                    (!auditStartDate || !auditEndDate
                                        ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                                        : 'bg-red-600 text-white hover:bg-red-700')
                                }
                            >
                                <i className="fa-solid fa-plus mr-2"></i>
                                Create Audits ({selectedCount})
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}"""

TEMPLATE = {
    "key": "kmc_flw_flags",
    "name": "KMC FLW Flag Report",
    "description": (
        "Identifies FLWs with concerning performance patterns. "
        "Select flagged FLWs to create targeted audits with AI review."
    ),
    "icon": "fa-flag",
    "color": "red",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
