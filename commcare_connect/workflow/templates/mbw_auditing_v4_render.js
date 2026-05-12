function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {

    // =========================================================================
    // Constants
    // =========================================================================
    var THRESHOLDS = {
        gs_red: 50,                 // GS Score below this → red flag
        fu_red: 50,                 // Follow-up rate below this → red flag
        fu_yellow: 80,              // Follow-up rate below this (but >= fu_red) → yellow flag
        pct_still_elig_red: 50,     // % Still Eligible below this → red flag
        pct_still_elig_yellow: 85,  // % Still Eligible below this (but >= red) → yellow flag
        ebf_low: 30,                // EBF at/below this → yellow flag (green range is 31-89%)
        ebf_high: 89,               // EBF above this → yellow flag
        dist_ratio_low: 1.0,        // Dist ratio below this → GPS clustering yellow flag
        worsened_pct: 10,           // Metric worsened by this % vs last run → yellow flag
    };

    var PERF_CATEGORIES = [
        { id: 'eligible_for_renewal', label: 'Eligible for Renewal', color: 'green' },
        { id: 'requires_improvement', label: 'Requires Improvement', color: 'yellow' },
        { id: 'suspended', label: 'Suspension', color: 'red' },
    ];

    var METRIC_COLS = [
        { key: 'gs_score',           label: 'GS Score',        fmt: 'pct',  higherBetter: true  },
        { key: 'followup_rate',      label: 'Follow-up Rate',  fmt: 'pct',  higherBetter: true  },
        { key: 'pct_still_eligible', label: '% Still Eligible',fmt: 'pct',  higherBetter: true  },
        { key: 'ebf_pct',            label: 'EBF %',           fmt: 'pct',  higherBetter: true  },
        { key: 'revisit_dist',       label: 'Revisit Dist (km)',fmt: 'dec', higherBetter: false  },
        { key: 'meter_per_visit',    label: 'Meter/Visit',     fmt: 'int',  higherBetter: null  },
        { key: 'dist_ratio',         label: 'Dist Ratio',      fmt: 'dec',  higherBetter: true  },
        { key: 'minute_per_visit',   label: 'Minute/Visit',    fmt: 'int',  higherBetter: null  },
    ];

    // =========================================================================
    // State
    // =========================================================================
    var savedResults = instance.state && instance.state.worker_results ? instance.state.worker_results : {};
    var savedTaskStates = instance.state && instance.state.task_states ? instance.state.task_states : {};
    var prevMetrics = instance.state && instance.state.previous_metrics ? instance.state.previous_metrics : {};

    var _step = React.useState('idle');
    var step = _step[0]; var setStep = _step[1];

    var _dashData = React.useState(null);
    var dashData = _dashData[0]; var setDashData = _dashData[1];

    var _jobMessages = React.useState([]);
    var jobMessages = _jobMessages[0]; var setJobMessages = _jobMessages[1];

    var _jobError = React.useState(null);
    var jobError = _jobError[0]; var setJobError = _jobError[1];

    var _activeTab = React.useState('audit');
    var activeTab = _activeTab[0]; var setActiveTab = _activeTab[1];

    var _workerResults = React.useState(savedResults);
    var workerResults = _workerResults[0]; var setWorkerResults = _workerResults[1];

    var _taskStates = React.useState(savedTaskStates);
    var taskStates = _taskStates[0]; var setTaskStates = _taskStates[1];

    var _sortCol = React.useState('flags');
    var sortCol = _sortCol[0]; var setSortCol = _sortCol[1];

    var _sortAsc = React.useState(false);
    var sortAsc = _sortAsc[0]; var setSortAsc = _sortAsc[1];

    var _search = React.useState('');
    var search = _search[0]; var setSearch = _search[1];

    var _filterFlag = React.useState('all');
    var filterFlag = _filterFlag[0]; var setFilterFlag = _filterFlag[1];

    var _savingUser = React.useState(null);
    var savingUser = _savingUser[0]; var setSavingUser = _savingUser[1];

    var _perfData = React.useState(null);
    var perfData = _perfData[0]; var setPerfData = _perfData[1];

    var _concludeModal = React.useState(false);
    var concludeModal = _concludeModal[0]; var setConcludeModal = _concludeModal[1];

    var _concluding = React.useState(false);
    var concluding = _concluding[0]; var setConcluding = _concluding[1];

    var _notesModal = React.useState(null);
    var notesModal = _notesModal[0]; var setNotesModal = _notesModal[1];

    var _notesDraft = React.useState('');
    var notesDraft = _notesDraft[0]; var setNotesDraft = _notesDraft[1];

    var _savingNotes = React.useState(false);
    var savingNotes = _savingNotes[0]; var setSavingNotes = _savingNotes[1];

    var _tab2Step = React.useState('idle');
    var tab2Step = _tab2Step[0]; var setTab2Step = _tab2Step[1];

    var _tab2Data = React.useState(null);
    var tab2Data = _tab2Data[0]; var setTab2Data = _tab2Data[1];

    var _refreshingTasks = React.useState(false);
    var refreshingTasks = _refreshingTasks[0]; var setRefreshingTasks = _refreshingTasks[1];


    var jobCleanupRef = React.useRef(null);
    var tab2CleanupRef = React.useRef(null);

    // =========================================================================
    // Derived helpers
    // =========================================================================
    var flwNameMap = React.useMemo(function() {
        var m = {};
        (workers || []).forEach(function(w) {
            if (w.username) m[w.username.toLowerCase()] = w.name || w.username;
        });
        return m;
    }, [workers]);

    // =========================================================================
    // Job runner — server fetches all pipeline data (no browser round-trip)
    // =========================================================================
    var runAnalysis = React.useCallback(function() {
        if (!actions || !actions.startJob) return;
        if (step === 'running') return;

        setStep('running');
        setJobError(null);
        setJobMessages(['Starting analysis...']);
        setDashData(null);

        var allUsernames = (workers || []).map(function(w) { return w.username; });

        actions.startJob(instance.id, {
            job_type: 'mbw_auditing_v4',
            active_usernames: allUsernames,
            flw_names: flwNameMap,
            flw_statuses: workerResults,
            opportunity_id: instance.opportunity_id,
        }).then(function(resp) {
            if (!resp || !resp.success) {
                setStep('error');
                setJobError((resp && resp.error) || 'Failed to start analysis job');
                return;
            }
            var taskId = resp.task_id;
            if (!taskId) {
                setStep('error');
                setJobError('No task ID returned');
                return;
            }

            var cleanup = actions.streamJobProgress(
                taskId,
                function(data) {
                    if (data.message) setJobMessages(function(p) { return p.concat([data.message]); });
                },
                null,
                function(results) {
                    // Server computed all metrics — merge last_active from workers prop
                    var workerMap = {};
                    (workers || []).forEach(function(w) {
                        workerMap[(w.username || '').toLowerCase()] = w;
                    });
                    var flwSummaries = (results.flw_summaries || []).map(function(s) {
                        var w = workerMap[s.username] || {};
                        return Object.assign({}, s, {
                            last_active: w.last_active || s.last_active || '',
                            display_name: s.display_name || w.name || s.username,
                        });
                    });
                    setDashData({ flw_summaries: flwSummaries });
                    setStep('ready');
                    onUpdateState({ analysis_complete: true, analysis_ts: new Date().toISOString() })
                        .catch(function(e) { console.warn('state save failed:', e); });
                },
                function(error) { setStep('error'); setJobError(error || 'Analysis failed'); },
                function() { setStep('error'); setJobError('Analysis was cancelled'); }
            );
            jobCleanupRef.current = cleanup;
        }).catch(function(err) {
            setStep('error');
            setJobError((err && err.message) || 'Failed to start job');
        });
    }, [step, workers, flwNameMap, workerResults, instance.id, instance.opportunity_id, actions, onUpdateState]);

    React.useEffect(function() {
        if (!dashData) {
            runAnalysis();
        }
    }, []);

    React.useEffect(function() {
        return function() { if (jobCleanupRef.current) jobCleanupRef.current(); };
    }, []);

    React.useEffect(function() {
        return function() { if (tab2CleanupRef.current) tab2CleanupRef.current(); };
    }, []);

    // =========================================================================
    // Tab 2 job runner — passes task_filters to server; server fetches pipeline
    // data and restricts visit rows to post-trigger-date per FLW.
    // No browser-side pipeline data needed.
    // =========================================================================
    var runTab2Analysis = React.useCallback(function() {
        if (!dashData || tab2Step === 'running') return;
        if (tab2CleanupRef.current) tab2CleanupRef.current();

        setTab2Step('running');

        // FLWs that have an open task with a recorded trigger date
        var flaggedWithTask = enrichedData.filter(function(f) {
            return f.hasTask && taskStates[f.username] && taskStates[f.username].triggered_at;
        });

        if (flaggedWithTask.length === 0) {
            setTab2Step('idle');
            return;
        }

        var flaggedUsernames = flaggedWithTask.map(function(f) { return f.username; });
        var taskFilters = {};
        flaggedWithTask.forEach(function(f) {
            taskFilters[f.username] = taskStates[f.username].triggered_at;
        });

        actions.startJob(instance.id, {
            job_type: 'mbw_auditing_v4',
            active_usernames: flaggedUsernames,
            task_filters: taskFilters,
            flw_names: flwNameMap,
            flw_statuses: workerResults,
            opportunity_id: instance.opportunity_id,
        }).then(function(resp) {
            if (!resp || !resp.success) {
                setTab2Step('error');
                return;
            }
            var cleanup = actions.streamJobProgress(
                resp.task_id, null, null,
                function(results) {
                    var byUser = {};
                    (results.flw_summaries || []).forEach(function(s) {
                        byUser[s.username] = s;
                    });
                    setTab2Data(byUser);
                    setTab2Step('ready');
                },
                function() { setTab2Step('error'); },
                function() { setTab2Step('error'); }
            );
            tab2CleanupRef.current = cleanup;
        }).catch(function() { setTab2Step('error'); });
    }, [dashData, enrichedData, taskStates, flwNameMap, workerResults, instance.id, instance.opportunity_id, actions, tab2Step]);

    // =========================================================================
    // Flag computation
    // =========================================================================
    var computeFlags = function(flw) {
        var reasons = [];
        var type = null;

        // GS Score: red below 50%
        if (flw.gs_score != null && flw.gs_score < THRESHOLDS.gs_red) {
            reasons.push('GS Score: ' + flw.gs_score + '% (below 50%)');
            type = 'red';
        }
        // Follow-up rate: red below 50%, yellow 50–79%
        if (flw.followup_rate != null && flw.followup_rate < THRESHOLDS.fu_red) {
            reasons.push('Follow-up Rate: ' + flw.followup_rate + '% (below 50%)');
            type = 'red';
        } else if (flw.followup_rate != null && flw.followup_rate < THRESHOLDS.fu_yellow) {
            reasons.push('Follow-up Rate: ' + flw.followup_rate + '% (50–79%)');
            if (!type) type = 'yellow';
        }
        // % Still Eligible: red below 50%, yellow below 85%
        if (flw.pct_still_eligible != null && flw.pct_still_eligible < THRESHOLDS.pct_still_elig_red) {
            reasons.push('% Still Eligible: ' + flw.pct_still_eligible + '% (below 50%)');
            if (type !== 'red') type = 'red';
        } else if (flw.pct_still_eligible != null && flw.pct_still_eligible < THRESHOLDS.pct_still_elig_yellow) {
            reasons.push('% Still Eligible: ' + flw.pct_still_eligible + '% (below 85%)');
            if (!type) type = 'yellow';
        }
        // EBF: yellow if outside green range (31–89%)
        if (flw.ebf_pct != null && (flw.ebf_pct <= THRESHOLDS.ebf_low || flw.ebf_pct > THRESHOLDS.ebf_high)) {
            reasons.push('EBF: ' + flw.ebf_pct + '%');
            if (!type) type = 'yellow';
        }
        // GPS clustering: yellow if dist_ratio < 1.0
        if (flw.dist_ratio != null && flw.dist_ratio < THRESHOLDS.dist_ratio_low) {
            reasons.push('GPS Clustering (Dist Ratio: ' + flw.dist_ratio + ')');
            if (!type) type = 'yellow';
        }
        // Worsened vs last run: yellow if any metric worsened >10%
        var prev = prevMetrics[flw.username];
        if (prev) {
            METRIC_COLS.forEach(function(col) {
                var curr = flw[col.key];
                var prevVal = prev[col.key];
                if (curr == null || prevVal == null || prevVal === 0) return;
                if (col.higherBetter === null) return; // neutral metrics skip
                var lowerIsBetter = !col.higherBetter;
                var worsened = lowerIsBetter ? (curr > prevVal) : (curr < prevVal);
                if (!worsened) return;
                var changePct = Math.abs(curr - prevVal) / Math.abs(prevVal) * 100;
                if (changePct > THRESHOLDS.worsened_pct) {
                    reasons.push(col.label + ' worsened (' + Math.round(changePct) + '%)');
                    if (!type) type = 'yellow';
                }
            });
        }

        return { type: type, reasons: reasons };
    };

    var getChangeDir = function(curr, prev, higherBetter) {
        if (curr == null || prev == null || higherBetter === null) return null;
        var diff = curr - prev;
        var threshold = Math.abs(prev) * 0.02;
        if (Math.abs(diff) <= threshold) return 'same';
        return (higherBetter ? diff > 0 : diff < 0) ? 'up' : 'down';
    };

    var ChangeIcon = function(props) {
        var dir = props.dir;
        if (!dir) return null;
        if (dir === 'up') return React.createElement('span', { className: 'text-green-600 ml-1 text-xs', title: 'Improved since last run' }, '▲');
        if (dir === 'same') return React.createElement('span', { className: 'text-yellow-500 ml-1 text-xs', title: 'No significant change' }, '≈');
        return React.createElement('span', { className: 'text-red-500 ml-1 text-xs', title: 'Worsened since last run' }, '▼');
    };

    // =========================================================================
    // Formatted value + per-metric value colour
    // =========================================================================
    var fmtVal = function(val, fmt) {
        if (val == null) return '—';
        if (fmt === 'pct') return val + '%';
        if (fmt === 'dec') return val.toFixed(1);
        if (fmt === 'int') return Math.round(val).toString();
        return String(val);
    };

    // Returns a Tailwind text colour class for a metric value based on its
    // red/yellow/green thresholds (display colour only — does not affect flags).
    var getMetricValueColor = function(key, val) {
        if (val == null) return '';
        if (key === 'gs_score') {
            return val < 50 ? 'text-red-600' : 'text-green-600';
        }
        if (key === 'followup_rate') {
            if (val < 50) return 'text-red-600';
            if (val < 80) return 'text-yellow-600';
            return 'text-green-600';
        }
        if (key === 'pct_still_eligible') {
            if (val < 50) return 'text-red-600';
            if (val < 85) return 'text-yellow-600';
            return 'text-green-600';
        }
        if (key === 'ebf_pct') {
            if (val < 10 || val >= 99) return 'text-red-600';
            if (val <= 30 || val >= 90) return 'text-yellow-600';
            return 'text-green-600';
        }
        return '';
    };

    // =========================================================================
    // Enriched data
    // =========================================================================
    var enrichedData = React.useMemo(function() {
        if (!dashData) return [];
        return dashData.flw_summaries.map(function(flw) {
            var flags = computeFlags(flw);
            var wr = workerResults[flw.username] || {};
            var ts = taskStates[flw.username] || {};
            return Object.assign({}, flw, {
                flags: flags,
                result: wr.result || null,
                notes: wr.notes || '',
                hasTask: !!(ts.triggered_at),
                taskStatus: ts.status || null,
                taskTriggeredAt: ts.triggered_at || null,
            });
        });
    }, [dashData, workerResults, taskStates]);

    var filteredData = React.useMemo(function() {
        var data = enrichedData;

        if (search.trim()) {
            var q = search.toLowerCase();
            data = data.filter(function(f) {
                return (f.display_name && f.display_name.toLowerCase().indexOf(q) >= 0) ||
                       (f.username && f.username.toLowerCase().indexOf(q) >= 0);
            });
        }

        if (filterFlag === 'red') data = data.filter(function(f) { return f.flags.type === 'red'; });
        else if (filterFlag === 'flagged') data = data.filter(function(f) { return f.flags.type !== null; });
        else if (filterFlag === 'tasks') data = data.filter(function(f) { return f.hasTask; });

        data = data.slice().sort(function(a, b) {
            if (sortCol === 'name') {
                var va = a.display_name || ''; var vb = b.display_name || '';
                return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
            }
            if (sortCol === 'flags') {
                var order = { red: 2, yellow: 1 };
                var va = order[a.flags.type] || 0; var vb = order[b.flags.type] || 0;
                return sortAsc ? va - vb : vb - va;
            }
            var va = a[sortCol] != null ? a[sortCol] : -Infinity;
            var vb = b[sortCol] != null ? b[sortCol] : -Infinity;
            return sortAsc ? va - vb : vb - va;
        });

        return data;
    }, [enrichedData, search, filterFlag, sortCol, sortAsc]);

    var tab2FlaggedRows = React.useMemo(function() {
        return enrichedData.filter(function(f) { return f.flags.type !== null || f.hasTask; });
    }, [enrichedData]);

    // =========================================================================
    // Performance band summary (Tab 3) — computed from current workerResults
    // =========================================================================
    var computePerfBands = function() {
        var bands = [
            { id: 'eligible_for_renewal', label: 'Eligible for Renewal', color: 'green' },
            { id: 'requires_improvement', label: 'Requires Improvement', color: 'yellow' },
            { id: 'suspended', label: 'Suspension', color: 'red' },
            { id: null, label: 'Uncategorized', color: 'gray' },
        ];
        return bands.map(function(band) {
            var catFlws = enrichedData.filter(function(f) { return f.result === band.id; });
            var avgFu = null;
            var fuFlws = catFlws.filter(function(f) { return f.followup_rate != null; });
            if (fuFlws.length > 0) {
                avgFu = Math.round(fuFlws.reduce(function(s, f) { return s + f.followup_rate; }, 0) / fuFlws.length);
            }
            var avgGs = null;
            var gsFlws = catFlws.filter(function(f) { return f.gs_score != null; });
            if (gsFlws.length > 0) {
                avgGs = Math.round(gsFlws.reduce(function(s, f) { return s + f.gs_score; }, 0) / gsFlws.length);
            }
            var totalMothers = catFlws.reduce(function(s, f) { return s + (f.num_mothers || 0); }, 0);
            var totalEligible = catFlws.reduce(function(s, f) { return s + (f.num_mothers_eligible || 0); }, 0);
            return Object.assign({}, band, {
                num_flws: catFlws.length,
                total_mothers: totalMothers,
                total_eligible: totalEligible,
                avg_fu: avgFu,
                avg_gs: avgGs,
            });
        });
    };

    // =========================================================================
    // Handlers
    // =========================================================================
    var handleSort = function(col) {
        if (sortCol === col) setSortAsc(!sortAsc);
        else { setSortCol(col); setSortAsc(col === 'name'); }
    };

    var handleSetCategory = function(username, category) {
        setSavingUser(username);
        var wr = workerResults[username] || {};
        var notes = wr.notes || '';
        actions.saveWorkerResult(instance.id, { username: username, result: category, notes: notes })
            .then(function(resp) {
                if (resp.success) {
                    var updated = Object.assign({}, workerResults);
                    updated[username] = Object.assign({}, wr, { result: category });
                    if (resp.worker_results) {
                        setWorkerResults(resp.worker_results);
                    } else {
                        setWorkerResults(updated);
                    }
                } else {
                    alert('Failed to save: ' + (resp.error || 'unknown error'));
                }
            })
            .catch(function(e) { alert('Error: ' + ((e && e.message) || e)); })
            .finally(function() { setSavingUser(null); });
    };

    var handleOpenNotes = function(flw) {
        setNotesModal(flw.username);
        setNotesDraft(flw.notes || '');
    };

    var handleSaveNotes = function() {
        if (!notesModal) return;
        setSavingNotes(true);
        var username = notesModal;
        var wr = workerResults[username] || {};
        actions.saveWorkerResult(instance.id, { username: username, result: wr.result || null, notes: notesDraft })
            .then(function(resp) {
                if (resp.success) {
                    var updated = Object.assign({}, workerResults);
                    updated[username] = Object.assign({}, wr, { notes: notesDraft });
                    if (resp.worker_results) setWorkerResults(resp.worker_results);
                    else setWorkerResults(updated);
                    setNotesModal(null);
                }
            })
            .catch(function(e) { alert('Error: ' + ((e && e.message) || e)); })
            .finally(function() { setSavingNotes(false); });
    };

    var handleTriggerTask = function(flw) {
        var flagDesc = flw.flags.reasons.join('; ') || 'Performance review required';
        actions.openTaskCreator({
            username: flw.username,
            title: 'MBW Audit: ' + flw.display_name,
            description: flagDesc,
            priority: flw.flags.type === 'red' ? 'high' : 'medium',
            workflow_instance_id: instance.id,
        });
        // Record trigger date in state so Tab 2 can filter post-task data
        var updated = Object.assign({}, taskStates);
        updated[flw.username] = { status: 'open', triggered_at: new Date().toISOString() };
        setTaskStates(updated);
        onUpdateState({ task_states: updated }).catch(function(e) { console.warn('task state save failed:', e); });
    };

    var handleMarkTaskResolved = function(username) {
        var updated = Object.assign({}, taskStates);
        updated[username] = Object.assign({}, updated[username], { status: 'closed' });
        setTaskStates(updated);
        onUpdateState({ task_states: updated }).catch(function(e) { console.warn('task state save failed:', e); });
    };

    var handleConclude = function() {
        if (concluding) return;
        setConcluding(true);
        var currentMetrics = {};
        enrichedData.forEach(function(f) {
            var snap = {};
            METRIC_COLS.forEach(function(col) { snap[col.key] = f[col.key]; });
            currentMetrics[f.username] = snap;
        });
        actions.completeRun(instance.id, { overall_result: 'completed', notes: 'Audit run concluded' })
            .then(function(resp) {
                if (resp.success) {
                    onUpdateState({ previous_metrics: currentMetrics })
                        .catch(function(e) { console.warn('metrics save failed:', e); });
                    setConcludeModal(false);
                } else {
                    alert('Failed to conclude: ' + (resp.error || 'unknown'));
                }
            })
            .catch(function(e) { alert('Error: ' + ((e && e.message) || e)); })
            .finally(function() { setConcluding(false); });
    };

    // Can only conclude when all triggered tasks are resolved
    var canConclude = React.useMemo(function() {
        return Object.values(taskStates).every(function(t) {
            return !t.triggered_at || t.status === 'closed' || t.status === 'completed';
        });
    }, [taskStates]);

    // =========================================================================
    // Sub-components
    // =========================================================================
    var SortTh = function(props) {
        var col = props.col;
        var label = props.label;
        var active = sortCol === col;
        return React.createElement('th', {
            className: 'px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100 whitespace-nowrap select-none',
            onClick: function() { handleSort(col); },
            title: props.title || '',
        }, label, active ? (sortAsc ? ' ▲' : ' ▼') : '');
    };

    var FlagBadge = function(props) {
        var flags = props.flags;
        if (!flags.type) return React.createElement('span', { className: 'text-gray-300 text-xs' }, '—');
        var isRed = flags.type === 'red';
        var tooltip = flags.reasons.join('\n');
        return React.createElement('span', {
            className: 'inline-flex items-center justify-center w-6 h-6 rounded-full text-white text-xs font-bold cursor-help ' +
                (isRed ? 'bg-red-500' : 'bg-yellow-400'),
            title: tooltip,
        }, isRed ? '!' : '?');
    };

    var CategorySelect = function(props) {
        var flw = props.flw;
        var saving = savingUser === flw.username;
        var colorMap = { eligible_for_renewal: 'text-green-700 bg-green-50 border-green-300', requires_improvement: 'text-yellow-700 bg-yellow-50 border-yellow-300', suspended: 'text-red-700 bg-red-50 border-red-300' };
        var cls = 'text-xs border rounded px-1 py-0.5 ' + (colorMap[flw.result] || 'text-gray-600 bg-white border-gray-300');
        if (saving) return React.createElement('span', { className: 'text-xs text-gray-400 italic' }, 'Saving…');
        return React.createElement('select', {
            className: cls,
            value: flw.result || '',
            onChange: function(e) { handleSetCategory(flw.username, e.target.value || null); },
        },
            React.createElement('option', { value: '' }, '— Set category —'),
            PERF_CATEGORIES.map(function(c) {
                return React.createElement('option', { key: c.id, value: c.id }, c.label);
            })
        );
    };

    var TaskCell = function(props) {
        var flw = props.flw;
        if (flw.hasTask) {
            var isClosed = flw.taskStatus === 'closed' || flw.taskStatus === 'completed';
            return React.createElement('div', { className: 'flex flex-col gap-1 items-center' },
                React.createElement('span', {
                    className: 'inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded ' +
                        (isClosed ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'),
                }, React.createElement('i', { className: 'fa-solid ' + (isClosed ? 'fa-circle-check' : 'fa-clock') }),
                flw.taskStatus || 'open'),
                !isClosed && React.createElement('button', {
                    className: 'text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 hover:bg-gray-200 border border-gray-200',
                    onClick: function() { handleMarkTaskResolved(flw.username); },
                    title: 'Mark this task as resolved',
                }, 'Mark Resolved')
            );
        }
        return React.createElement('button', {
            className: 'text-xs px-2 py-0.5 rounded bg-orange-100 text-orange-700 hover:bg-orange-200 border border-orange-200',
            onClick: function() { handleTriggerTask(flw); },
            title: 'Open task creator for this FLW',
        }, React.createElement('i', { className: 'fa-solid fa-plus mr-1' }), 'Trigger Task');
    };

    // =========================================================================
    // Metric table row (used in both Tab 1 and Tab 2)
    // =========================================================================
    var MetricRow = function(props) {
        var flw = props.flw;
        var showChange = props.showChange;
        var prev = props.prevOverride !== undefined ? props.prevOverride : (prevMetrics[flw.username] || null);

        var cells = METRIC_COLS.map(function(col) {
            var val = flw[col.key];
            var dir = (showChange && prev) ? getChangeDir(val, prev[col.key], col.higherBetter) : null;
            var valColor = getMetricValueColor(col.key, val);
            return React.createElement('td', {
                key: col.key,
                className: 'px-3 py-3 text-sm text-center whitespace-nowrap',
            },
                React.createElement('span', { className: valColor || undefined }, fmtVal(val, col.fmt)),
                dir ? React.createElement(ChangeIcon, { dir: dir }) : null
            );
        });

        return React.createElement('tr', {
            key: flw.username,
            className: 'hover:bg-gray-50 ' + (flw.flags.type === 'red' ? 'border-l-4 border-red-400' : flw.flags.type === 'yellow' ? 'border-l-4 border-yellow-400' : ''),
        },
            React.createElement('td', { className: 'px-3 py-3 text-sm' },
                React.createElement('div', { className: 'font-medium text-gray-900' }, flw.display_name),
                React.createElement('div', { className: 'text-xs text-gray-400 font-mono' }, flw.username)
            ),
            React.createElement('td', { className: 'px-3 py-3 text-xs text-gray-500' },
                flw.last_active || '—'
            ),
            React.createElement('td', { className: 'px-3 py-3 text-sm text-center' },
                flw.num_mothers,
                flw.num_mothers_eligible != null ? React.createElement('span', { className: 'text-gray-400 ml-1 text-xs' }, '(' + flw.num_mothers_eligible + ')') : null
            ),
            cells,
            React.createElement('td', { className: 'px-3 py-3 text-center' },
                React.createElement(FlagBadge, { flags: flw.flags })
            ),
            React.createElement('td', { className: 'px-3 py-3 text-center' },
                React.createElement(CategorySelect, { flw: flw })
            ),
            React.createElement('td', { className: 'px-3 py-3 text-center' },
                React.createElement('button', {
                    className: 'text-xs text-gray-500 hover:text-gray-800 px-1',
                    onClick: function() { handleOpenNotes(flw); },
                    title: flw.notes ? 'Notes: ' + flw.notes : 'Add notes',
                }, flw.notes
                    ? React.createElement('i', { className: 'fa-solid fa-note-sticky text-blue-400' })
                    : React.createElement('i', { className: 'fa-regular fa-note-sticky text-gray-300' })
                )
            ),
            React.createElement('td', { className: 'px-3 py-3 text-center' },
                React.createElement(TaskCell, { flw: flw })
            )
        );
    };

    // =========================================================================
    // Table header (shared Tab 1 and Tab 2)
    // =========================================================================
    var TableHeader = function() {
        return React.createElement('thead', { className: 'bg-gray-50 sticky top-0 z-10' },
            React.createElement('tr', null,
                React.createElement(SortTh, { col: 'name', label: 'FLW' }),
                React.createElement('th', { className: 'px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap' }, 'Last Active'),
                React.createElement(SortTh, { col: 'num_mothers', label: '# Mothers', title: 'Total (eligible in brackets)' }),
                METRIC_COLS.map(function(col) {
                    return React.createElement(SortTh, { key: col.key, col: col.key, label: col.label });
                }),
                React.createElement('th', { className: 'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap' }, 'Flag'),
                React.createElement('th', { className: 'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap' }, 'Category'),
                React.createElement('th', { className: 'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase w-10' }, 'Notes'),
                React.createElement('th', { className: 'px-3 py-3 text-center text-xs font-medium text-gray-500 uppercase whitespace-nowrap' }, 'Task')
            )
        );
    };

    // =========================================================================
    // Loading / error screens
    // =========================================================================
    if (step === 'idle' || step === 'running') {
        return React.createElement('div', { className: 'space-y-4' },
            React.createElement('div', { className: 'bg-white rounded-lg shadow-sm p-6' },
                React.createElement('h1', { className: 'text-2xl font-bold text-gray-900' }, definition.name),
                React.createElement('p', { className: 'text-gray-600 mt-1' }, definition.description)
            ),
            React.createElement('div', { className: 'bg-blue-50 border border-blue-200 rounded-lg p-6' },
                React.createElement('div', { className: 'flex items-center gap-3 mb-3' },
                    React.createElement('i', { className: 'fa-solid fa-spinner fa-spin text-blue-600 text-xl' }),
                    React.createElement('span', { className: 'font-medium text-blue-800' },
                        step === 'idle' ? 'Preparing analysis…' : 'Running analysis…'
                    )
                ),
                jobMessages.length > 0 && React.createElement('div', { className: 'text-sm text-blue-700 space-y-0.5' },
                    jobMessages.slice(-5).map(function(m, i) {
                        return React.createElement('div', { key: i }, m);
                    })
                )
            )
        );
    }

    if (step === 'error') {
        return React.createElement('div', { className: 'space-y-4' },
            React.createElement('div', { className: 'bg-white rounded-lg shadow-sm p-6' },
                React.createElement('h1', { className: 'text-2xl font-bold text-gray-900' }, definition.name)
            ),
            React.createElement('div', { className: 'bg-red-50 border border-red-200 rounded-lg p-6' },
                React.createElement('div', { className: 'flex items-center gap-2 text-red-800 font-medium mb-2' },
                    React.createElement('i', { className: 'fa-solid fa-circle-exclamation' }),
                    'Analysis Error'
                ),
                React.createElement('p', { className: 'text-red-700 text-sm' }, jobError || 'An unknown error occurred.'),
                React.createElement('button', {
                    className: 'mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 text-sm font-medium',
                    onClick: function() { setStep('idle'); },
                }, 'Retry')
            )
        );
    }

    // =========================================================================
    // KPI summary bar
    // =========================================================================
    var totalFlws = enrichedData.length;
    var redCount = enrichedData.filter(function(f) { return f.flags.type === 'red'; }).length;
    var yellowCount = enrichedData.filter(function(f) { return f.flags.type === 'yellow'; }).length;
    var taskedCount = enrichedData.filter(function(f) { return f.hasTask; }).length;
    var categorizedCount = enrichedData.filter(function(f) { return f.result; }).length;

    // =========================================================================
    // Tab 1: Per FLW Audit Report
    // =========================================================================
    var Tab1 = function() {
        return React.createElement('div', { className: 'space-y-4' },
            React.createElement('div', { className: 'bg-white rounded-lg shadow-sm p-4' },
                React.createElement('div', { className: 'flex flex-wrap items-center gap-3' },
                    React.createElement('div', { className: 'flex gap-2' },
                        [
                            { id: 'all', label: 'All (' + totalFlws + ')' },
                            { id: 'red', label: 'Red Flags (' + redCount + ')' },
                            { id: 'flagged', label: 'All Flagged (' + (redCount + yellowCount) + ')' },
                            { id: 'tasks', label: 'Has Task (' + taskedCount + ')' },
                        ].map(function(f) {
                            return React.createElement('button', {
                                key: f.id,
                                onClick: function() { setFilterFlag(f.id); },
                                className: 'px-3 py-1.5 text-sm rounded-full border transition-colors ' +
                                    (filterFlag === f.id
                                        ? 'bg-blue-600 text-white border-blue-600'
                                        : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'),
                            }, f.label);
                        })
                    ),
                    React.createElement('input', {
                        type: 'text',
                        placeholder: 'Search FLWs…',
                        value: search,
                        onChange: function(e) { setSearch(e.target.value); },
                        className: 'flex-1 min-w-40 border border-gray-300 rounded-lg px-3 py-1.5 text-sm',
                    }),
                    React.createElement('button', {
                        className: 'ml-auto px-3 py-1.5 text-sm rounded border bg-white text-gray-700 border-gray-300 hover:bg-gray-50',
                        onClick: runAnalysis,
                        title: 'Re-run analysis with latest data',
                    }, React.createElement('i', { className: 'fa-solid fa-rotate-right mr-1' }), 'Refresh Data')
                )
            ),
            React.createElement('div', { className: 'bg-white rounded-lg shadow-sm overflow-x-auto' },
                React.createElement('table', { className: 'min-w-full divide-y divide-gray-200' },
                    React.createElement(TableHeader, null),
                    React.createElement('tbody', { className: 'bg-white divide-y divide-gray-200' },
                        filteredData.map(function(flw) {
                            return React.createElement(MetricRow, { key: flw.username, flw: flw, showChange: true });
                        }),
                        filteredData.length === 0 && React.createElement('tr', null,
                            React.createElement('td', { colSpan: 14, className: 'px-4 py-8 text-center text-gray-400' }, 'No FLWs match current filters.')
                        )
                    )
                )
            ),
            prevMetrics && Object.keys(prevMetrics).length > 0 && React.createElement('p', { className: 'text-xs text-gray-400 px-1' },
                '▲▼ arrows show change vs. previous concluded run. ≈ = no significant change.'
            )
        );
    };

    // =========================================================================
    // Tab 2: Improvement Within Audit
    // =========================================================================
    var Tab2 = function() {
        if (tab2FlaggedRows.length === 0) {
            return React.createElement('div', { className: 'bg-white rounded-lg shadow-sm p-8 text-center' },
                React.createElement('i', { className: 'fa-solid fa-check-circle text-green-400 text-3xl mb-3' }),
                React.createElement('p', { className: 'text-gray-600' }, 'No flagged FLWs or open tasks in this run.')
            );
        }

        // Determine which FLWs have post-task data available
        var taskedWithDate = tab2FlaggedRows.filter(function(f) {
            return f.hasTask && taskStates[f.username] && taskStates[f.username].triggered_at;
        });

        return React.createElement('div', { className: 'space-y-4' },
            // Info + compute button
            React.createElement('div', { className: 'bg-blue-50 border border-blue-200 rounded-lg p-3 flex items-start justify-between gap-3' },
                React.createElement('div', { className: 'text-sm text-blue-700' },
                    React.createElement('i', { className: 'fa-solid fa-circle-info mr-1' }),
                    tab2Step === 'ready'
                        ? 'Post-task metrics: only data submitted after each FLW\'s task was triggered. Change arrows compare post-task vs. current run values.'
                        : 'Showing FLWs with red/yellow flags or open tasks. Click "Compute Post-Task Metrics" to load data submitted after each task was triggered.'
                ),
                taskedWithDate.length > 0 && React.createElement('button', {
                    className: 'shrink-0 px-3 py-1.5 text-sm rounded border font-medium transition-colors ' +
                        (tab2Step === 'running'
                            ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                            : 'bg-blue-600 text-white border-blue-600 hover:bg-blue-700'),
                    onClick: runTab2Analysis,
                    disabled: tab2Step === 'running',
                    title: 'Re-run analysis using only data submitted after each task was triggered',
                },
                    tab2Step === 'running'
                        ? React.createElement('span', null, React.createElement('i', { className: 'fa-solid fa-spinner fa-spin mr-1' }), 'Computing…')
                        : React.createElement('span', null, React.createElement('i', { className: 'fa-solid fa-rotate-right mr-1' }), 'Compute Post-Task Metrics')
                )
            ),

            // Table: post-task data if ready, otherwise current-run data
            React.createElement('div', { className: 'bg-white rounded-lg shadow-sm overflow-x-auto' },
                React.createElement('table', { className: 'min-w-full divide-y divide-gray-200' },
                    React.createElement(TableHeader, null),
                    React.createElement('tbody', { className: 'bg-white divide-y divide-gray-200' },
                        tab2FlaggedRows.map(function(flw) {
                            // If post-task data is ready, merge it into the row for display
                            var postTask = tab2Data && tab2Data[flw.username];
                            var displayFlw = postTask
                                ? Object.assign({}, flw, {
                                    gs_score: postTask.gs_score,
                                    followup_rate: postTask.followup_rate,
                                    pct_still_eligible: postTask.pct_still_eligible,
                                    ebf_pct: postTask.ebf_pct,
                                    revisit_dist: postTask.revisit_dist,
                                    meter_per_visit: postTask.meter_per_visit,
                                    dist_ratio: postTask.dist_ratio,
                                    minute_per_visit: postTask.minute_per_visit,
                                })
                                : flw;
                            // Change arrows for Tab 2 compare post-task to current full-run values
                            var tab2PrevOverride = postTask ? {} : null;
                            if (postTask) {
                                METRIC_COLS.forEach(function(col) {
                                    tab2PrevOverride[col.key] = flw[col.key];
                                });
                            }
                            return React.createElement(MetricRow, {
                                key: flw.username,
                                flw: displayFlw,
                                showChange: !!(postTask),
                                prevOverride: postTask ? tab2PrevOverride : null,
                            });
                        })
                    )
                )
            )
        );
    };

    // =========================================================================
    // Tab 3: Summary by Performance Band
    // =========================================================================
    var Tab3 = function() {
        var bands = perfData || computePerfBands();

        var bandColor = { green: 'border-green-400 bg-green-50', yellow: 'border-yellow-400 bg-yellow-50', red: 'border-red-400 bg-red-50', gray: 'border-gray-300 bg-gray-50' };

        return React.createElement('div', { className: 'space-y-4' },
            React.createElement('div', { className: 'flex items-center justify-between' },
                React.createElement('p', { className: 'text-sm text-gray-500' },
                    'Based on latest performance categories set for each FLW, including current run.'
                ),
                React.createElement('button', {
                    className: 'px-3 py-1.5 text-sm rounded border bg-white text-gray-700 border-gray-300 hover:bg-gray-50',
                    onClick: function() { setPerfData(computePerfBands()); },
                }, React.createElement('i', { className: 'fa-solid fa-rotate-right mr-1' }), 'Refresh Summary')
            ),

            React.createElement('div', { className: 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4' },
                bands.map(function(band) {
                    return React.createElement('div', {
                        key: band.id || 'none',
                        className: 'bg-white rounded-lg shadow-sm p-5 border-l-4 ' + (bandColor[band.color] || bandColor.gray),
                    },
                        React.createElement('div', { className: 'text-lg font-bold text-gray-900' }, band.label),
                        React.createElement('div', { className: 'text-3xl font-bold mt-1' }, band.num_flws),
                        React.createElement('div', { className: 'text-sm text-gray-500 mt-1' }, 'FLWs'),
                        React.createElement('div', { className: 'mt-3 space-y-1 text-sm text-gray-600' },
                            React.createElement('div', null, '# Mothers: ', React.createElement('strong', null, band.total_mothers)),
                            React.createElement('div', null, 'Eligible Mothers: ', React.createElement('strong', null, band.total_eligible)),
                            band.avg_fu != null && React.createElement('div', null, 'Avg Follow-up: ', React.createElement('strong', null, band.avg_fu + '%')),
                            band.avg_gs != null && React.createElement('div', null, 'Avg GS Score: ', React.createElement('strong', null, band.avg_gs + '%'))
                        )
                    );
                })
            ),

            React.createElement('div', { className: 'bg-white rounded-lg shadow-sm overflow-hidden' },
                React.createElement('div', { className: 'px-4 py-3 border-b border-gray-200' },
                    React.createElement('h3', { className: 'font-semibold text-gray-900' }, 'FLW Performance by Assessment Status')
                ),
                React.createElement('div', { className: 'overflow-x-auto' },
                    React.createElement('table', { className: 'min-w-full divide-y divide-gray-200' },
                        React.createElement('thead', { className: 'bg-gray-50' },
                            React.createElement('tr', null,
                                React.createElement('th', { className: 'px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase' }, 'Status'),
                                React.createElement('th', { className: 'px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase' }, 'FLWs'),
                                React.createElement('th', { className: 'px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase' }, 'Mothers'),
                                React.createElement('th', { className: 'px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase' }, 'Eligible'),
                                React.createElement('th', { className: 'px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase' }, 'Avg Follow-up'),
                                React.createElement('th', { className: 'px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase' }, 'Avg GS Score')
                            )
                        ),
                        React.createElement('tbody', { className: 'bg-white divide-y divide-gray-200' },
                            bands.map(function(band) {
                                return React.createElement('tr', { key: band.id || 'none', className: 'hover:bg-gray-50' },
                                    React.createElement('td', { className: 'px-4 py-3 font-medium text-sm text-gray-900' }, band.label),
                                    React.createElement('td', { className: 'px-4 py-3 text-center text-sm' }, band.num_flws),
                                    React.createElement('td', { className: 'px-4 py-3 text-center text-sm' }, band.total_mothers),
                                    React.createElement('td', { className: 'px-4 py-3 text-center text-sm' }, band.total_eligible),
                                    React.createElement('td', { className: 'px-4 py-3 text-center text-sm' }, band.avg_fu != null ? band.avg_fu + '%' : '—'),
                                    React.createElement('td', { className: 'px-4 py-3 text-center text-sm' }, band.avg_gs != null ? band.avg_gs + '%' : '—')
                                );
                            })
                        )
                    )
                )
            )
        );
    };

    // =========================================================================
    // Tab 4: Guide
    // =========================================================================
    var Tab4 = function() {
        var sections = [
            {
                title: 'Workflow Overview',
                body: 'Every two weeks, the PM triggers a new audit run. The dashboard loads data for all active FLWs (~98). The PM reviews flags, triggers OCS Audit Bot tasks for red-flagged FLWs (mandatory) and yellow-flagged FLWs (optional with a note), monitors improvement over 7 days, then sets final performance categories and concludes the run.'
            },
            {
                title: 'Flag Types',
                body: '🔴 Red flag = task required. Triggered by: Follow-up Rate below 50%, % Still Eligible below 50%, or GS Score below 50%.\n🟡 Yellow flag = task optional (note required if skipped). Triggered by: Follow-up Rate 50–79%, % Still Eligible below 85%, EBF% ≤30% or >95%, GPS Dist Ratio < 1.0, or any metric worsening >10% since the last concluded run.'
            },
            {
                title: 'Metric Definitions',
                items: [
                    { name: 'GS Score', def: 'Gold Standard visit checklist score. Based on highest value recorded for this FLW. Red flag if below 50%.' },
                    { name: 'Follow-up Rate', def: 'Of visits due more than 5 days ago (among mothers with eligible_full_intervention_bonus=1), % completed. Red if below 50%, yellow if 50–79%, green if 80%+.' },
                    { name: '% Still Eligible', def: 'Of mothers with eligible_full_intervention_bonus=1 AND anc_completion_date set, % who have missed fewer than 2 visits. Green ≥85%, yellow 50–84%, red <50%.' },
                    { name: 'EBF %', def: 'Percentage of visits where current breastfeeding status is exclusive. Yellow flag if ≤30% or >95%.' },
                    { name: 'Revisit Dist (km)', def: 'Average GPS distance between visits to the same mother case.' },
                    { name: 'Meter/Visit', def: 'Median GPS distance traveled per visit (meters).' },
                    { name: 'Dist Ratio', def: 'Revisit distance (m) ÷ Meter/Visit. Values below 1.0 suggest GPS clustering (yellow flag).' },
                    { name: 'Minute/Visit', def: 'Median visit duration in minutes.' },
                ]
            },
            {
                title: 'Performance Categories',
                items: [
                    { name: 'Eligible for Renewal', def: 'FLW met performance standards and is eligible for program renewal.' },
                    { name: 'Requires Improvement', def: 'FLW showed improvement but needs continued monitoring before renewal.' },
                    { name: 'Suspension', def: 'FLW did not improve sufficiently and is recommended for suspension.' },
                ]
            },
            {
                title: 'Concluding a Run',
                body: 'The run can only be concluded once all open tasks are closed. When concluded, the current metrics are saved as the baseline for change indicators in the next run. Use Tab 3 to review the performance band breakdown before concluding.'
            },
        ];

        return React.createElement('div', { className: 'space-y-6 max-w-3xl' },
            sections.map(function(s, i) {
                return React.createElement('div', { key: i, className: 'bg-white rounded-lg shadow-sm p-6' },
                    React.createElement('h3', { className: 'text-lg font-semibold text-gray-900 mb-3' }, s.title),
                    s.body && React.createElement('p', { className: 'text-sm text-gray-700 whitespace-pre-line' }, s.body),
                    s.items && React.createElement('dl', { className: 'space-y-2' },
                        s.items.map(function(item, j) {
                            return React.createElement('div', { key: j },
                                React.createElement('dt', { className: 'text-sm font-medium text-gray-900' }, item.name),
                                React.createElement('dd', { className: 'text-sm text-gray-600 ml-4' }, item.def)
                            );
                        })
                    )
                );
            })
        );
    };

    // =========================================================================
    // Main render
    // =========================================================================
    var tabs = [
        { id: 'audit',       label: 'Audit Report',         icon: 'fa-table' },
        { id: 'improvement', label: 'Improvement in Audit', icon: 'fa-chart-line' },
        { id: 'summary',     label: 'Summary by Band',      icon: 'fa-layer-group' },
        { id: 'guide',       label: 'Guide',                 icon: 'fa-book' },
    ];

    return React.createElement('div', { className: 'space-y-4 pb-8' },

        // Header
        React.createElement('div', { className: 'bg-white rounded-lg shadow-sm p-6 flex items-start justify-between' },
            React.createElement('div', null,
                React.createElement('h1', { className: 'text-2xl font-bold text-gray-900' }, definition.name),
                React.createElement('p', { className: 'text-gray-600 mt-1 text-sm' }, definition.description)
            ),
            React.createElement('button', {
                className: 'px-4 py-2 text-sm rounded-lg font-medium transition-colors ' +
                    (!canConclude ? 'bg-gray-200 text-gray-400 cursor-not-allowed' : 'bg-green-600 text-white hover:bg-green-700'),
                onClick: function() { if (canConclude) setConcludeModal(true); },
                disabled: !canConclude,
                title: canConclude ? 'Conclude this audit run' : 'Close all tasks before concluding',
            }, React.createElement('i', { className: 'fa-solid fa-flag-checkered mr-2' }), 'Conclude Run')
        ),

        // KPI bar
        React.createElement('div', { className: 'grid grid-cols-2 sm:grid-cols-5 gap-3' },
            [
                { label: 'Total FLWs', value: totalFlws, color: 'border-blue-400' },
                { label: 'Red Flags', value: redCount, color: 'border-red-400' },
                { label: 'Yellow Flags', value: yellowCount, color: 'border-yellow-400' },
                { label: 'Tasks Open', value: taskedCount, color: 'border-orange-400' },
                { label: 'Categorized', value: categorizedCount + ' / ' + totalFlws, color: 'border-green-400' },
            ].map(function(kpi, i) {
                return React.createElement('div', {
                    key: i,
                    className: 'bg-white rounded-lg shadow-sm p-4 border-l-4 ' + kpi.color,
                },
                    React.createElement('div', { className: 'text-2xl font-bold text-gray-900' }, kpi.value),
                    React.createElement('div', { className: 'text-xs text-gray-500 mt-0.5' }, kpi.label)
                );
            })
        ),

        // Tab bar
        React.createElement('div', { className: 'bg-white rounded-lg shadow-sm' },
            React.createElement('div', { className: 'flex border-b border-gray-200 overflow-x-auto' },
                tabs.map(function(tab) {
                    return React.createElement('button', {
                        key: tab.id,
                        onClick: function() { setActiveTab(tab.id); },
                        className: 'flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ' +
                            (activeTab === tab.id
                                ? 'border-blue-600 text-blue-600'
                                : 'border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-300'),
                    },
                        React.createElement('i', { className: 'fa-solid ' + tab.icon }),
                        tab.label
                    );
                })
            ),
            React.createElement('div', { className: 'p-4' },
                activeTab === 'audit'       ? React.createElement(Tab1, null) :
                activeTab === 'improvement' ? React.createElement(Tab2, null) :
                activeTab === 'summary'     ? React.createElement(Tab3, null) :
                                              React.createElement(Tab4, null)
            )
        ),

        // Notes modal
        notesModal && React.createElement('div', {
            className: 'fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-40',
            onClick: function(e) { if (e.target === e.currentTarget) setNotesModal(null); },
        },
            React.createElement('div', { className: 'bg-white rounded-xl shadow-2xl w-full max-w-md mx-4' },
                React.createElement('div', { className: 'px-6 py-4 border-b border-gray-200 font-semibold text-gray-900' },
                    'Notes for ' + notesModal
                ),
                React.createElement('div', { className: 'px-6 py-4' },
                    React.createElement('textarea', {
                        className: 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm',
                        rows: 4,
                        value: notesDraft,
                        onChange: function(e) { setNotesDraft(e.target.value); },
                        placeholder: 'Add notes about this FLW…',
                    })
                ),
                React.createElement('div', { className: 'px-6 py-4 border-t border-gray-200 flex justify-end gap-3' },
                    React.createElement('button', {
                        className: 'px-4 py-2 text-sm border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50',
                        onClick: function() { setNotesModal(null); },
                    }, 'Cancel'),
                    React.createElement('button', {
                        className: 'px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400',
                        onClick: handleSaveNotes,
                        disabled: savingNotes,
                    }, savingNotes ? 'Saving…' : 'Save Notes')
                )
            )
        ),

        // Conclude run modal
        concludeModal && React.createElement('div', {
            className: 'fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-40',
            onClick: function(e) { if (e.target === e.currentTarget) setConcludeModal(false); },
        },
            React.createElement('div', { className: 'bg-white rounded-xl shadow-2xl w-full max-w-md mx-4' },
                React.createElement('div', { className: 'px-6 py-4 bg-green-50 border-b border-green-200' },
                    React.createElement('h3', { className: 'text-lg font-semibold text-green-900' },
                        React.createElement('i', { className: 'fa-solid fa-flag-checkered mr-2' }),
                        'Conclude Audit Run'
                    )
                ),
                React.createElement('div', { className: 'px-6 py-5 text-sm text-gray-700 space-y-3' },
                    React.createElement('p', null,
                        'This will mark the run as ', React.createElement('strong', null, 'Completed'), ' and save current metrics as the baseline for change indicators in the next run.'
                    ),
                    React.createElement('p', null,
                        categorizedCount + ' of ' + totalFlws + ' FLWs have been categorized.'
                    ),
                    React.createElement('p', { className: 'text-orange-700 font-medium' },
                        'This action cannot be undone.'
                    )
                ),
                React.createElement('div', { className: 'px-6 py-4 border-t border-gray-200 flex justify-end gap-3' },
                    React.createElement('button', {
                        className: 'px-4 py-2 text-sm border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50',
                        onClick: function() { setConcludeModal(false); },
                    }, 'Cancel'),
                    React.createElement('button', {
                        className: 'px-5 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:bg-gray-400 font-medium',
                        onClick: handleConclude,
                        disabled: concluding,
                    }, concluding ? 'Concluding…' : 'Conclude Run')
                )
            )
        )
    );
}
